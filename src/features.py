"""Cutoff-respecting feature table.

Every feature is computed from version publish timestamps strictly before an
`asof` instant plus the package name. `compute_features` refuses (raises) if it
is handed any timestamp at or after `asof`, so leakage fails loudly rather than
silently.

Implementation readings of the pre-registered feature list (fixed before any
model was fitted; see MEMORY.md):
- a "release" is a version key in the registry `time` map (not `created`/`modified`)
- gaps are in days between consecutive releases, sorted by timestamp
- median_gap_last_5: median of the 4 gaps between the last 5 releases
- gap_trend: (median of last 3 gaps + 1) / (median of up to 3 gaps before those + 1);
  the +1 day keeps same-instant publishes from dividing by zero
- releases_12m / releases_24m: releases in [asof - 1 calendar year, asof) / 2 years
- major_version_count: distinct leading integers of the version strings
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "cache.db"
TABLE_PATH = ROOT / "data" / "table.parquet"

CUTOFF = datetime(2024, 1, 1, tzinfo=timezone.utc)
FRAME_FIRST_PUBLISH_DATE = datetime(2022, 1, 1).date()  # calendar-date reading, see MEMORY.md

FEATURES = [
    "days_since_last_release", "releases_12m", "releases_24m", "median_gap_last_5",
    "gap_trend", "total_versions", "package_age_days", "major_version_count",
    "releases_per_year_lifetime", "longest_prior_gap_days", "is_scoped",
]

_MAJOR = re.compile(r"^\s*v?(\d+)")
DAY = 86400.0


def parse_time_map(time_map: dict) -> list[tuple[str, datetime]]:
    """(version, timestamp) for every version entry; non-string or unparsable values are skipped."""
    out = []
    for k, v in time_map.items():
        if k in ("created", "modified") or not isinstance(v, str):
            continue
        try:
            ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append((k, ts))
    return out


def before(releases: list[tuple[str, datetime]], asof: datetime) -> list[tuple[str, datetime]]:
    return [(v, t) for v, t in releases if t < asof]


def _years_back(asof: datetime, n: int) -> datetime:
    return asof.replace(year=asof.year - n)


def compute_features(releases: list[tuple[str, datetime]], is_scoped: bool, asof: datetime) -> dict:
    """Features as of `asof`. Every timestamp passed in must be strictly before `asof`."""
    for v, t in releases:
        if t >= asof:
            raise AssertionError(f"leakage: release {v!r} at {t.isoformat()} is not before asof {asof.isoformat()}")
    if not releases:
        raise ValueError("no releases before asof")
    releases = sorted(releases, key=lambda r: r[1])
    ts = np.array([t.timestamp() for _, t in releases])
    gaps = np.diff(ts) / DAY
    first, last = ts[0], ts[-1]
    now = asof.timestamp()
    age = (now - first) / DAY

    last3 = gaps[-3:]
    prior3 = gaps[-6:-3]
    majors = {int(m.group(1)) for v, _ in releases if (m := _MAJOR.match(v))}

    return {
        "days_since_last_release": (now - last) / DAY,
        "releases_12m": int((ts >= _years_back(asof, 1).timestamp()).sum()),
        "releases_24m": int((ts >= _years_back(asof, 2).timestamp()).sum()),
        "median_gap_last_5": float(np.median(gaps[-4:])) if len(gaps) else np.nan,
        "gap_trend": float((np.median(last3) + 1) / (np.median(prior3) + 1)) if len(prior3) else np.nan,
        "total_versions": len(releases),
        "package_age_days": age,
        "major_version_count": len(majors),
        "releases_per_year_lifetime": len(releases) / max(age / 365.25, 1 / 365.25),
        "longest_prior_gap_days": float(gaps.max()) if len(gaps) else 0.0,
        "is_scoped": int(bool(is_scoped)),
    }


def in_frame(pre: list[tuple[str, datetime]]) -> bool:
    """The three pre-registered frame filters, on releases before the cutoff."""
    if len(pre) < 5:
        return False
    if not any(t.year == 2023 for _, t in pre):
        return False
    return min(t for _, t in pre).date() <= FRAME_FIRST_PUBLISH_DATE


def build_table() -> pd.DataFrame:
    from label import dormant_label  # local import keeps label logic in its own module

    db = sqlite3.connect(DB_PATH)
    rows = []
    n_ok = 0
    for name, tj, scoped in db.execute(
            "SELECT name, time_json, is_scoped FROM packages WHERE status='ok' ORDER BY name"):
        n_ok += 1
        releases = parse_time_map(json.loads(tj))
        pre = before(releases, CUTOFF)
        if not in_frame(pre):
            continue
        row = {"name": name, **compute_features(pre, bool(scoped), CUTOFF)}
        row["dormant"] = dormant_label(releases)
        rows.append(row)
    df = pd.DataFrame(rows, columns=["name", *FEATURES, "dormant"])
    print(f"ok documents: {n_ok}; retained rows: {len(df)}")
    return df


def main():
    df = build_table()
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(TABLE_PATH, index=False)
    print(f"wrote {TABLE_PATH} rows={len(df)} dormant={int(df.dormant.sum())} "
          f"base_rate={df.dormant.mean():.4f}")
    print("feature NaN counts:", df[FEATURES].isna().sum().to_dict())


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
