# List todos

Read every saved record, including archived tasks, through either interface.

## Sub-features

- `GET /todos` returns a JSON array.
- `python3 -m todo list` returns the same array.

## How to get to it (user POV)

Run the create recipe first to seed records 1 and 2. On an empty run instead, assert the root with `--expect-json '.=[]'`.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200 --expect-json '0.id=1' --expect-json '1.id=2'` shows the seeded IDs.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli --read-only --expect-json '0.id=1' --expect-json '1.id=2' -- python3 -m todo list --data {DATA_DIR}` shows the same IDs.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` retains the requests, assertions, read-backs, and stored todos.json.

## Gotchas

Archived records remain visible. Never infer absence from a status alone; compare stable IDs.
