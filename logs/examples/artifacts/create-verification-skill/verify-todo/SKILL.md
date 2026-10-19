---
name: verify-todo
description: "Launch todo from an empty data dir, drive its HTTP API and CLI the way a user does, and keep evidence under artifacts/verify/. Use for /verify-todo, before claiming a change to todo works, or to reproduce a bug in the running app."
---

# Verify todo

Prove behavior on the running app, not a proxy. Every step goes through `.claude/skills/verify-todo/scripts/control.sh`, run from the repo root. Feature recipes live in [features/README.md](features/README.md).

## Launch

- `.claude/skills/verify-todo/scripts/control.sh launch` starts `python3 -m todo serve --data "$DATA_DIR" --port "$PORT"` from the repo root on a free port (or `VERIFY_PORT`), with an empty data dir under `${VERIFY_STATE_DIR:-/tmp/verify-todo-$UID}/<run-id>/data`.
- Ready = `GET /health` returns 2xx, then doctor proves it is usable with `GET /todos`.
- It prints JSON: run_id, pid, port, base_url, data_dir, evidence_dir. The server log is `artifacts/verify/<run-id>/launch.log`.
- It refuses to start while an earlier run is alive. Teardown is `cleanup`.
- Build first: none (interpreted). Tests (they do not replace driving): `python3 -m unittest`.

## Doctor

`.claude/skills/verify-todo/scripts/control.sh doctor` is read-only and exits 1 on any failed check: the recorded pid is alive and is still the process this run started, the port answers, `GET /health` is 2xx, `GET /todos` is 2xx, the data dir and evidence dir exist. Run it before the first drive and again after any failed drive.

## Drive

- HTTP: `.claude/skills/verify-todo/scripts/control.sh drive --feature <id> http METHOD PATH ['JSON'] [--expect STATUS]`.
- CLI: `.claude/skills/verify-todo/scripts/control.sh drive --feature <id> cli -- python3 -m todo <subcommand> ...`. `{DATA_DIR}`, `{PORT}`, `{BASE_URL}`, `{RUN_ID}` in arguments become this run's values.
- One recipe per feature in `features/`. Drive the public routes and commands only.

## Evidence

- Every drive writes `artifacts/verify/<run-id>/NNN-<feature>-<http|cli>.json`: the request or argv, the status or exit code, the response body or stdout/stderr, and timing.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` snapshots the data dir (the side effect) and writes `manifest.json` with a sha256 per file.
- Proof standard: the real user path (no internal setters or test-only endpoints); the action and the resulting state, read back through a second view; side effects checked, not inferred; mocks only at production boundaries. If a dry-run or test mode is used, observe what it really skips.

## Cleanup

`.claude/skills/verify-todo/scripts/control.sh cleanup` stops only the process group this run started (pid file plus recorded start identity, never by name), deletes the scratch state and data dir, and keeps `artifacts/verify/<run-id>/`. It prints the evidence path and fails if the evidence is gone.

## Helpers

- `scripts/control.sh`: `launch | doctor | drive | evidence | cleanup | status | prove`. `.claude/skills/verify-todo/scripts/control.sh --help` lists the arguments.
- One-shot proof: `.claude/skills/verify-todo/scripts/control.sh prove --feature <id> http GET /todos` runs launch, doctor, drive, evidence, and cleanup, then checks the evidence survived.
- Contract check: `python3 /home/box/developer/poteto-skills/skills/create-verification-skill/scripts/check_verify_skill.py .claude/skills/verify-todo`.
- Drift check: `python3 /home/box/developer/poteto-skills/skills/maintain-verification-skill/scripts/drift_check.py .claude/skills/verify-todo --repo .`.
