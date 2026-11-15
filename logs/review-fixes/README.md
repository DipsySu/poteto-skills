# Curator review fixes

## Round 6, second fix, 2026-11-15

Base and unchanged HEAD: `31c8c5a20f3caf447da276571ea5d9aca4ddf8eb`, branch `curator/2026-11-09`. This addresses only A1–A3 and B1–B4 from the second round-6 review. Every finding was reproduced before editing its production code. No commit or push was performed.

The existing 27 regression groups passed on the base. The final regression runner was then replayed against byte-identical archived HEAD production files: **27 existing groups pass, all 7 new groups fail** ([summary](round6-second-before/summary.json), [log](round6-second-before/run.log), [source hashes](round6-second-before/source-state.json)). On the fixed working files, `python3 -B scripts/run_examples.py --review-regressions /tmp/poteto-round6-second-results` passes **34 groups / 67 assertions** ([summary](round6-second-after/summary.json), [log](round6-second-after/run.log), [source hashes](round6-second-after/source-state.json)). Use a fresh output directory. The new groups also run in their corresponding full formal/maintain example paths.

| Item / regression | Root cause, reproduction, fix and coverage |
|---|---|
| A1 / `review_timeout_format_handoff` | A call timeout formatted its input before recording the timeout. A controlled clock advancing again in `repr` reproduced `accepted=0`, `result=pass`, exit 1 with `--expect-fail`. Timeout fields are now stored first; properties start as `pending` and become `pass` only after completion. Both ordinary and expected-failure runs now exit 3. |
| A2 / `review_interruptible_result_format` | User `repr`/`jsonable` ran while alarms were deferred. Both checkers exceeded an external 8 s guard despite a 0.5 s internal budget. The protected region now records only confirmed outcomes, coverage and safe placeholders; user formatting remains interruptible. Manual clocks assert formatting runs with recording depth zero, and real infinite `repr` fixtures retain the confirmed failure and exit 1 with `output_incomplete` / `trace_incomplete`. Existing signal-boundary and shrink counterexample assertions remain unchanged. |
| A3 / `review_expression_paths` | Optional walrus assignments incorrectly established definite request provenance, reproducing `GET /var/www/<*>` for a translated filesystem path. Short-circuit operands and conditional-expression arms now have conditional contexts; expression tests precede their arms. Uncertain joins revoke provenance, while proven `and`-true / `or`-false paths retain aliases. Positive cases cover `if`, `not`, conditional expressions and case guards; existing if/elif/else and match/case groups still pass. |
| B1 / `review_bounded_limit_workload` | Every invariant/actions call paid generator-recording and two timer updates, even with limits disabled. Search now uses a minimal recording counter, reuses an earlier one-shot alarm and bypasses call/record wrappers when both limits are zero. Each call retains its own absolute deadline. Controlled-clock tests verify an obsolete alarm rearms to the actual call/run deadline without losing a hung call or adding periodic polling. Counts and repeated timings are below. |
| B2 / `review_net_change_docs` | Comparing local main `98c3efb3ad5879ca38faa5879c5fcc523e8dccc5` confirmed neither checker had any timeout API ([evidence](round6-second-before/main-docs.json)). Public docs/help now describe added flags, `CHECK_TIMEOUT` and exit contracts, rather than a nonexistent main `TIMEOUT` migration. CHANGELOG retains all main entries and summarizes this branch's net changes; true seed-replay migration remains. New assertions reject branch-only migration/round prose and require overhead and incompatible-exit notices. |
| B3 / `review_query_path_aliases` | Request provenance was lost when removing query text, so `/home` and `/Users` disappeared. `split`/`partition` on `?` or `#` now preserve the first element, including tuple/list unpacking. Regression positives cover aliases and both split forms; filesystem sources, non-first elements and unknown separators stay excluded. |
| B4 / `review_go_receiver_workload` | Receiver regexes repeatedly searched the full file prefix. A 256-character window bounds each lookup; ambiguous truncated prefixes remain untrusted. Route inventory, field/identifier boundaries and existing Go positives stay covered. The gate counts searched characters rather than imposing a short timing limit. |

