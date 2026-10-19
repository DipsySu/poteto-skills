# List todos

A user sees every task with its `id`, `title`, and `status` (`open`, `done`, or `archived`) as one JSON array in creation order. Archived tasks stay in the list. `GET /todos` and `python3 -m todo list` read the same store and print the same array; an empty store prints `[]`.

## Sub-features

- `list-todos-http-get`: `GET /todos` over HTTP.
- `list-todos-cli-list`: `python3 -m todo list` in a terminal.

## How to get to it (user POV)

- HTTP API: `GET /todos` on the running server (base URL from `launch`).
- Terminal: `python3 -m todo list`.

## Driving it with control.sh

Preconditions:

- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0.
- Fresh run from `launch` (empty data dir).

- **Empty store.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200`. Expect `200` and `[]`.
- **Seed one todo.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http POST /todos '{"title": "verify list-todos"}' --expect 201`. Expect `201` and id 1.
- **HTTP GET.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200`. Expect `200` and `[{"id": 1, "status": "open", "title": "verify list-todos"}]`.
- **CLI list.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli -- python3 -m todo list --data {DATA_DIR}`. Expect exit 0 and stdout `[{"id": 1, "status": "open", "title": "verify list-todos"}]`.
- **Proof.** The two views must agree. Run `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`; `NNN-data-snapshot/todos.json` holds the same array.

## Gotchas

- Before the first write the store has no `todos.json`: reads still answer `[]`, and a snapshot taken then holds only `todos.lock`.
- Rows keep creation order and archived rows are listed too. There is no filter parameter; filter by `status` yourself.
- Every CLI call needs the run's data dir (`{DATA_DIR}`); without it the CLI touches its default data location instead of the scratch one.
