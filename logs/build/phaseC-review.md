# Phase C review of Phase B (Claude cross-reviewing Codex)

Scope: `git diff 58fd322 3246f78 -- skills install.sh` (Codex's changes to Phase A's skills; `install.sh` was unchanged), plus the new `scripts/run_examples.py` and `scripts/verify.py`. Method: I read every hunk, then re-ran each behaviour I doubted with throwaway harnesses under `/tmp/phaseC-scratch`. Localhost sockets work in this environment, so the live checks ran for real. Each fix below was shown to fail on Codex's version and pass after the fix. The agent-driven runs (`logs/agent-runs/`) surfaced two more issues, listed separately.

## Fixed

1. **High: generated cleanup leaks the real server behind a wrapper launch command** (`scaffold.py`, control.sh `cmd_cleanup`).
   - Codex replaced "signal the process group `launch` created" with "signal only the recorded PID". `launch` makes that PID the leader of a new process group (`setsid`, or `set -m` where `setsid` is missing), so the group is exactly what the run started.
   - With a wrapper launch (`sh -c "...; ..."`, `npm start`, `make run`), the recorded PID is the wrapper, and killing only it orphans the server.
   - Evidence, with a generated control.sh and a `sh -c` launch: Codex's version printed `"killed": true` and exited 0, but the port stayed open and the server PID was alive. Phase A's group kill and the fix both close the port with no process left.
   - Fix: after the identity check, signal `-PGID` when the PID leads its own group, otherwise the PID alone. Codex's zombie handling and non-empty identity requirement are kept. Prose updated in `SKILL.md`, the scaffold docstring and usage, and the generated SKILL.md Cleanup section.
   - Regression: `run_examples.py` `wrapped_cleanup`. On Codex's scaffold it fails with "cleanup also stops the server child of a wrapper launch command"; on the fix it passes.
2. **Medium: process-start identity became Linux-only.** `proc_started` reads `/proc`. Elsewhere (macOS) it printed nothing, so `launch` recorded an empty identity. Doctor then always failed "not running", and cleanup refused to signal ("start time differs") but still deleted the run state, leaving the server running with no record.
   - Fix: fall back to `ps -o lstart=` (Phase A's portable identity) when `/proc/PID/stat` is unreadable.
   - Evidence: I forced the fallback on Linux by pointing it at a missing path. Doctor exited 0 with identity `Mon Oct 19 14:28:29 2026`, and cleanup was clean. Not run on a real macOS host.
3. **Medium: the curator's `new_skills` became identical to `untracked_skills`** (`upstream_diff.sh`).
   - Every run re-reported every upstream skill without a snapshot (for example S12 `poteto-mode`, fetched but deliberately not snapshotted) as new. So `changes` never reached 0, "No proposals" was unreachable, and each offline proposal claimed "A new pstack skill appeared since the last curator run" about skills seen before. That is a false, unsourced claim in generated output.
   - Fix: a skill is new if it is absent from `research/upstream/` and the previous run had not seen it. The first run still reports all of them (Codex's intent). The offline "Why" text now states exactly that.
   - Regression: the curator example now adds a skill between runs and asserts the second run proposes only that one. Codex's scripts fail this (they re-propose `local-new`).
4. **Low: `check_proposals.py` docstring contradicted behaviour.** It said files without H2 sections are valid and that 401/403/429 are warnings; Codex made both of those failures. The docstring now states the explicit `No proposals` line and the 2xx-only rule.
5. **Low: `drift_check.py` note contradicted behaviour.** It said that with no source routes or commands "drift is limited to index and contract". Since Codex's change, every mapped route (and python/node command) is reported dead in that case. The note now says so, and says to confirm in source before deleting a recipe (matching maintain SKILL.md step 2).

## Found during the agent-driven runs (also fixed)

6. **Medium: `drift_check.py` reported a healthy map as drifted** (Codex's CLI-vocabulary recovery). Any `python3 -m X word`, `python3 path.py word`, or `node x.js word` in a feature file counted as an app CLI. A gotcha line saying ``Run `python3 -m unittest discover -s tests` `` gave `command dead in map: python3 -m unittest discover` and exit 1. The maintain skill then tells the agent to fix or delete it.
   - Fix: recover vocabulary only from recipes' `drive ... cli -- PROG` commands, which is how scaffolded and fixture recipes invoke the app CLI.
   - Codex's regression still holds: with `todo/__main__.py` deleted, all 26 mapped `python3 -m todo` mentions are reported dead.
   - New example assertion: an incidental python command in a recipe is not drift.
7. **Low: control.sh merged two runs' evidence.** `launch` refused an existing run id only if its *state* dir existed. Reusing `VERIFY_RUN_ID` after cleanup appended to the kept evidence dir (seen in the /correct transcript: 28 files from two runs, one manifest). `launch` now refuses a run id with kept evidence too. Phase A code, not Codex's.

## Checked; Codex's change is correct (kept)

- **patlint keeps zero-count adopted rules:** correct. Otherwise `--update-baseline` treats an emptied rule as new and re-adopts whatever violations exist.
- **check_verify_skill ignores `__pycache__`/`.pyc`:** not a weakening. Bytecode is neither a helper nor a TODO carrier, and every source file is still checked.
- **plan.py:** proof now needs hits at the mistake, zero at the fix, and both replays successful. Candidates are replayed per token before promotion, and the owner is resolved through the AST. All stricter, and SKILL.md was updated to match.
- **bounded_check:** the module is registered in `sys.modules` (dataclass fix), empty init sets and invalid bounds are rejected, and only an invariant violation kills a mutant. The help text matches.
- **Curator:** `--offline` implies no fetch, a failed diff aborts, online apply re-fetches, `CURATOR_VERIFY_CMD` is gone (no references left), and the fetch gate is 2xx-only. All stricter; Codex updated the curator SKILL.md.
- **drift_check:** dead routes are reported even when no source routes remain (Codex's motivating case); see 5 for the note.
- **run_examples.py / verify.py:** the assertions are real (exit codes, parsed JSON, digest checks, negative cases). Each `self.ensure(True, ...)` labels a preceding `self.run(..., expected=N)` that already asserts. One cosmetic point: `verify.py`'s app check prints a fixed "7 unit tests", though it does run the suite.
- **Docs:** S21 downgraded to `[unverified]` (only a search snippet was seen): correct. The README and MAPPING additions describe the runner accurately. The CHANGELOG line about PID-only cleanup was corrected.

## Noted, not changed (judgment calls)

- **rule_table:** Codex classifies prose words ahead of check words unless a backticked runnable span is present. That fixes "README documents the tests", which used to count as enforced. The trade-off: rows that name a real check in free text beside a prose word are now flagged. Probe results:
  - `pre-commit hook (reviewers double-check)` -> prose
  - `ruff in CI, see docs` -> prose
  - `` `ruff check .` in CI, see docs `` -> enforced

  This errs toward flagging, the safer side for /correct, and the fix is to name the command in backticks.
- **bounded_check with `--deadlock`:** BFS returns the first violation, so a mutant that deadlocks before it breaks an invariant counts as surviving. That is a false failure, not a false pass; run `--mutants` without `--deadlock`.
- **Process groups:** the group kill relies on the app not moving its children into new process groups or sessions. That holds for ordinary servers and wrappers.

## Verification

- `scripts/run-examples.sh` and `scripts/verify.sh` results: `logs/verify/run-examples.out`, `logs/verify/verify.out`.
- Agent-driven runs: `logs/agent-runs/create-verification-skill.md` and `logs/agent-runs/correct.md`, with artifacts in `logs/agent-runs/artifacts/`.