[Assertion audit](round6-second-after/assertion-audit.json): **229 of the 230 existing `ensure` assertions are byte-for-byte equivalent ASTs**. The only changed assertion strengthens `TIMEOUT` to `CHECK_TIMEOUT` while retaining every other condition; 8 assertions were added. No assertion was deleted or weakened. Additional independent real-signal probes cover [interruptible output](round6-second-after/interruptible-output.json) and [a hung call after reused alarms](round6-second-after/coalesced-hung-after-fast-calls.json).

Deterministic [workload evidence](round6-second-performance/workload.json): at 1,000 states / 3 invariants, enabled-limit timer updates fall **8,002 → 3**; with both limits disabled, call/record wrappers fall **4,000/3,000 → 0/0**. Go receiver scans at 100/200/400/800 routes fall from **160,755 / 646,405 / 2,607,705 / 10,490,305** characters to **24,684 / 50,284 / 101,484 / 203,884**. Eightfold routes now produce 8.26× work rather than 65.26×.

Repeat the observational benchmark with `python3 -B logs/review-fixes/round6-second-performance/benchmark.py`; it loads pinned main/base sources plus the working checker, runs 100,000 integer states / 3 invariants three times per case and records source hashes. [Results](round6-second-performance/results.json), medians in seconds:

| Version | Default limits | Both limits disabled |
|---|---:|---:|
| main (no timeout API) | 0.126 | — |
| Reviewed HEAD | 2.120 | 0.872 |
| Fixed | 0.660 | 0.141 |

Enabled limits still cost about **5.23× main** on this deliberately cheap-call workload; disabled limits cost about **1.12×**. Timings are not regression gates. CHANGELOG and the formal skill explicitly retain the additional-overhead and default-90-s risks; no claim of main-equivalent enabled performance is made.

Full `bash scripts/verify.sh --run-examples` ran once in an isolated copy: **7 PASS / 2 FAIL, exit 1** ([gate output](round6-second-verification/verify.log), [example results](round6-second-verification/summary.json), [source record](round6-second-verification/source-state.json)). Formal verification passed **101 assertions**, correct passed 47, and all new scanner groups passed before maintain's launch. Script help, syntax, docs/mapping, installation isolation and the fresh example app tests/lint passed. Create/maintain were blocked by `PermissionError` binding `127.0.0.1`; curator was blocked by `/dev/fd/63: Operation not permitted` ([exact excerpts](round6-second-verification/environment.json)). Example regeneration/results therefore remain failing in this sandbox; the full suite has **not** passed. The historical Linux results below belong to the earlier fix, not this one.

Full `bash scripts/verify.sh --run-examples` on Linux, run twice outside the sandbox: **9 PASS / 0 FAIL, exit 0** both times (formal-verification 101, correct 47, maintain 49 assertions). Gate output is in [verify-box.log](round6-second-linux/verify-box.log) and [verify-box2.log](round6-second-linux/verify-box2.log). `logs/examples/` holds the second Linux run. An independent Linux `python3 scripts/run_examples.py --review-regressions` also passed **all 34 groups / 67 assertions**: [summary](round6-second-linux/regressions-summary.json), [log](round6-second-linux/regressions-run.log).

Only evidence files and the repeatable benchmark remain under the new log directories. The isolated verification copy and scratch fixtures were removed; no nested `.git` or temporary directory remains under `logs/`. Opus #5–11 were not implemented; the three previously unrecorded details are appended to Follow-ups below. B2 necessarily adds the empty-directory incompatible-exit notice to CHANGELOG without changing patlint behavior.

## Round 6, 2026-11-15

