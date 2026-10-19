# Create a todo

A user adds a task by title, over the HTTP API (`POST /todos` with a JSON `title`) or in a terminal (`python3 -m todo add <title>`). The app trims the title, gives the task the next integer id (1 on an empty store), stores it with status `open`, and answers with the new task as JSON. HTTP and CLI share one data dir, so a task created either way shows up in both lists. A blank title or an empty body is rejected and nothing is stored.

## Sub-features

- `create-todo-http-post`: `POST /todos` over HTTP.
- `create-todo-cli-add`: `python3 -m todo add` in a terminal.
- `create-todo-validation`: a blank title or an empty body is rejected with 409.

## How to get to it (user POV)

- HTTP API: `POST /todos` on the running server (base URL from `launch`).
- Terminal: `python3 -m todo add <title>`.

## Driving it with control.sh

Preconditions:

- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0.
- Fresh run from `launch` (empty data dir).

- **HTTP POST.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201`. Expect `201` and `{"id": 1, "title": "verify create-todo", "status": "open"}`.
- **CLI add.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify create-todo'`. Expect exit 0 and stdout `{"id": 2, "title": "verify create-todo", "status": "open"}`.
- **Validation.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "  "}' --expect 409`. Expect `{"error": "title must be a nonempty string"}`. Then run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos --expect 409` (no body). Expect `{"error": "body must be 1..65536 bytes"}`.
- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http GET /todos --expect 200` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. The read-back is exactly `[{"id": 1, "status": "open", "title": "verify create-todo"}, {"id": 2, "status": "open", "title": "verify create-todo"}]` (the rejected posts added nothing), and `NNN-data-snapshot/todos.json` holds the same array.

## Gotchas

- Validation errors answer 409, not 400. `drive` exits 1 on any 4xx unless you pass the expected status with `--expect`.
- Titles are trimmed (`"  after archive  "` is stored as `"after archive"`) and duplicates are allowed. Identify rows by `id`, never by title.
- The create response lists keys as `id, title, status`; reads and the stored file use sorted keys (`id, status, title`). Compare parsed JSON, not strings.
- Every CLI call needs the run's data dir (`{DATA_DIR}`); without it the CLI touches its default data location instead of the scratch one.
