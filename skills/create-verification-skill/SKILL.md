---
name: create-verification-skill
description: "Generate a project-local verify-<app> skill: a control.sh harness that launches the real app from an empty data dir, drives it the way a user does (HTTP, CLI), and keeps evidence, plus a feature map with one recipe per feature. Use for /create-verification-skill, \"make a verify skill for this repo\", or when agents have no scripted way to prove a change works in the running app."
---

# Create a verification skill

Give agents hands and eyes: one committed, scripted way to launch the app, drive a feature the way a user would, and keep proof. Write the output for the next agent, who reads it cold, mid-task, and has never seen the app.

`$SKILL_DIR` is the directory holding this file. Scripts are Python 3 stdlib and bash; each has `--help`.

## 1. Interview the repo, not the user

```bash
python3 $SKILL_DIR/scripts/scan_repo.py .        # --json for the full inventory
```

Check each answer against the code. Ask the user only what the repo can't tell you.

- **Surface:** what a user touches (HTTP API, CLI, web UI, desktop app). Pick the primary one and note the rest.
- **Run:** the repo's own start command, ports, env vars, seed data, auth.
- **Drive:** existing harnesses first (e2e specs, curl-able routes, PTY scripts). Otherwise plain HTTP for services and a fresh process per CLI call.
- **Observe:** response bodies, stdout/stderr/exit codes, files written, rows inserted, logs.
- **Isolate:** can two instances run side by side (ports, data dirs)? If not, the skill says so and refuses to drive a shared instance.

If the checkout doesn't build or start, fix that or report it precisely before generating anything. A skill written against a broken base teaches wrong steps.

## 2. Scaffold

```bash
python3 $SKILL_DIR/scripts/scaffold.py . --app <name>    # --skills-dir .cursor/skills for Cursor
```

This writes `.claude/skills/verify-<name>/`:

- `SKILL.md` with Launch, Doctor, Drive, Evidence, Cleanup, and Helpers sections.
- `features/README.md` plus one file per detected feature, each with four H2s in order: `Sub-features`, `How to get to it (user POV)`, `Driving it with control.sh`, `Gotchas`.
- `verify-<app>/scripts/control.sh` with `launch | doctor | drive | evidence | cleanup | status | prove`. It keeps a pid file, kills only the process group it started after checking the leader's recorded start identity, and writes evidence to `artifacts/verify/<run-id>/`.

Anything the scan couldn't establish is a `TODO(verify):` marker. [`references/feature-example.md`](references/feature-example.md) shows a finished recipe.

## 3. Replace every TODO with real commands

```bash
grep -rn 'TODO(verify)' .claude/skills/verify-<name>
```

- Run each command before you write it down, and record the observed status or output as the expected result. Replace `<id>`-style placeholders in recipe commands with the values you observed; `check_verify_skill.py` fails on them.
- Launch must reach a usable state from an empty data dir, including migrations and seed data. Ready means usable, not just "the process answers". Keep a real read (`READ_PATH`) in doctor.
- Use stable handles: route paths, subcommand names, JSON fields, ARIA roles. Never coordinates or tab order.
- Evidence follows the real user path: the action, then the resulting state through a second view, then the side effect (`evidence --copy-data`). Mock only at production boundaries. Check what a dry-run mode really skips by observing it.
- Assert bodies or stdout with repeatable `--expect-json 'PATH=VALUE'`, `--expect-text TEXT`, or `--expect-no-text TEXT`. Dot paths include array indexes; `.` is the root. Missing paths fail; JSON types must match. `--expect STATUS` and CLI `--expect-exit N` (default 0) combine with the assertions. Put CLI harness flags before `--`; application arguments follow it.
- Label each HTTP or CLI write `--write KEY`; follow it with an asserted GET or CLI read/list using `--read-back KEY` and the same feature ID. Assert the stored value, including unchanged state after rejection. HTTP read-backs must address that resource or a non-root parent path. Declare other CLI reads `--read-only`; these declarations must match the command's real behavior.
- If the seed data lacks a journey's precondition, the journey fails and the reason goes in that feature's Gotchas. Never weaken a journey to make it pass.
- An irrelevant missing asset that blocks startup may be created by the skill, marked as verification scaffolding, and removed in cleanup.
- Extend `control.sh` when the surface needs it (a browser or PTY subcommand). Keep every helper executable and show its invocation in SKILL.md.

## 4. Prove it end to end once

```bash
C=.claude/skills/verify-<name>/scripts/control.sh
$C launch
$C doctor
# Follow the feature recipe: a --write KEY drive, then an asserted --read-back KEY drive.
$C evidence --copy-data
$C cleanup                                 # also after failure
ls artifacts/verify/<run-id>/              # the evidence must survive cleanup
$C prove --feature <id> http GET <route> --expect-json 'PATH=VALUE'  # read-only shortcut
```

Drive one mapped feature exactly as its recipe says. Fix what fails, and run `cleanup` after every failed attempt so no process or port is left behind. A skill that was never executed is a draft, not a deliverable. If you proved it in a stand-in (a container instead of the real host), list the setup steps the stand-in did that the real target won't.

## 5. Gate and hand off

```bash
python3 $SKILL_DIR/scripts/check_verify_skill.py .claude/skills/verify-<name> --require-proof artifacts/verify    # must exit 0
```

The gate requires asserted recipes and second views for HTTP and CLI writes. `--require-proof` opens manifested drive records: execution and nonempty assertions must pass, and writes must have asserted read-backs. Older harnesses need `control.sh` regenerated and their CONFIG reapplied. The scaffold also writes `FEATURE_MAP.md`; keep it in sync with `features/README.md`.

Commit the skill so every person and agent drives the app the same way, and add `artifacts/` to `.gitignore`. Point the user at /maintain-verification-skill to keep the map honest. Suggest a cadence only if asked.

**Reply:** the surface you chose, the prove command and its output, the evidence path, features mapped vs. detected, and anything left unverified with the reason.

## Sources

Adapted from pstack by Lauren Tan (MIT, © 2026 Lauren Tan). The scripts are new to poteto-skills.

- S4 pstack `create-verification-skill` (+ feature-map example): https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md
- S13 Guide 06, verify and ship (control CLI traits, prove before handoff): https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md
- S17 principle-prove-it-works: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-prove-it-works/SKILL.md
- S18 principle-build-the-lever: https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md
- S1 inverted pyramid post ("use it minimally, but do use /create-verification-skill"): https://x.com/poteto/status/2111133080526495985