Base and unchanged HEAD: `f6e20f19858c039fab63bd4319717d0170c824c7`, branch `curator/2026-11-09`. Scope is the six requested findings from the gpt-6-astra / opus reviews of that HEAD. No commit or push was performed.

Before any production edit, the existing 21 regression groups passed and the six new groups failed: [initial summary](round6-before/summary.json), [source hashes](round6-before/source-state.json). The final, strengthened six groups were then replayed against byte-identical saved HEAD production files: **all six fail** in [base replay](round6-base-replay/summary.json) / [log](round6-base-replay/run.log). This final replay is the authoritative failure evidence: the initial bounded signal-injection fixture referenced a closure local absent in HEAD; the fixture was corrected and the original failure (exit 3, lost violation) was independently reproduced. The replay also includes both shrink handoff windows and the added guarded-case coverage.

After the fixes, `python3 scripts/run_examples.py --review-regressions /tmp/poteto-round6-after-final` passed **27 groups / 59 assertions**: [summary](round6-after/summary.json), [log](round6-after/run.log), [final source hashes](round6-after/source-state.json). Use a fresh output directory when repeating the command. All six groups also run in their respective full example paths. An AST comparison confirms all **224 existing `ensure` assertions are unchanged**; six new assertions were added. Only the two module-budget fixtures changed `TIMEOUT` to `CHECK_TIMEOUT`, and the existing dubious-ownership fixture now has a checkout marker to represent its fail-closed scenario.

| Requested fix / regression | Root cause, reproduction and result |
|---|---|
| 1 / `review_confirmed_signal_boundary` | Real SIGALRM injection after a failing check returned but before recording changed both checkers from exit 1 to 3 and lost the violation; gen also lost accepted coverage. Short result-recording regions now defer alarms until the counterexample, coverage and raw trace are stored. Four injection stages cover the initial gen/bounded return and shrink candidate/whole-shrink return; a confirmed reduced `7` cannot revert to `42`. Pre-registered result containers remain in place; user calls and trace descriptions stay interruptible. |
| 2 / `review_match_case_paths` | A disk alias acquired request provenance from another case, creating `GET /var/www/<*>`. Case bodies now have exclusive branch contexts; uncertain joins revoke aliases. Failed guards can leave pattern/guard bindings behind, so those are treated conservatively without losing same-case positive aliases. Existing if/elif/else, unpacking and route positives remain covered. |
| 3 / `review_git_locale` | English error-string matching made the supplied `/private/etc/hosts` command exit 0 in `C` but exit 2 in `zh_CN.UTF-8`. Checkout markers/environment now decide fallback independently of diagnostic language. Real Chinese locale plus a deterministic translated-git stub cover files/directories/root inference; outside-git errors stay quiet and worktree errors still exit 2 without modifying baselines. The existing missing-git warning is retained. |
| 4 / `review_swallowed_alarm` | A swallowed one-shot alarm left no signal armed. `tick` now schedules a 0.1 s one-shot retry before raising after expiry; call cleanup resets it. Manual clocks verify successive swallowed alarms, earliest deadline, no pre-deadline polling and timer reset in both checkers. Real bare-except loops stop around 0.4 s instead of completing their 3 s busy period, with a loose supplemental upper bound; timing is not the primary assertion. |
| 5 / `review_route_workload` | Every route redundantly resolved provenance, repeatedly walking its whole function. Function binding/scope/branch indexes are cached, only filesystem-prefix candidates require provenance, and normalized paths are shared across methods. For N=20/60/120, both ordinary and filesystem-like route inventories must remain exact and AST work at sixfold N must stay below eightfold growth. |
| 6 / `review_budget_namespace` | Generic business `TIMEOUT` was silently interpreted as seconds (`0.3` interrupted a passing run; `None` caused exit 2). Both checkers now use only `CHECK_TIMEOUT`; manual-clock cases prove business values `0.3`, `3`, and `None` are ignored, while module limits, CLI precedence and zero opt-outs work. Existing import-budget/equality tests retain their assertions. Help, SKILL.md, generated-skill instructions, CHANGELOG and MAPPING describe migration and the initial CLI/default import budget. |

