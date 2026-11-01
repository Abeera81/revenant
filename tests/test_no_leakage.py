import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from features import CUTOFF, before, compute_features  # noqa: E402
from label import dormant_label  # noqa: E402


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


PRE = [
    ("0.1.0", utc(2019, 3, 1)), ("0.2.0", utc(2019, 9, 1)), ("1.0.0", utc(2020, 6, 15)),
    ("1.1.0", utc(2021, 2, 1)), ("2.0.0", utc(2022, 5, 10)), ("2.1.0", utc(2023, 4, 2)),
    ("2.1.1", utc(2023, 11, 30)),
]
POST = [("3.0.0", utc(2024, 6, 1))]


def test_features_ignore_2024_release():
    without = compute_features(before(PRE, CUTOFF), False, CUTOFF)
    with_post = compute_features(before(PRE + POST, CUTOFF), False, CUTOFF)
    assert without == with_post


def test_label_sees_2024_release():
    assert dormant_label(PRE) == 1
    assert dormant_label(PRE + POST) == 0
    assert dormant_label(PRE + [("9.0.0", utc(2025, 1, 1))]) == 1  # window end is exclusive


def test_feature_function_rejects_post_cutoff_timestamp():
    with pytest.raises(AssertionError, match="leakage"):
        compute_features(PRE + POST, False, CUTOFF)
    with pytest.raises(AssertionError, match="leakage"):
        compute_features(PRE + [("x", CUTOFF)], False, CUTOFF)  # exactly at cutoff also rejected
