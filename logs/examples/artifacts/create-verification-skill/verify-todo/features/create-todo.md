# Create a todo

Create a titled task; the API and CLI return its stable ID and status open.

## Sub-features

- `POST /todos` creates a todo from a JSON title.
- `python3 -m todo add` creates the same record through the CLI.

## How to get to it (user POV)

Launch with `.claude/skills/verify-todo/scripts/control.sh launch`, then doctor. Start with an empty store.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo --write create-http http POST /todos '{"title":"verify HTTP"}' --expect 201 --expect-json 'id=1' --expect-json 'status=open'` returns the new record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo --read-back create-http http GET /todos --expect 200 --expect-json '0.title=verify HTTP' --expect-json '0.status=open'` observes the stored title and status.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli --write create-cli --expect-json 'id=2' --expect-json 'status=open' -- python3 -m todo add 'verify CLI' --data {DATA_DIR}` returns the second record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli --read-back create-cli --expect-json '1.title=verify CLI' --expect-json '1.status=open' -- python3 -m todo list --data {DATA_DIR}` observes the CLI write.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` retains the requests, assertions, read-backs, and stored todos.json.

## Gotchas

Titles must be nonempty strings. IDs begin at 1 only on a fresh run. Always use this run's data directory.
