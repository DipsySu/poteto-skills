---
name: formal-verification
description: "Formally check the part of a repo where a bug costs the most and tests reach the least: pick one property, check it with the tool that fits (TLA+ for orderings and concurrency, Kani for Rust, generated-input tests when no verifier exists, or the bundled stdlib bounded_check.py when nothing is installed), show it failing on a broken variant, report findings without weakening the property, and leave a project-local skill with a one-command check. Use for /formal-verification, \"model-check this race\", \"prove this invariant\", or when retries, leases, state machines, parsers, or unsafe code need more than tests."
---

# Formal verification

A test checks the cases someone thought to write. A model checker checks every ordering of steps in a model, a bounded verifier checks every input up to a bound, and a proof checks every input. Use one where that difference matters, then leave a check the next agent will actually run.

`$SKILL_DIR` is the directory holding this file.

## 1. Choose the target from the code

Answer from the repo, and ask the user only what you can't observe. If they named an area or a bug, start there.

A good target is a promise the code makes (no lost update, a job never runs twice at once, no transition leaves the state table, a parser never reads past its buffer) where a break harms users and tests rarely reach the breaking case. Look at concurrency, retries, leases, idempotency, state machines, parsers, and unsafe code. Pick the strongest target, not the easiest one to verify. State the property and the harm in one plain sentence each.

## 2. Let the question choose the tool

```bash
$SKILL_DIR/scripts/tla_available.sh     # java + tla2tools.jar present? other verifiers on PATH?
```

| Question | Tool |
|---|---|
| Can events happen in a bad order? (races, retries, leases, multi-step updates) | TLA+/TLC over a small model of the design. This is the default, in any language. |
| Does the Rust code behave for every input up to a bound? (overflow, UB, memory safety) | Kani on the real code (`cargo kani`) |
| The same question for C or C++ | CBMC, or an SMT-backed verifier the repo already uses |
| Must the property hold at every size, where a bound isn't enough? | a proof assistant (Lean, Coq) |
| No verifier runs on this language | Prefer the repo's installed property-based testing library on real code with a fixed seed; otherwise use `gen_check.py` for Python. For other languages, break the shipped code and see the test fail. |
| TLA+ isn't installed and installing it isn't an option | `bounded_check.py`: the same breadth-first state exploration, in stdlib Python |

Prefer a tool the repo's developers can install and run, and tell the user which tool you chose and why.

### bounded_check.py

`MODEL.py` defines `init()`, `actions(state) -> [(name, next_state)]`, and `invariants = {name: fn}`. Optional: `terminal`, `describe`, `mutants`, `DEPTH`. Each action is one atomic step of the real code; cite its lines. See [`references/example_model.py`](references/example_model.py): two `todo add` processes and a file lock.

```bash
python3 $SKILL_DIR/scripts/bounded_check.py MODEL.py               # shortest counterexample on failure
python3 $SKILL_DIR/scripts/bounded_check.py MODEL.py --mutants     # every broken variant must be killed
python3 $SKILL_DIR/scripts/bounded_check.py MODEL.py --deadlock --depth 30
python3 $SKILL_DIR/scripts/bounded_check.py MODEL.py --expect-violation <name>   # exit 0 only if it fails
python3 $SKILL_DIR/scripts/bounded_check.py MODEL.py --timeout 60 --call-timeout 5   # defaults: MODEL.CHECK_TIMEOUT or 90 s per run, 10 s per call
```

### gen_check.py

Python-only generated-input checks on shipped code, with stdlib combinators and bounded shrinking. This samples inputs; it is not exhaustive verification. No harness mode or cross-language JSON protocol is provided.

`PROPS.py` imports `ints`, `text`, `lists`, `tuples`, `dicts`, `one_of`, `sampled_from`, and `reject` from `gen_check`. Define `properties = {name: (gen, check)}` and a nonempty `TARGETS = ["todo/store.py:Store.add"]`: repo-relative file, then function or `Class.method`. Import the real code from `--repo`. Only calls during `check(x)` count; imports, module-level frames, setup/reset hooks, and rejected inputs do not. Every declared target must run during accepted checks; the report lists which repo functions ran. Merely importing or copying a function cannot satisfy its declared target.

Each combinator has a shrinker. Signatures: `ints(min_value=-100, max_value=100)`, `text(alphabet=letters_digits_whitespace, min_size=0, max_size=30)`, `lists(elements, min_size=0, max_size=10)`, `tuples(*elements)`, `dicts(keys, values, min_size=0, max_size=10)`, `one_of(*generators)`, `sampled_from(values)`. Dictionary keys must be hashable and sufficiently varied to fill the requested size; generation gives up after a bounded number of attempts.

`check(x)` fails if it raises or returns exactly `False`; assertion-style checks returning `None` pass. Call `reject()` for unmet preconditions. Optional `examples = {name: [inputs...]}` run before generated inputs. Optional `setup()` and `reset()` run in that order before every pinned/generated input and every shrink attempt: clear persistent state there so replay is repeatable. Shrinking stops after at most 500 candidates and reports a reduced counterexample, not a guaranteed minimum.

