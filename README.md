# Revenant

Scores each npm dependency in a `package-lock.json` by its probability of going dormant.

A package is labelled **dormant** if it **went quiet**: it published no new version during the fixed label window defined in [PREREGISTRATION.md](PREREGISTRATION.md).

## On commit dates

All commits were made before the Oct 31, 11:59 PM PDT deadline. They display as Nov 1 because they carry a +05:00 (Pakistan) offset — the final commit, 88308c3 at 2026-11-01T09:47:43+05:00, is 2026-10-31T21:47:43 Pacific time.

## Results

Rows 3803 (train 2662, test 1141), stratified 70/30, random_state=20261031.
Base rate (dormant): all 0.3952, train 0.3952, test 0.3953.
CIs: percentile bootstrap over the test split, 2000 resamples.

| Model | ROC AUC [95% CI] | Top-decile lift [95% CI] | Brier |
|---|---|---|---|
| TabPFN | 0.8893 [0.8715, 0.9077] | 2.244 [2.042, 2.463] | 0.1327 |
| Logistic regression | 0.8380 [0.8153, 0.8614] | 2.200 [2.015, 2.415] | 0.1624 |
| Base rate | 0.5000 [0.5000, 0.5000] | 0.836 [0.795, 1.224] | 0.2390 |

Ablation: `days_since_last_release` removed

| Model | ROC AUC [95% CI] | Top-decile lift [95% CI] | Brier |
|---|---|---|---|
| TabPFN | 0.8712 [0.8523, 0.8907] | 2.178 [1.989, 2.386] | 0.1438 |
| Logistic regression | 0.7803 [0.7544, 0.8072] | 2.046 [1.848, 2.247] | 0.1882 |
| Base rate | 0.5000 [0.5000, 0.5000] | 0.836 [0.795, 1.224] | 0.2390 |

Full output (confusion matrices, thresholds, named errors, curves): [results/backtest.md](results/backtest.md).

## Limitations

- (a) The label measures a 12-month publish gap and is not proof of abandonment.
- (b) Deprecation is excluded because the registry does not date it.
- (c) The training frame is random npm packages, while the product scores curated lockfile dependencies. This is a stated distribution shift.

## Licence

This project is MIT licensed. TabPFN-3.5 is used under Prior Labs' non-commercial licence.
