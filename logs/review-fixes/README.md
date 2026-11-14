# Curator review fixes

## Round 5, 2026-11-14

Base: `8f599d5873ceb53b811db6c62487eb0276cd0a6c`, branch `curator/2026-11-09`. Addresses the six requested fixes from the fourth-round reviews.

- Added six regression groups before production edits: all six failed, while the existing 15 groups passed. Controlled SIGALRM injection reproduces the property-return handoff; a manual clock/fake timer checks deadlines without timing assumptions. Existing assertions remain, with exact exit expectations updated for the requested contract.
- Property results/coverage are registered before execution; confirmed failures survive the handoff with exit 1. Request-path bindings respect mutually exclusive if/elif/else arms and reject uncertain joins. Git failures inside worktrees exit 2 without changing the baseline; ordinary non-git fallback stays quiet.
- Property call timeouts now exit 3; bounded model-call timeouts remain exit 2. Both checkers use one-shot nearest-deadline timers, keep synchronous return checks, and restore prior signal state when still owned. Zero disables each limit independently; migration and SIGALRM ownership are documented, including generated project skills.
- Focused verification: all 21 groups pass (53 assertions). The final six new groups also all fail when replayed against saved base production sources. Reproduce with `python3 scripts/run_examples.py --review-regressions /tmp/poteto-round5-results` using a fresh output directory.
- Full `bash scripts/verify.sh --run-examples` on an isolated copy: **7 PASS / 2 FAIL, exit 1**. Formal verification passed 93 assertions; correct passed 46; docs, syntax, helper contracts, installation and the fresh app tests/lint passed. Create/maintain hit the sandbox's loopback-bind `PermissionError`; curator hit `/dev/fd/63: Operation not permitted`. Example regeneration/results therefore remain failing; full verification did not pass in that sandbox.
- Full `bash scripts/verify.sh --run-examples` on Linux, run twice outside the sandbox: **9 PASS / 0 FAIL, exit 0** both times (formal-verification 93, correct 46, maintain 44 assertions). `logs/examples/` holds the output of that Linux run; `--review-regressions` there also passed all 21 groups.
- Isolated copies and fixture repositories were removed. Detailed output stays in system temporary space; no nested Git repository or temporary fixture directory remains under `logs/`.

## Follow-ups (not fixed in round 5)

- `bounded_check.py`: crossing the deadline after search can mislabel `trace_incomplete`, overwrite `stopped`, and downgrade a completed pass to exit 3.
- The summary “outside the search; confirmed findings retained” can contradict where/what actually stopped.
- `scan_repo.py`: `GO_HANDLE_RECEIVERS` still lacks `api` / `engine` / `group`.
- Request paths using `self.path.split("?")[0]` / `partition("?")` remain unrecognized; maintain-verification-skill's “every route” wording overstates coverage.
- Remaining patlint WARNING noise, one git subprocess per file, and undocumented empty-directory/submodule behavior changes.
- Other documentation mismatches: patlint `--help`, “Committing” versus index membership, and module `TIMEOUT` versus the import-time budget.
- JSON mode does not print seed/bounds to stderr.
- A run timeout can overwrite gen_check's earlier `incomplete` shrink details.
- Existing bounded-check init/terminal/describe exceptions are not wrapped as `ModelError`.

## Previous reassessment, 2026-11-14

Base: `14880b7069089191e3a5bd19f7513570cb3ca4fd`, branch `curator/2026-11-09`. This pass addresses the two current findings in `reviews/codex-curator-2026-11-09.md`; no commit or push was performed.

- Added `review_synchronous_deadlines` and `review_path_rebindings` to `scripts/run_examples.py` before changing production code. Both failed on the base implementation while the existing 13 groups passed. Existing assertions were neither removed nor weakened.
- Deadline regressions advance a manual clock without delivering a timer tick. They cover exact/overdue returns, earliest run/call expiry, hooks, generation, shrink attempts, property/mutant boundaries and final results. Timed-out inputs add no accepted target coverage; an expired run starts no later property or mutant.
- Route regressions cover translated-path tuple/list unpacking, `for`/`async for`, `with`/`async with`, named expressions, augmented assignment and other local bindings. Unknown sources revoke path/segment provenance; proven aliases, nested unpacking and existing positive cases remain valid.
- Focused verification: all 15 groups pass (44 assertions). Reproduce with `python3 scripts/run_examples.py --review-regressions /tmp/poteto-final-review-results` using a fresh output directory.

Full verification ran on an isolated copy with `bash scripts/verify.sh --run-examples`: **7 PASS / 2 FAIL, exit 1**. Formal verification passed **87 assertions**; syntax, executable contracts, docs/mapping, installer isolation and the fresh example app's seven tests/lint passed. Create/maintain hit the sandbox's loopback-bind `PermissionError`; curator hit `/dev/fd/63: Operation not permitted`. These leave the example-regeneration and example-results gates failing; full verification has not passed.

[sync-rebinding-summary.json](sync-rebinding-summary.json) retains compact before/after evidence and production hashes. The final tests were also replayed against unchanged base production sources: only the two new groups failed. An AST comparison confirmed all 213 existing `ensure` assertions are unchanged. Isolated copies and fixture repositories were removed; detailed output stays in system temporary space. No nested Git repository or temporary fixture directory remains under `logs/`.

