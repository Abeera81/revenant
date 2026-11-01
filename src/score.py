"""Lockfile -> features as of now -> TabPFN -> abstain or score.

  python src/score.py path/to/package-lock.json [--refresh]

Registry documents for scoring go to data/live_cache.db (same schema and
fetcher as the collector) so they never mix with the backtest sample.
TabPFN is fitted on the full labelled table (data/table.parquet).
"""
import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402
from features import FEATURES, TABLE_PATH, before, compute_features, parse_time_map  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "live_cache.db"
BACKTEST_JSON = ROOT / "results" / "backtest.json"
SEED = 20261031
MIN_RELEASES = 5
MIN_AGE_DAYS = 365


def lockfile_names(path: Path) -> list[str]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    names = set()
    for key, meta in (lock.get("packages") or {}).items():
        if not key or meta.get("link"):
            continue
        names.add(meta.get("name") or key.rsplit("node_modules/", 1)[-1])
    def walk(deps):  # lockfileVersion 1
        for n, meta in (deps or {}).items():
            names.add(n)
            walk(meta.get("dependencies"))
    walk(lock.get("dependencies"))
    return sorted(names)


def fetch(names: list[str], refresh: bool) -> sqlite3.Connection:
    collect.DB_PATH = LIVE_DB
    db = collect.init_db()
    done = set() if refresh else {r[0] for r in db.execute(
        "SELECT name FROM packages WHERE status IN ('ok','not_found')")}
    todo = [n for n in names if n not in done]
    if todo:
        print(f"fetching {len(todo)} registry documents ({len(names) - len(todo)} cached)", file=sys.stderr)
        asyncio.run(collect.run(todo, 16, db, None))
    return db


def abstain_reason(f: dict) -> str | None:
    reasons = []
    if f["total_versions"] < MIN_RELEASES:
        reasons.append(f"only {f['total_versions']} lifetime releases (needs {MIN_RELEASES})")
    if f["package_age_days"] < MIN_AGE_DAYS:
        reasons.append(f"only {f['package_age_days']:.0f} days old (needs {MIN_AGE_DAYS})")
    return "; ".join(reasons) or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lockfile", type=Path)
    ap.add_argument("--refresh", action="store_true", help="refetch registry documents")
    args = ap.parse_args()

    names = lockfile_names(args.lockfile)
    db = fetch(names, args.refresh)
    asof = datetime.now(timezone.utc)
    rows, abstained, unavailable = [], [], []
    for n in names:
        r = db.execute("SELECT status, time_json, is_scoped FROM packages WHERE name=?", (n,)).fetchone()
        if r is None or r[0] != "ok":
            unavailable.append((n, "registry document not found" if r and r[0] == "not_found" else "fetch failed"))
            continue
        pre = before(parse_time_map(json.loads(r[1])), asof)
        if not pre:
            abstained.append((n, "no published versions"))
            continue
        f = compute_features(pre, bool(r[2]), asof)
        reason = abstain_reason(f)
        if reason:
            abstained.append((n, reason))
        else:
            rows.append({"name": n, **f})

    table = pd.read_parquet(TABLE_PATH)
    base_rate = table["dormant"].mean()
    brier = "TBD (run src/backtest.py --write)"
    if BACKTEST_JSON.exists():
        brier = f"{json.loads(BACKTEST_JSON.read_text())['full']['tabpfn']['brier']:.4f}"

    print(f"Revenant: probability each dependency publishes no new version in the next 12 months")
    print(f"as of {asof:%Y-%m-%d %H:%M} UTC; lockfile {args.lockfile.name}; {len(names)} unique packages")
    print(f"Base rate in the backtest frame: {base_rate:.4f}   TabPFN held-out Brier score: {brier}")
    print("Backtest frame = random npm packages, 2024 cutoff; lockfile dependencies are a different population.\n")

    if rows:
        from tabpfn import TabPFNClassifier
        live = pd.DataFrame(rows)
        X = live[FEATURES].to_numpy(float)
        model = TabPFNClassifier(random_state=SEED, device="cpu")
        model.fit(table[FEATURES].to_numpy(float), table["dormant"].to_numpy(int))
        # Attribution by occlusion: each feature in turn set to its training median,
        # all variants scored in one batch; largest |p - p_occluded| is reported.
        med = table[FEATURES].median().to_numpy(float)
        variants = [X]
        for j in range(len(FEATURES)):
            Xj = X.copy()
            Xj[:, j] = med[j]
            variants.append(Xj)
        P = model.predict_proba(np.vstack(variants))[:, 1].reshape(len(variants), len(X))
        p, deltas = P[0], P[0] - P[1:]
        live["p"] = p
        top = np.abs(deltas).argmax(axis=0)
        live["driver"] = [
            f"{FEATURES[j]}={live.iloc[i][FEATURES[j]]:.4g} ({'+' if deltas[j, i] >= 0 else ''}{deltas[j, i]:.3f})"
            for i, j in enumerate(top)]
        live = live.sort_values("p", ascending=False)
        w = max(len(n) for n in live["name"])
        print(f"SCORED ({len(live)})")
        print(f"{'package':<{w}}  {'p(dormant)':>10}  largest-contributing feature (value, effect vs training median)")
        for _, r in live.iterrows():
            print(f"{r['name']:<{w}}  {r['p']:>10.3f}  {r['driver']}")
    else:
        print("SCORED (0)")

    print(f"\nABSTAINED ({len(abstained)}) - pre-registered rule: fewer than {MIN_RELEASES} lifetime releases or under 1 year old")
    for n, why in abstained:
        print(f"  {n}: {why}")
    print(f"\nNOT SCORED, REGISTRY UNAVAILABLE ({len(unavailable)})")
    for n, why in unavailable:
        print(f"  {n}: {why}")


if __name__ == "__main__":
    main()
