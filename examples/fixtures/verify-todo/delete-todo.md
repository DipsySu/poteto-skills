# Archive a todo

Archive a completed task while preserving its ID and history.

## Sub-features

- `DELETE /todos/<id>` archives a done record.
- `python3 -m todo rm` archives through the CLI.

## How to get to it (user POV)

On a fresh run, follow create and done recipes to seed records 1 and 2 in status done.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 200` returns ID 1, status archived.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm 2 --data {DATA_DIR}` exits 0 with ID 2, status archived.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http GET /todos --expect 200` shows IDs 1 and 2, both archived.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` records both actions and todos.json containing both archived records.

## Gotchas

An open task cannot skip done; that request returns 409. Archived is terminal. rm retains the record.
