# todo verification map

The maintained source for verifying what todo users can do. Read this index before driving the app, then follow the matching feature file as the recipe.

## Baseline preconditions

- Launch with `.claude/skills/verify-todo/scripts/control.sh launch`: empty data dir, its own port, JSON with run_id, base_url, data_dir.
- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0 before the first drive.
- Never drive an instance this run did not start.
- None at launch. Each mutation recipe names its seed command; a fresh store starts IDs at 1.

## Driving conventions

- Run every command from the repo root, literally as written.
- HTTP: `.claude/skills/verify-todo/scripts/control.sh drive --feature <id> http METHOD PATH ['JSON'] [--expect STATUS]`.
- CLI: `.claude/skills/verify-todo/scripts/control.sh drive --feature <id> cli -- <command>`. `{DATA_DIR}`, `{PORT}`, `{BASE_URL}`, and `{RUN_ID}` in arguments become this run's values.
- Add repeatable `--expect-json 'PATH=VALUE'`, `--expect-text TEXT`, or `--expect-no-text TEXT`. JSON paths use dots and array indexes; `.` selects the root. VALUE is JSON, or a string if it is not JSON. Missing paths fail, including when VALUE is null; JSON types must match.
- HTTP `--expect STATUS` and CLI `--expect-exit N` (default 0) combine with every assertion. Harness flags go before `http`/`cli`, after the HTTP path, or before the CLI `--`; application flags go after `--`.
- Start each recipe from the state its Preconditions name. Use stable handles: route paths, subcommand names, JSON fields.

## Proof and skip reporting

- Each drive records the action and its result in `artifacts/verify/<run-id>/`.
- Label each HTTP or CLI mutation `--write KEY`, then use an asserted GET or CLI read/list with `--read-back KEY` before the next write with that key, then `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. The read must show the changed (or unchanged after rejection) stored value. HTTP reads must address the written resource or a non-root parent path; a health check cannot prove a todo write. Use a distinct key per write.
- CLI read commands declare `--read-only`; `--read-back KEY` also declares a read. These declarations describe the command's real behavior; the harness cannot infer arbitrary CLI semantics.
- Record the feature id with every artifact (`--feature`).
- Report an unreachable path with the attempted command and the unmet precondition. Never report it as verified through a different path.

## Feature entry contract

Each feature file has an H1, one paragraph of user-visible behavior, then exactly these H2s in order: `Sub-features`, `How to get to it (user POV)`, `Driving it with control.sh`, `Gotchas`. Keep implementation details out: user paths, stable handles, required state, commands, observable proof.

## Features

- [Create a todo](./create-todo.md): `POST /todos`, `python3 -m todo add`.
- [List todos](./list-todos.md): `GET /todos`, `python3 -m todo list`.
- [Mark a todo done](./done-todo.md): `POST /todos/<id>/done`, `python3 -m todo done`.
- [Delete a todo](./delete-todo.md): `DELETE /todos/<id>`, `python3 -m todo rm`.
