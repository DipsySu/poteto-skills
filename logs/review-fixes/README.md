# Curator review follow-up, 2026-11-13

This follow-up addresses `reviews/codex-curator-2026-11-09.md` and `reviews/claude-curator-2026-11-09.md` on `curator/2026-11-09`, starting from `77c8dd9`. The first review fixes were already committed before this task. No commits or pushes are made by this follow-up.

Ten independent regression groups in `scripts/run_examples.py` failed before this follow-up's fixes. At that point only the regression runner had changed; the production helpers, existing fixtures and documentation were unchanged. [recheck-before/summary.json](recheck-before/summary.json) and [recheck-before/run.log](recheck-before/run.log) record the failures. After the fixes, **all ten groups passed, with 33 assertions**, in [recheck-after/summary.json](recheck-after/summary.json) and [recheck-after/run.log](recheck-after/run.log).

| Regression group | Verified behavior |
|---|---|
| review_deadline_order | Both checkers compare absolute run/call deadlines, including delayed call starts and ties; run timeout exits 3 and call timeout exits 2. |
| review_mutant_deadline | A timeout during description retains the confirmed kill and raw trace, starts no later mutant, and lists pending work as incomplete. A deadline between searches also prevents a new search. |
| review_mutant_json | Every mutant exposes result, stopped, detail and explored-state counts; top-level incomplete and not_run distinguish timeouts and unrun work from survivors. |
| review_mutant_errors | A crashing mutant reports ERROR and exit 2, alone or before a later run timeout. |
| review_sigint | The shrink fixture works when the parent ignores SIGINT and preserves the accepted counterexample and target coverage. |
| review_fixture_limits | Disk-using trace, seeded-property and import fixtures use 2-second budgets. Deadline-order checks use a manual clock. |
| review_phantom_routes | All five requested counterexamples, plus unbound/cross-function path names and tokens, produce no routes; proven path aliases, split segments and Go router calls remain discoverable. |
| review_baseline_retry | Untracked hits leave a new rule absent; staging the files permits retry; other rules and later ratchet enforcement remain intact. |
| review_git_warning | Missing git and failing git commands warn on stderr while retaining disk fallback. |
| review_docs | The changelog describes changed seed sequences, 90 s / 10 s defaults and migration; this README distinguishes current and historical evidence. |

The historical `before/`, `after/`, `regressions-*.json`, `regressions-*.log` and `verify.log` belong to the first review pass and are unchanged. They are not the evidence for this follow-up.

[recheck-hang.json](recheck-hang.json) additionally records a real-signal reproduction with two mutants sharing an infinite `describe()`: the starting-commit checker exceeded an external 5-second deadline despite `--timeout 1`; the fixed checker exited 3, retained the first confirmed kill, and left the second mutant unrun.

Reproduce the focused run with `python3 scripts/run_examples.py --review-regressions /tmp/poteto-review-results` (use a fresh output directory). It exercises deadline order using a manual monotonic clock, mutant result retention and JSON, error exit codes, inherited SIGINT ignore, real-time fixture budgets, phantom routes, baseline retry, git warnings, and migration documentation.

`bash scripts/verify.sh --run-examples` was run once for this follow-up: **7 PASS / 2 FAIL, exit 1**. [recheck-verify.log](recheck-verify.log) is the complete gate output; `logs/examples/` now contains that run's regenerated per-skill logs, summary and artifacts, rather than the older passing snapshot. Full verification has not passed.

- formal-verification passed all **78** assertions; correct passed **44**. The route/CLI regressions passed before maintain-verification-skill reached the blocked server launch.
- create-verification-skill and maintain-verification-skill failed launching their local HTTP server. The sandbox denied binding `127.0.0.1` with `PermissionError: [Errno 1] Operation not permitted`.
- curator passed 62 assertions before its offline proposal command failed at `curate.sh:57`: `/dev/fd/63: Operation not permitted`.
- [recheck-environment.json](recheck-environment.json) independently reproduces the loopback-bind and captured-output process-substitution denials. The two failing verification gates are example regeneration and the example-results summary, not two separate source-check failures.
- Frontmatter, all 27 executable help contracts, Python and shell syntax, documentation/mapping, installer isolation, and a fresh example app's seven unit tests and lint passed.

The pre-existing Linux-specific `ps --ppid` check is intentionally unchanged; this run failed earlier at HTTP launch. No assertion was removed or relaxed. Existing `gen_check.py` call-timeout checks now require exact exit 2 to match the requested contract; the request-path positive fixture now explicitly assigns `path = self.path`. JSON test commands parse stdout separately while retaining stderr in the logs, so expected fallback warnings cannot corrupt JSON parsing.

The full command still needs an environment permitting the existing HTTP and output-redirection checks. The working branch remains at its starting commit with these fixes uncommitted and unpushed.