Performance evidence ([measurements](round6-after/performance.json); seconds are observational, not test gates):

| Routes N | HEAD wall time | Fixed wall time |
|---|---:|---:|
| 100 | 0.142635 s | 0.008416 s |
| 300 | 1.270054 s | 0.023403 s |
| 600 | 5.068269 s | 0.047314 s |

At N=20→120, AST child-edge visits fell from **4,715→111,715 (23.69×)** to **1,735→9,835 (5.67×)** for ordinary paths and **1,884→10,684 (5.67×)** for filesystem-like paths. An independent scanner executed-line counter also measured near-linear growth at N=100→600 (5.94× ordinary / 5.94× filesystem-like), guarding against merely moving quadratic work out of AST traversal.

Full verification ran once on an isolated copy using `bash scripts/verify.sh --run-examples`: **7 PASS / 2 FAIL, exit 1**. [Gate output](round6-verification/verify.log), [example summary](round6-verification/summary.json) and [run/source record](round6-verification/run.json) retain the results. Formal verification passed 96 assertions and correct passed 47; the scanner regressions passed before maintain's launch attempt. Script contracts, syntax, docs/mapping, install isolation and the fresh example app tests/lint passed. Create/maintain were blocked by `PermissionError: [Errno 1] Operation not permitted` in loopback port allocation; curator was blocked by `/dev/fd/63: Operation not permitted`. Example regeneration and the example-results gate therefore remain failing; the full suite has **not** passed in this sandbox. No assertions or environment checks were skipped to hide these failures.

Full `bash scripts/verify.sh --run-examples` on Linux, run twice outside the sandbox: **9 PASS / 0 FAIL, exit 0** both times (formal-verification 96, correct 47, maintain 46 assertions). Gate output is in [verify-box.log](round6-linux/verify-box.log) and [verify-box2.log](round6-linux/verify-box2.log). `logs/examples/` holds the second Linux run. An independent Linux `python3 scripts/run_examples.py --review-regressions` also passed **all 27 groups / 59 assertions**: [summary](round6-linux/regressions-summary.json), [log](round6-linux/regressions-run.log).

The isolated verification copy and runner scratch repositories were removed; retained output directories contain evidence files only. No nested `.git` or temporary fixture directory remains under `logs/`. Low-priority findings were not fixed; the cumulative Follow-ups below retain the old entries and add the missing opus #5–11 items.

## Round 5, 2026-11-14

Base: `8f599d5873ceb53b811db6c62487eb0276cd0a6c`, branch `curator/2026-11-09`. Addresses the six requested fixes from the fourth-round reviews.

- Added six regression groups before production edits: all six failed, while the existing 15 groups passed. Controlled SIGALRM injection reproduces the property-return handoff; a manual clock/fake timer checks deadlines without timing assumptions. Existing assertions remain, with exact exit expectations updated for the requested contract.
- Property results/coverage are registered before execution; confirmed failures survive the handoff with exit 1. Request-path bindings respect mutually exclusive if/elif/else arms and reject uncertain joins. Git failures inside worktrees exit 2 without changing the baseline; ordinary non-git fallback stays quiet.
- Property call timeouts now exit 3; bounded model-call timeouts remain exit 2. Both checkers use one-shot nearest-deadline timers, keep synchronous return checks, and restore prior signal state when still owned. Zero disables each limit independently; migration and SIGALRM ownership are documented, including generated project skills.
- Focused verification: all 21 groups pass (53 assertions). The final six new groups also all fail when replayed against saved base production sources. Reproduce with `python3 scripts/run_examples.py --review-regressions /tmp/poteto-round5-results` using a fresh output directory.
- Full `bash scripts/verify.sh --run-examples` on an isolated copy: **7 PASS / 2 FAIL, exit 1**. Formal verification passed 93 assertions; correct passed 46; docs, syntax, helper contracts, installation and the fresh app tests/lint passed. Create/maintain hit the sandbox's loopback-bind `PermissionError`; curator hit `/dev/fd/63: Operation not permitted`. Example regeneration/results therefore remain failing; full verification did not pass in that sandbox.
- Full `bash scripts/verify.sh --run-examples` on Linux, run twice outside the sandbox: **9 PASS / 0 FAIL, exit 0** both times (formal-verification 93, correct 46, maintain 44 assertions). `logs/examples/` holds the output of that Linux run; `--review-regressions` there also passed all 21 groups.
- Isolated copies and fixture repositories were removed. Detailed output stays in system temporary space; no nested Git repository or temporary fixture directory remains under `logs/`.

