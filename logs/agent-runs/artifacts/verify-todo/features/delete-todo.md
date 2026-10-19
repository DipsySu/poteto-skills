# Delete a todo

"Delete" archives: a user retires a finished task by id, over HTTP (`DELETE /todos/<id>`) or in a terminal (`python3 -m todo rm <id>`), and its status moves from `done` to `archived`. The row is not removed; it stays in every list with status `archived` and keeps its id. Only `done` tasks can be archived: an `open` or already archived task is refused with 409 (CLI exit 1).

## Sub-features

- `delete-todo-http-delete`: `DELETE /todos/<id>` over HTTP.
- `delete-todo-cli-rm`: `python3 -m todo rm` in a terminal.
- `delete-todo-refusals`: archiving an open or an archived task (409, CLI exit 1).

## How to get to it (user POV)

- HTTP API: `DELETE /todos/<id>` on the running server (base URL from `launch`).
- Terminal: `python3 -m todo rm <id>`.

## Driving it with control.sh

Preconditions:

- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0.
- Fresh run from `launch`, then two done todos and one open todo: run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos '{"title": "verify delete-todo"}' --expect 201` (id 1), `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify delete-todo'` (id 2), `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos/1/done --expect 200`, `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo done --data {DATA_DIR} 2`, and `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos '{"title": "still open"}' --expect 201` (id 3).

- **HTTP DELETE.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 200`. Expect `200` and `{"id": 1, "status": "archived", "title": "verify delete-todo"}`.
- **CLI rm.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 2`. Expect exit 0 and stdout `{"id": 2, "status": "archived", "title": "verify delete-todo"}`.
- **Refusals.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/3 --expect 409`. Expect `{"error": "cannot move open to archived"}`. Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 409`. Expect `{"error": "cannot move archived to archived"}`. Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 3`. Expect exit 1 and stderr `todo: cannot move open to archived`.
- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http GET /todos --expect 200` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. The read-back is exactly `[{"id": 1, "status": "archived", "title": "verify delete-todo"}, {"id": 2, "status": "archived", "title": "verify delete-todo"}, {"id": 3, "status": "open", "title": "still open"}]`: archived rows are still listed, and `NNN-data-snapshot/todos.json` holds the same array.

## Gotchas

- `DELETE` and `rm` archive; they never remove a row. Don't expect a shorter list or a 404 afterwards.
- An open task must be marked done first (see [Mark a todo done](./done-todo.md)); archiving it directly is the refusal path, not a harness failure.
- Every CLI call needs the run's data dir (`{DATA_DIR}`); without it the CLI touches its default data location instead of the scratch one.
