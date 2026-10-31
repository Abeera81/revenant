# Revenant

Scores each npm dependency in a `package-lock.json` by its probability of going dormant.

A package is labelled **dormant** if it **went quiet**: it published no new version during the fixed label window defined in [PREREGISTRATION.md](PREREGISTRATION.md).

## Results

TBD — the backtest has not been run yet.

## Limitations

- (a) The label measures a 12-month publish gap and is not proof of abandonment.
- (b) Deprecation is excluded because the registry does not date it.
- (c) The training frame is random npm packages, while the product scores curated lockfile dependencies. This is a stated distribution shift.

## Licence

This project is MIT licensed. TabPFN-3.5 is used under Prior Labs' non-commercial licence.
