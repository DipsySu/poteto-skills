# Create a todo

Create a titled task; the API and CLI return its stable ID and status open.

## Sub-features

- `POST /todos` creates a todo from a JSON title.
- `python3 -m todo add` creates the same record through the CLI.

## How to get to it (user POV)

Launch with `.claude/skills/verify-todo/scripts/control.sh launch`, then doctor. Start with an empty store.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title":"verify HTTP"}' --expect 201` returns ID 1, title verify HTTP, status open.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli -- python3 -m todo add 'verify CLI' --data {DATA_DIR}` exits 0 and returns ID 2, status open.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http GET /todos --expect 200` returns both records, matching the CLI output.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` retains the requests, responses and todos.json containing both records.

## Gotchas

Titles must be nonempty strings. IDs begin at 1 only on a fresh run. Always use this run's data directory.
