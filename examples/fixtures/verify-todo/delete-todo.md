# Archive a todo

Archive a completed task while preserving its ID and history.

## Sub-features

- `DELETE /todos/<id>` archives a done record.
- `python3 -m todo rm` archives through the CLI.

## How to get to it (user POV)

On a fresh run, follow create and done recipes to seed records 1 and 2 in status done.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo --write archive-http http DELETE /todos/1 --expect 200 --expect-json 'id=1' --expect-json 'status=archived'` archives the first record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo --read-back archive-http http GET /todos --expect-json '0.status=archived'` observes the archived state.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli --write archive-cli --expect-json 'id=2' --expect-json 'status=archived' -- python3 -m todo rm 2 --data {DATA_DIR}` archives the second record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli --read-back archive-cli --expect-json '1.status=archived' -- python3 -m todo list --data {DATA_DIR}` observes the CLI archive.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` retains the requests, assertions, read-backs, and stored todos.json.

## Gotchas

An open task cannot skip done; that request returns 409. Archived is terminal. rm retains the record.
