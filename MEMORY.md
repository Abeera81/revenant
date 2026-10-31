# MEMORY

- Pre-registration (PREREGISTRATION.md) was frozen before any data collection or model fitting. No data has been fetched; no results exist yet.

## Decisions made before any model was fitted

- The pre-registered abstention rule cannot fire inside the backtest, because the sampling frame already requires at least 5 versions and 2 years of age at cutoff. Abstention counts will therefore be reported from the live lockfile run only, and the write-up must state that the backtest frame excludes abstainable packages by construction.
- The classification threshold will be chosen by maximising Youden's J on the training split only. This rule was fixed before any model was fitted.