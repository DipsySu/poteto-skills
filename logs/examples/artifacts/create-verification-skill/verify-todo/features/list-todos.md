# List todos

Read every saved record, including archived tasks, through either interface.

## Sub-features

- `GET /todos` returns a JSON array.
- `python3 -m todo list` returns the same array.

## How to get to it (user POV)

Run the create recipe first to see records; an empty run returns an empty array.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200` returns the current array.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli -- python3 -m todo list --data {DATA_DIR}` exits 0 with an equal array.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` captures both views and the saved state. With the create recipe seed, both arrays contain IDs 1 and 2.

## Gotchas

Archived records remain visible. Never infer absence from a status alone; compare stable IDs.