## Previous reassessment, 2026-11-13

This prior pass fixed three earlier findings, starting from `03dcb22f01f0d7dca713aca89eb99a7fc5c8c83d` on `curator/2026-11-09`. Its changes were committed before the current reassessment; the evidence in this section is historical.

The three new regression groups were added to `scripts/run_examples.py` before changing production code. **All three failed on the starting implementation; all ten existing groups passed.** [reassessment-before/source-state.json](reassessment-before/source-state.json) records the commit, source hashes and empty production diff, while [reassessment-before/summary.json](reassessment-before/summary.json) and [reassessment-before/run.log](reassessment-before/run.log) retain the failures. An additional real-SIGALRM reproduction against the starting checker is in [reassessment-before/shrink-real-signal.json](reassessment-before/shrink-real-signal.json): a hung shrink candidate incorrectly returned exit 0, `ok: true`, and `incomplete: null` with `--expect-fail`.

After fixing the implementation, **all 13 regression groups pass, with 42 assertions**: [reassessment-after/summary.json](reassessment-after/summary.json), [reassessment-after/run.log](reassessment-after/run.log). No existing assertion was removed or weakened.

| New regression | Contract and coverage |
|---|---|
| `review_import_budget` | Uses a manual monotonic clock to compare both checkers before, exactly at, and after `TIMEOUT`; import time is included, expired budgets exit 3 without running a property, and explicit `--timeout` overrides remain effective. |
| `review_go_receivers` | Covers mux aliases, struct fields, `Handle`, mixed-case router methods and returned muxes, asserting both route inventory and `server` launch classification. `kv.Put("/var/lib/data", v)` and untrusted fields/handlers stay excluded and remain `cli`; known receivers retain filesystem-like HTTP paths. |
| `review_shrink_call_timeout` | Keeps the best confirmed reduction, failure detail and accepted target coverage; records the hung candidate, call scope, shrink phase and incomplete status. Stops further reduction but runs the next property. Checks JSON/text output, exact exit codes and a real infinite-loop candidate under SIGALRM. |

The shrink contract is explicit in `skills/formal-verification/SKILL.md`: an unexpected confirmed failure retains exit 1; an otherwise valid `--expect-fail` run with a shrink call timeout exits 2 and cannot be successful; an independent unexpected failure still takes exit 1. The confirmed failure remains visible alongside `timeout_scope`, `timeout_phase`, `input`, and `incomplete`. Run timeout/Ctrl-C retain their existing failure-versus-inconclusive behavior.

Full verification ran once on an isolated copy of the current tracked working files, using `bash scripts/verify.sh --run-examples`: **7 PASS / 2 FAIL, exit 1**. [reassessment-verification/verify.log](reassessment-verification/verify.log) records the gate output; [reassessment-verification/run.json](reassessment-verification/run.json) records source hashes, and [reassessment-verification/summary.json](reassessment-verification/summary.json) and the adjacent per-skill logs retain the example results. The isolated copy was removed afterward; this run did not overwrite the historical `logs/examples/` snapshot.

- Formal verification passed **86 assertions**, including the new import and shrink cases; correct passed **44**. The new Go receiver regression also passed before maintain-verification-skill reached its HTTP launch.
- Create/maintain failed at the existing loopback port allocation: `PermissionError: [Errno 1] Operation not permitted`. Curator passed 62 assertions, then failed at `curate.sh:57` opening `/dev/fd/63` for output process substitution. [reassessment-verification/environment.json](reassessment-verification/environment.json) independently records both environment denials. No check was skipped or changed to hide these failures.
- Frontmatter, all 27 executable help contracts, Python/shell syntax, documentation/mapping, installer isolation, and the fresh example app's seven unit tests and lint passed. The two failing gates are example regeneration and the example-results summary. Full verification has **not** passed; it requires an environment permitting those existing operations.

Reproduce the focused run with `python3 scripts/run_examples.py --review-regressions /tmp/poteto-reassessment-results`. Fixture repositories and driver files now live under the runner's temporary scratch directory and are removed in `finally`; the requested output directory receives only `run.log` and `summary.json`. Git baseline, Go route and git-fallback fixtures also use scratch space during the full example run.

Twenty untracked fixture directories left by the previous recheck (67,551 bytes, including two nested `.git` directories) were moved outside `logs/` without changing their contents. [reassessment-cleanup.json](reassessment-cleanup.json) lists the exact paths and retained temporary location. Existing evidence logs, summaries and tracked fixture snapshots remain intact. A recursive check found no `.git` entries or temporary fixture directories remaining under `logs/`.

## Previous follow-up, starting at `77c8dd9`

The previous follow-up addressed the earlier Codex/Claude review findings on `curator/2026-11-09`, starting from `77c8dd9`. Its code fixes were subsequently committed as `03dcb22` before the reassessment below began. The evidence in this section is historical.

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

That full run needed an environment permitting the existing HTTP and output-redirection checks. The previous follow-up was uncommitted when this historical evidence was recorded; its fixes are now in `03dcb22`.
