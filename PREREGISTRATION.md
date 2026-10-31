# Revenant — Pre-registration

This specification was committed before any data was fetched and before any
model was fitted. It is frozen: the cutoff, the label, the sampling frame, the
feature list, the evaluation plan and the abstention rule must not be edited
after any model has been fitted.

Frozen at commit: 4403383 (2026-10-31T09:33:54+05:00)

## Pre-registered specification

**Cutoff:** 2024-01-01 (all features computed as of this instant).

**Label:** `dormant = 1` if the package published no version with a `time`
timestamp in `[2024-01-01, 2025-01-01)`; else `0`. One window, fixed.
Deprecation is excluded from the label because the npm registry does not
expose a deprecation date.

**Sampling frame.** Random sample of npm package names from the registry
replication endpoint, kept if ALL of:
- at least one version published during 2023
- at least 5 total versions published before the cutoff
- first publish on or before 2022-01-01

Target ~4,000 retained rows. The frame is random-npm; the product runs on
curated lockfile dependencies. That distribution shift is a stated
limitation, not something to paper over.

**Features** (all as of cutoff, from `time` and the package name only):
`days_since_last_release`, `releases_12m`, `releases_24m`,
`median_gap_last_5`, `gap_trend` (median gap of last 3 / prior 3),
`total_versions`, `package_age_days`, `major_version_count`,
`releases_per_year_lifetime`, `longest_prior_gap_days`, `is_scoped`.

**Abstention rule (pre-registered).** Revenant refuses to score a package
with fewer than 5 lifetime releases or less than 1 year of age at scoring
time, and shows the reason instead of a probability. Abstentions are
reported as a count, never silently dropped.

**Evaluation.** Stratified 70/30 split, `random_state=20261031`.
Three models on the identical split: TabPFN-3.5, logistic regression on
standardized features, and a predict-the-base-rate baseline. Report:
- base rate
- ROC AUC with 95% bootstrap CI (2,000 resamples) for each model
- confusion matrix at a threshold chosen on the training split only
- top-decile lift with 95% CI
- calibration: reliability curve and Brier score
- **ablation**: refit all three with `days_since_last_release` removed
- the three highest-confidence errors in each direction, named

If TabPFN loses to logistic regression or to the base rate, that is the
result and it goes in the post unchanged.
