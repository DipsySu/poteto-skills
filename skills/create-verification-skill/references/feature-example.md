# Mark a todo done

A user marks an open todo as done from the HTTP API or the terminal. The todo keeps its id and title, its status becomes `done`, and it still appears in the list.

## Sub-features

- `done-todo-http`: `POST /todos/<id>/done` marks one todo done.
- `done-todo-cli`: `python3 -m todo done <id>` does the same from a terminal.
- `done-todo-twice`: marking a done todo again is rejected and changes nothing.

## How to get to it (user POV)

- HTTP API: `POST /todos/<id>/done` on the running server.
- Terminal: `python3 -m todo --data <dir> done <id>`.

## Driving it with control.sh

Preconditions:

- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0.
- Todo `1` exists: on a fresh run, `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos '{"title": "Buy milk"}'` returns `201` with `"id": 1`.

- **HTTP.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 200`. The body is `{"id": 1, "status": "done"}`.
- **Twice.** Run the same command again with `--expect 409`. The body names the illegal transition `done -> done`.
- **CLI.** Create todo `2` the same way, then run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo --data {DATA_DIR} done 2`. Exit code `0`; stdout lists todo `2` with `"status": "done"`.
- **Proof.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http GET /todos` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. The list shows todos `1` and `2` as `done`, and the snapshot's `todos.json` has the same statuses.

## Gotchas

- Ids start at `1` only on an empty data dir. On a reused run, read the id from the create response.
- The CLI defaults to `./data` without `--data {DATA_DIR}` and would edit the developer's real list.
- A `200` alone proves nothing about storage; the read-back and the snapshot are the proof.
