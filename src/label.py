"""Dormancy labelling.

dormant = 1 if the package published no version with a `time` timestamp in
[2024-01-01, 2025-01-01); else 0. The label reads only that window.
"""
from datetime import datetime, timezone

WINDOW_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2025, 1, 1, tzinfo=timezone.utc)


def label_window(releases: list[tuple[str, datetime]]) -> list[tuple[str, datetime]]:
    return [(v, t) for v, t in releases if WINDOW_START <= t < WINDOW_END]


def dormant_label(releases: list[tuple[str, datetime]]) -> int:
    window = label_window(releases)
    assert all(WINDOW_START <= t < WINDOW_END for _, t in window)
    return int(len(window) == 0)
