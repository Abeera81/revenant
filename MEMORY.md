# MEMORY

- Pre-registration (PREREGISTRATION.md) was frozen before any data collection or model fitting. No data has been fetched; no results exist yet.

## Decisions made before any model was fitted

- The pre-registered abstention rule cannot fire inside the backtest, because the sampling frame already requires at least 5 versions and 2 years of age at cutoff. Abstention counts will therefore be reported from the live lockfile run only, and the write-up must state that the backtest frame excludes abstainable packages by construction.
- The classification threshold will be chosen by maximising Youden's J on the training split only. This rule was fixed before any model was fitted.
- Collection N was fixed by a wall-clock budget, not a row target: the sample is every name fetched within 100 minutes of starting the full run, beginning from the existing 20,000. Because the sample is a prefix of a permutation seeded at 20261031, any stopping point is still a uniform random sample. This rule was fixed before any label was computed and before the base rate was known. Target was ~140,000 names at concurrency 24; the actual N is whatever the cache holds at the cutoff.

## Stage: collector (src/collect.py)

Built: `python src/collect.py --n N [--concurrency 8]`. Enumerates all names from `https://replicate.npmjs.com/_all_docs` (unranked), caches them in `data/names.txt` (gitignored), draws a sample as the first N entries of `numpy.random.default_rng(20261031).permutation(len(names))` (so a larger N extends a smaller N's sample), fetches `https://registry.npmjs.org/<name>`, and stores only name, status, full `time` map, version count and scoped flag in `data/cache.db` (gitignored; rebuild by rerunning). Rows with status ok/not_found are never refetched; `error` rows are retried on rerun. Backoff on 429/5xx/transport errors. Log: `results/collect.log` (append-only, includes a failed first attempt caused by my own page-size bug).

Endpoint facts verified: `skip` is rejected (HTTP 400); `startkey` pagination works; `limit` max is 10000 (10001 gives 400). Full enumeration takes about 11 minutes. Name list snapshot taken 2026-10-31: 4552046 unique names, sha256[:16]=de33bcd6a5d85014 (registry total_rows was 4552052 at the end; names added during enumeration make the list approximate).

Real counts, run with n=20000, concurrency 8: sampled 20000; fetched ok 19962; not_found (404) 38; error 0; no 429/5xx retries logged; cache rows 20000. Rerun fetched 0 (resumability confirmed). Throughput about 10-12 docs/s.

Frame-filter retention (applying only the three frame filters from PREREGISTRATION.md to the cached `time` maps; label not examined): 570 of 19962 ok documents retained. This is about 2.9%, so ~4,000 retained rows needs roughly 140,000 sampled names, about 3.5-4 hours at the observed rate. NOT yet done: `--n` was set to 20000 for verification only.

Unknown / next: decide final N (and whether to raise concurrency) and run the full collection; then features and labelling. No features, labels or models exist. `examples/demo-lockfile.json` and a `package-lock.json` .gitignore line appeared in the working tree from outside this stage; they are not part of the collector.