## Follow-ups (retained through round 6)

Second-fix status: B3 resolves the split/partition alias item below. B2 resolves the module-budget documentation premise and the empty-directory CHANGELOG notice; earlier references to a `TIMEOUT` migration describe branch history, not a main API. The remaining aspects of these historical entries stay deferred.

- `bounded_check.py`: crossing the deadline after search can mislabel `trace_incomplete`, overwrite `stopped`, and downgrade a completed pass to exit 3.
- The summary “outside the search; confirmed findings retained” can contradict where/what actually stopped.
- `scan_repo.py`: `GO_HANDLE_RECEIVERS` still lacks `api` / `engine` / `group`.
- Request paths using `self.path.split("?")[0]` / `partition("?")` remain unrecognized; maintain-verification-skill's “every route” wording overstates coverage.
- Opus #9 also notes tuple-membership route checks remain unrecognized.
- Remaining patlint WARNING noise, one git subprocess per file, and undocumented empty-directory/submodule behavior changes.
- Other documentation mismatches: patlint `--help`, “Committing” versus index membership, and module `TIMEOUT` versus the import-time budget.
- JSON mode does not print seed/bounds to stderr.
- A run timeout can overwrite gen_check's earlier `incomplete` shrink details.
- Existing bounded-check init/terminal/describe exceptions are not wrapped as `ModelError`.
- Opus #5: bounded model-call timeout exits 2 but also populates `incomplete` and prints “INCOMPLETE”; separate model errors from run-budget incompleteness in a later change.
- Opus #6: completed property work can be reported as stopping inside the last property; completion bookkeeping and append-only incomplete details remain deferred.
- Opus #8: explicit files still start separate git processes; a copied checkout with `.git` but no git executable exits 2. Empty/ignored directory and submodule behavior remains as recorded above.
- Opus #9: `scan_repo.py`'s Handle/HandleFunc receiver docstring still lists `engine/group/api` beyond the implemented receiver set. The prior module `TIMEOUT`/import-budget mismatch above is addressed only as part of the required round-6 `CHECK_TIMEOUT` migration; the other documentation follow-ups remain open.
- Opus #10: generated project skills still require the one-time seed migration note; deciding whether new skills should omit historical migration prose is deferred.
- Opus #11: `review_docs` still depends on this review README and exact CHANGELOG wording, and `review_fixture_limits` inspects the runner's own source literals. Whether review logs belong in main and how to decouple those tests remains deferred.
- Round-6 second review, opus #7: patlint's "files git would commit" / CI-clone wording omits the `SKIP_DIRS` exception for tracked `vendor/`, `dist/`, `build/` and `target/`; its help epilog also omits git failures and empty directories from exit 2. These help/wording changes remain deferred.
- Round-6 second review, opus #9: gen_check's import-timeout JSON reports `timeout: null` even when the initial 90 s budget was active. The existing append-only incomplete-details follow-up above remains open as well.
- Round-6 second review, opus #10: Go server/CLI classification depends on retained routes rather than matched registrations, so a filtered `s.mux.HandleFunc("/home")` can leave a server classified as CLI. This and the previously recorded receiver/docstring mismatch remain deferred.

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
