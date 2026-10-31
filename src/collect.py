"""Registry fetch -> SQLite cache.

Enumerates every package name from the npm replication endpoint (unranked, so
the sample is not selected on today's popularity), draws a seeded random
sample, and caches only the `time` map, version count and scoped flag of each
registry document. No README or description text is stored.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np

SEED = 20261031
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "cache.db"
NAMES_PATH = ROOT / "data" / "names.txt"
LOG_PATH = ROOT / "results" / "collect.log"
REPLICATE = "https://replicate.npmjs.com/_all_docs"
REGISTRY = "https://registry.npmjs.org/"
PAGE = 10000
MAX_ATTEMPTS = 6
DONE_STATUSES = ("ok", "not_found")
UA = "revenant-backtest/0.1 (research; https://github.com/Abeera81/revenant)"

log = logging.getLogger("collect")
STATS = {"http_429": 0, "http_5xx": 0, "transport_errors": 0, "retry_waits": 0}


def setup_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s")
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def enumerate_names() -> list[str]:
    """Return all package names, cached in data/names.txt after one full pass."""
    if NAMES_PATH.exists():
        return NAMES_PATH.read_text(encoding="utf-8").splitlines()
    NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("enumerating names from %s (startkey pagination, limit=%d)", REPLICATE, PAGE)
    names: list[str] = []
    startkey = None
    total_rows = None
    with httpx.Client(timeout=120, headers={"User-Agent": UA}) as client:
        while True:
            params = {"limit": PAGE}
            if startkey is not None:
                params["startkey"] = json.dumps(startkey)
            for attempt in range(MAX_ATTEMPTS):
                try:
                    r = client.get(REPLICATE, params=params)
                    if r.status_code == 200:
                        break
                    log.info("enumerate HTTP %s, retrying", r.status_code)
                except httpx.HTTPError as e:
                    log.info("enumerate error %r, retrying", e)
                time.sleep(min(60, 2 ** attempt))
            else:
                raise RuntimeError("replication endpoint unavailable; aborting")
            body = r.json()
            total_rows = body.get("total_rows")
            rows = body["rows"]
            last_page = len(rows) < PAGE
            names.extend(row["id"] for row in rows)  # startkey row repeats; deduped by set() below
            if last_page:
                break
            startkey = rows[-1]["id"]
            if (len(names) // PAGE) % 50 == 0:
                log.info("enumerated %d / ~%s", len(names), total_rows)
    names = sorted(set(names))
    tmp = NAMES_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(names) + "\n", encoding="utf-8")
    os.replace(tmp, NAMES_PATH)
    log.info("enumerated %d names (registry total_rows=%s)", len(names), total_rows)
    return names


def draw_sample(names: list[str], n: int) -> list[str]:
    """Seeded permutation prefix: a larger n extends a smaller n's sample."""
    perm = np.random.default_rng(SEED).permutation(len(names))
    return [names[i] for i in perm[:n]]


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS packages (
            name TEXT PRIMARY KEY,
            status TEXT NOT NULL,          -- ok | not_found | error
            http_status INTEGER,
            time_json TEXT,                -- full registry `time` map
            total_versions INTEGER,
            is_scoped INTEGER NOT NULL,
            fetched_at TEXT NOT NULL
        )"""
    )
    db.commit()
    return db


async def fetch_one(client: httpx.AsyncClient, name: str) -> dict:
    url = REGISTRY + quote(name, safe="@")
    row = {"name": name, "status": "error", "http_status": None, "time_json": None,
           "total_versions": None, "is_scoped": int(name.startswith("@"))}
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            STATS["transport_errors"] += 1
            STATS["retry_waits"] += 1
            wait = min(60, 2 ** attempt) + random.random()
            log.info("retry %s: %s (attempt %d, wait %.1fs)", name, type(e).__name__, attempt + 1, wait)
            await asyncio.sleep(wait)
            continue
        row["http_status"] = r.status_code
        if r.status_code == 200:
            try:
                doc = r.json()
                row.update(status="ok", time_json=json.dumps(doc.get("time") or {}),
                           total_versions=len(doc.get("versions") or {}))
            except ValueError:
                pass  # stays "error"
            return row
        if r.status_code == 404:
            row["status"] = "not_found"
            return row
        if r.status_code == 429 or r.status_code >= 500:
            STATS["http_429" if r.status_code == 429 else "http_5xx"] += 1
            STATS["retry_waits"] += 1
            ra = r.headers.get("retry-after")
            wait = (float(ra) if ra and ra.isdigit() else min(60, 2 ** attempt)) + random.random()
            log.info("retry %s: HTTP %s (attempt %d, wait %.1fs)", name, r.status_code, attempt + 1, wait)
            await asyncio.sleep(wait)
            continue
        return row  # other 4xx: recorded as error, retried on a later run
    return row


async def run(todo: list[str], concurrency: int, db: sqlite3.Connection, deadline: float | None) -> dict:
    counts = {"ok": 0, "not_found": 0, "error": 0}
    queue: asyncio.Queue = asyncio.Queue()
    for n in todo:
        queue.put_nowait(n)
    results: asyncio.Queue = asyncio.Queue()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(90, connect=20)

    async def worker(client):
        # Names are taken in permutation order, so the cached set stays a prefix
        # (minus at most `concurrency` in-flight names) when the deadline hits.
        while deadline is None or time.monotonic() < deadline:
            try:
                name = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await results.put(await fetch_one(client, name))

    async with httpx.AsyncClient(limits=limits, timeout=timeout, headers={"User-Agent": UA}) as client:
        pool = asyncio.gather(*[asyncio.create_task(worker(client)) for _ in range(concurrency)])
        i = 0
        while not (pool.done() and results.empty()):
            try:
                row = await asyncio.wait_for(results.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            i += 1
            counts[row["status"]] += 1
            db.execute(
                "INSERT OR REPLACE INTO packages VALUES (?,?,?,?,?,?,?)",
                (row["name"], row["status"], row["http_status"], row["time_json"],
                 row["total_versions"], row["is_scoped"],
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            if i % 100 == 0:
                db.commit()
            if i % 1000 == 0:
                log.info("progress %d/%d  ok=%d not_found=%d error=%d 429=%d 5xx=%d",
                         i, len(todo), counts["ok"], counts["not_found"], counts["error"],
                         STATS["http_429"], STATS["http_5xx"])
        await pool
    db.commit()
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True, help="sample size (prefix of the seeded permutation)")
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--max-minutes", type=float, default=None, help="stop taking new names after this wall-clock budget")
    args = ap.parse_args()
    setup_logging()
    log.info("start: seed=%d n=%d concurrency=%d max_minutes=%s", SEED, args.n, args.concurrency, args.max_minutes)
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None

    names = enumerate_names()
    sha = hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]
    log.info("name list: %d names, sha256[:16]=%s", len(names), sha)
    sample = draw_sample(names, args.n)
    db = init_db()
    done = {r[0] for r in db.execute(
        f"SELECT name FROM packages WHERE status IN ({','.join('?' * len(DONE_STATUSES))})", DONE_STATUSES)}
    todo = [n for n in sample if n not in done]
    log.info("sampled=%d already_cached=%d to_fetch=%d", len(sample), len(sample) - len(todo), len(todo))

    counts = asyncio.run(run(todo, args.concurrency, db, deadline))
    log.info("retry stats: %s", STATS)

    sample_status = {"ok": 0, "not_found": 0, "error": 0}
    for i in range(0, len(sample), 500):
        chunk = sample[i:i + 500]
        q = f"SELECT status, COUNT(*) FROM packages WHERE name IN ({','.join('?' * len(chunk))}) GROUP BY status"
        for s, c in db.execute(q, chunk):
            sample_status[s] += c
    total_rows = db.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    ok_rows = db.execute("SELECT COUNT(*) FROM packages WHERE status='ok'").fetchone()[0]
    log.info("this run fetched: %s", counts)
    log.info("sample state: sampled=%d ok=%d not_found=%d error=%d unfetched=%d",
             len(sample), sample_status["ok"], sample_status["not_found"], sample_status["error"],
             len(sample) - sum(sample_status.values()))
    log.info("cache rows: total=%d ok=%d", total_rows, ok_rows)


if __name__ == "__main__":
    main()