```bash
python3 $SKILL_DIR/scripts/gen_check.py PROPS.py --repo .                   # default seed 0; 100 generated inputs per property
python3 $SKILL_DIR/scripts/gen_check.py PROPS.py --repo . --seed 42 --examples 500
python3 $SKILL_DIR/scripts/gen_check.py PROPS.py --repo . --expect-fail round_trip  # exit 0 only on this property's counterexample
python3 $SKILL_DIR/scripts/gen_check.py PROPS.py --repo . --json
```

`--examples` counts generated inputs after pinned examples. The seed is printed for replay, including on failure; text mode also prints it to stderr before the first check. Each property draws from its own random stream keyed by the seed and property name, so an earlier property's timeout does not change later properties' generated inputs. A property that otherwise passes but rejected more than 50% of its attempted generated inputs fails with `filter_too_much`; this threshold is our project policy, not Hypothesis's published threshold. A failure or timeout keeps its own result, and a failure reports how many generated inputs were rejected before its counterexample. `--call-timeout S` (default 10) limits each input's hooks and check: a check over it ends that property as `timeout`, printing the input and seed, and the other properties still run. `--timeout S` (default `CHECK_TIMEOUT` in PROPS.py, else 90) limits the whole run. Imports initially use `--timeout` or the 90 s default; after import, `CHECK_TIMEOUT` applies to elapsed time since startup, and an already exhausted budget exits 3 without starting a property. Set `--timeout` to change the import budget as well. Exit codes: 0 pass, 1 property failure / excessive rejection / missing target, 2 usage or infrastructure error (imports, hooks, generation, shrinking); 3 inconclusive: a call/run limit (or Ctrl-C) stopped a check and nothing failed outright. A timeout is not a failure and not a proof. Ctrl-C during shrinking retains the best confirmed counterexample and target coverage; the interrupted run cannot complete an `--expect-fail` proof. `--expect-fail NAME` requires that property's counterexample, all other properties passing, target coverage, and no infrastructure, filter, or timeout result. For an able-to-fail proof, scratch-break the real implementation and restore it in `finally`.

A call timeout during shrinking stops that property's reduction and still runs the other properties. The confirmed failure remains `result: "fail"`, with its best counterexample and accepted target coverage; the timed-out candidate is not a failure and adds no coverage. The property reports `timeout_scope: "call"`, `timeout_phase: "shrink"`, the interrupted `input`, and `incomplete`; the top-level report also records `incomplete`. An unexpected confirmed failure retains exit 1. With `--expect-fail NAME`, a shrink call timeout prevents success and exits 3 unless another property failed unexpectedly (exit 1). A run timeout or Ctrl-C during shrinking still stops the run and exits 3 when all confirmed failures were expected.

Both checkers reserve `SIGALRM` and `signal.setitimer` on the main thread, normally on Unix. Checked code must not use `alarm`/`setitimer` concurrently. One-shot alarms are scheduled no later than the earlier enabled run/call deadline. Bounded searches may reuse an earlier armed alarm and recheck the current deadlines when it fires; each call still has its own deadline, and return-time deadline checks remain in place. Only after expiry, a short one-shot retry is armed before raising the timeout, so a swallowed exception does not disable later signals. Confirmed check results and accepted target coverage are recorded before a pending timeout is delivered; user-defined input and state formatting runs afterward and remains interruptible. If input formatting stops, `gen_check.py` keeps the confirmed failure with an `output_incomplete` marker and an unformatted-input placeholder. On exit, the checker restores the previous handler and timer (accounting for elapsed time) if it still owns the handler. If these facilities are unavailable (including Windows or execution in a worker thread), they warn on stderr that `--timeout` and `--call-timeout` are not enforced. Signal handling may be delayed in native code that does not check signals. Keep an external process deadline for a hard wall-clock bound. In `bounded_check.py`, `--call-timeout` covers actions, invariants and mutant actions; imports, initialization, terminal predicates and trace descriptions use the run limit. A timeout while describing a confirmed violation keeps the finding and serialized state trace. If state serialization itself stops, the trace retains available step/action metadata; unfinished values use `state: null` with `state_incomplete: true`. Both cases mark the violation with `trace_incomplete`. `--json` also returns a timeout report if the run limit fires while importing a model. No new mutant search starts after the run deadline. Each mutant includes `result`, `stopped`, `detail` and explored-state counts; top-level `incomplete` and `not_run` identify unfinished work. Crashing mutants are model errors (exit 2), never survivors. When both deadlines have elapsed at the next timer check, the earlier absolute deadline determines the timeout scope; ties use the run deadline.

