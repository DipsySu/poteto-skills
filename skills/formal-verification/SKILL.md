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

`--examples` counts generated inputs after pinned examples. The seed is printed for replay, including on failure. Rejecting more than 50% of attempted generated inputs fails with `filter_too_much`; this threshold is our project policy, not Hypothesis's published threshold. Exit codes: 0 pass, 1 property failure / excessive rejection / missing target, 2 usage or infrastructure error (imports, hooks, generation, shrinking). `--expect-fail NAME` requires that property's counterexample, all other properties passing, target coverage, and no infrastructure or filter failure. For an able-to-fail proof, scratch-break the real implementation and restore it in `finally`.

## 3. The quality bar

- **True to the code.** Every state variable and action points at a `file:line` a reviewer can open. A step the model treats as atomic is atomic in the code. Names come from the repo.
- **Able to fail.** Show each property failing on a broken variant: a removed lock, a reordered step, a dropped check. Keep those variants as `mutants` so `--mutants` re-proves it on every run. A check you have never seen fail proves nothing.
- **Findings stay findings.** If the current code breaks the property, report the counterexample as steps in the code's own terms. Never weaken the property or the model until it passes, and don't change product code unless the user asks.
- **Honest scope.** Say what is verified, under which bounds (clients, depth, input size) and assumptions, and what is left out. A pass covers the model or the bound, not the whole system.
- **Readable without the tool.** The property and the harm fit in one sentence each. A counterexample is a short list of numbered steps.
- **Cheap to rerun.** One command, a pinned tool version, seconds rather than minutes.
- **Kept in step.** The generated skill lists the source files the model mirrors. A change to those files means rerunning the check, and updating the model when the change is intended.

## 4. Leave a project-local skill

Write `.claude/skills/verify-<property>/SKILL.md`, with frontmatter whose description says when to use it, and commit the model next to it. From the skill alone, the next agent must be able to tell whether a change touches the modelled code (list the files), run the check with one command, tell a real bug from a modeling error (how to map a trace back to code), and update the model for an intended change. Use real paths and nouns, with no placeholders.

## 5. Prove it before handing off

Follow the generated skill once, as the next agent would, on a scratch change that breaks the property in the real code. For example, remove the lock on a scratch branch and update the model to match. The check must catch the break. Then drop the scratch change and confirm the check is back where it started: passing, or failing only on findings you will report. If the check misses the break, fix the skill or the model, not the scratch change.

**Hand off:** what you verified (one sentence), the tool and why, the bounds and assumptions, findings in plain words with their traces, the command, and what the check does not cover. Suggest a second target only if you found a close one.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). `bounded_check.py`, `gen_check.py`, `tla_available.sh`, and the example model are new to poteto-skills.

- S6 pstack `create-formal-verification-skill`: https://github.com/cursor/plugins/blob/main/pstack/skills/create-formal-verification-skill/SKILL.md
- S2 /create-formal-verification-skill launch post: https://x.com/poteto/status/2111926622278521253
- S13 Guide 06, verify and ship ("prove a property with a formal verification skill"): https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md
