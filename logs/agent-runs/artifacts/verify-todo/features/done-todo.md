# Mark a todo done

A user completes an open task by id, over HTTP (`POST /todos/<id>/done`) or in a terminal (`python3 -m todo done <id>`). The task's status moves from `open` to `done` and the app answers with the updated task. Status only moves forward (`open` → `done` → `archived`): completing a task twice is refused with 409, and an unknown id answers 404 over HTTP or exits 1 on the CLI.

## Sub-features

- `done-todo-http-post`: `POST /todos/<id>/done` over HTTP.
- `done-todo-cli-done`: `python3 -m todo done` in a terminal.
- `done-todo-refusals`: done twice (409) and an unknown id (404, CLI exit 1).

## How to get to it (user POV)

- HTTP API: `POST /todos/<id>/done` on the running server (base URL from `launch`).
- Terminal: `python3 -m todo done <id>`.

## Driving it with control.sh

Preconditions:

- `.claude/skills/verify-todo/scripts/control.sh doctor` exits 0.
- Fresh run from `launch`, then two open todos: run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos '{"title": "verify done-todo"}' --expect 201` (id 1) and `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify done-todo'` (id 2).

- **HTTP POST.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 200`. Expect `200` and `{"id": 1, "status": "done", "title": "verify done-todo"}`.
- **CLI done.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 2`. Expect exit 0 and stdout `{"id": 2, "status": "done", "title": "verify done-todo"}`.
- **Refusals.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 409`. Expect `{"error": "cannot move done to done"}`. Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/99/done --expect 404`. Expect `{"error": "todo not found"}`. Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 99`. Expect exit 1, no stdout, and stderr `todo: 99`.
- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http GET /todos --expect 200` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. The read-back is exactly `[{"id": 1, "status": "done", "title": "verify done-todo"}, {"id": 2, "status": "done", "title": "verify done-todo"}]` (the refusals changed nothing), and `NNN-data-snapshot/todos.json` holds the same array.

## Gotchas

- The CLI refusal exits 1, so its drive also exits 1; that is the expected result, not a harness failure. HTTP refusals need `--expect 409` or `--expect 404`.
- The CLI's error for an unknown id is just the id (`todo: 99`), not a sentence.
- Ids are never reused: archived rows keep theirs, so a todo created after an archive gets the next id (3 after ids 1 and 2).
- Every CLI call needs the run's data dir (`{DATA_DIR}`); without it the CLI touches its default data location instead of the scratch one.