**Replay migration and new limits:** Independent per-property random streams can change old sample sequences, so an old `--seed N --expect-fail NAME` may no longer reproduce. Keep the old checker for exact replays, revalidate recorded commands, and pin known counterexamples in `examples`. Time limits are new: both checkers default to 90 s per run / 10 s per call. Deadline enforcement adds overhead, and the run budget covers the main search plus all mutants, so previously completing long checks can become inconclusive (exit 3); a `bounded_check.py` model-call timeout is exit 2. Both accept `--timeout 0` and `--call-timeout 0` to disable the respective limit independently. Configure the module-level run budget with `CHECK_TIMEOUT` (`0` disables it after import), or pass `--timeout`; explicit CLI budgets take precedence.

## 3. The quality bar

- **True to the code.** Every state variable and action points at a `file:line` a reviewer can open. A step the model treats as atomic is atomic in the code. Names come from the repo.
- **Able to fail.** Show each property failing on a broken variant: a removed lock, a reordered step, a dropped check. Keep those variants as `mutants` so `--mutants` re-proves it on every run. A check you have never seen fail proves nothing.
- **Findings stay findings.** If the current code breaks the property, report the counterexample as steps in the code's own terms. Never weaken the property or the model until it passes, and don't change product code unless the user asks.
- **Honest scope.** Say what is verified, under which bounds (clients, depth, input size) and assumptions, and what is left out. A pass covers the model or the bound, not the whole system.
- **Readable without the tool.** The property and the harm fit in one sentence each. A counterexample is a short list of numbered steps.
- **Cheap to rerun.** One command, a pinned tool version, seconds rather than minutes. Give every run a limit shorter than your shell's. Under the signal/thread conditions above, both stdlib checkers stop at `--timeout` (default 90 s, or `CHECK_TIMEOUT` in MODEL.py or PROPS.py) and exit 3 when nothing has failed, with the state count or the input and seed. They stop a covered call at `--call-timeout` (default 10 s), which `bounded_check.py` reports as a model error (exit 2) with the trace to that state and `gen_check.py` as a `timeout` property, or an incomplete reduction of a confirmed failure (exit 3 unless an unexpected counterexample was separately confirmed, which retains exit 1). Exit 3 is inconclusive, not a failure and not a proof: report it as such and rerun with a smaller bound or after a fix.
- **Kept in step.** The generated skill lists the source files the model mirrors. A change to those files means rerunning the check, and updating the model when the change is intended.

## 4. Leave a project-local skill

Write `.claude/skills/verify-<property>/SKILL.md`, with frontmatter whose description says when to use it, and commit the model next to it. From the skill alone, the next agent must be able to tell whether a change touches the modelled code (list the files), run the check with one command, tell a real bug from a modeling error (how to map a trace back to code), and update the model for an intended change. Use real paths and nouns, with no placeholders.

Include a migration note in the generated skill: independent per-property random streams mean old `--seed N --expect-fail NAME` commands may no longer reproduce; revalidate them and preserve pinned counterexamples or the old checker. Document the 90 s per run / 10 s per call defaults and that long checks may now exit 3 (`bounded_check.py` model-call timeouts remain exit 2). Record explicit budgets and explain `--timeout 0` / `--call-timeout 0` as independent opt-outs. Document `CHECK_TIMEOUT` as the module-level run budget, overridden by `--timeout`; imports initially use the CLI budget or 90 s.

## 5. Prove it before handing off

Follow the generated skill once, as the next agent would, on a scratch change that breaks the property in the real code. For example, remove the lock on a scratch branch and update the model to match. The check must catch the break. Then drop the scratch change and confirm the check is back where it started: passing, or failing only on findings you will report. If the check misses the break, fix the skill or the model, not the scratch change.

**Hand off:** what you verified (one sentence), the tool and why, the bounds and assumptions, findings in plain words with their traces, the command, and what the check does not cover. Suggest a second target only if you found a close one.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). `bounded_check.py`, `gen_check.py`, `tla_available.sh`, and the example model are new to poteto-skills.

- S6 pstack `create-formal-verification-skill`: https://github.com/cursor/plugins/blob/main/pstack/skills/create-formal-verification-skill/SKILL.md
- S2 /create-formal-verification-skill launch post: https://x.com/poteto/status/2111926622278521253
- S13 Guide 06, verify and ship ("prove a property with a formal verification skill"): https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md
- Kani's draft agent plugin (kani#5059) @ `49a7a03` says to "give every run a limit" because "shell tools often have a 2 to 10 minute limit", and "A TIMEOUT or OUT_OF_MEMORY verdict is not a failure and not a proof". Its `guarded-run.py` gives "a killed run, which writes none," "an export with `run_state` KILLED or TIMEOUT, so the result is not lost.": https://github.com/model-checking/kani/blob/49a7a03905b5e676e6a4796caf114169a7ae6ad0/agent-plugin/skills/verify-rust/SKILL.md, https://github.com/model-checking/kani/blob/49a7a03905b5e676e6a4796caf114169a7ae6ad0/agent-plugin/scripts/guarded-run.py
- Hypothesis: "A health check is a proactive warning, not an error." Its health checks "warn about performance problems, not correctness errors", so `gen_check.py`'s rejection limit never overwrites a counterexample: https://hypothesis.readthedocs.io/en/latest/reference/api.html
