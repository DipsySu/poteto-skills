# Curator review fixes, 2026-11-13

The ten regression groups added to `scripts/run_examples.py` were executed against the unchanged production helpers first, then against the fixes. All ten failed before the fixes and passed afterward. The strict-exit regression injects exit 1 into otherwise clean drift reports and checks that both clean-map examples reject it.

| Regression | Fix |
|---|---|
| maintain_path_comparisons | Only request-path comparisons bypass filesystem-root filtering; filesystem checks do not create route drift. |
| maintain_zero_nargs | Explicit nargs=0 takes precedence over a registered string argparse action. |
| maintain_exit_contract | Clean drift examples require exit 0; the known missing route requires exit 1. |
| correct_git_file_set | An explicitly named ignored directory expanding to zero files returns a visible usage error. |
| correct_baseline_contract | Documentation distinguishes first rule adoption from increasing an existing baseline; actual ratchet behavior is exercised. |
| formal_trace_timeout | A timeout in describe() retains the confirmed violation, raw states and completed descriptions. |
| formal_shrink_interrupt | SIGINT while shrinking retains the best confirmed counterexample and accepted target coverage. |
| formal_property_seeds | Earlier per-call timeouts cannot change later properties' seeded input streams. |
| formal_import_timeout_json | Import-time bounded-check timeouts emit parseable JSON and exit 3. |
| formal_limit_support | Both checkers warn on stderr for unavailable interval timers and worker-thread execution; skill documentation states the scope. |

Evidence: `regressions-before.json`, `regressions-before.log`, `regressions-after.json`, `regressions-after.log` and their fixtures under `before/` and `after/`.

`scripts/verify.sh --run-examples` was run twice after the fixes. Both runs exited 1 with **7 PASS, 2 FAIL**. Full verification has not passed. The failures are the example-regeneration check and its summary check:

- create-verification-skill and maintain-verification-skill cannot launch their local HTTP server: binding `127.0.0.1` returns `PermissionError: [Errno 1] Operation not permitted`. An independent loopback-bind probe reproduced the denial.
- curator cannot open its output process-substitution path: `curate.sh: line 57: /dev/fd/63: Operation not permitted`. A captured-output process-substitution probe reproduced the denial.

The second full run recorded 53 passing formal-verification assertions and 39 passing correct assertions. All new maintain regressions passed before that skill reached the blocked HTTP launch. Frontmatter, 27 executable help contracts, Python and shell syntax, docs/mapping, installer isolation, and the fresh example app's seven unit tests and lint passed. `verify.log` records the first complete verification attempt; `logs/examples/` contains the second run's per-skill logs and summary.

No assertions were weakened or skipped to accommodate these restrictions. Only the eight directly related non-log files were changed. No commit or push was made in the working branch. The full verification command still needs to run in an environment permitting the existing local HTTP and output-redirection checks.
