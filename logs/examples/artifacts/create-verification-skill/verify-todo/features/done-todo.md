# Complete a todo

Move an open task to done. A second completion is rejected.

## Sub-features

- `POST /todos/<id>/done` completes an open record.
- `python3 -m todo done` performs the same transition through the CLI.

## How to get to it (user POV)

On a fresh run use the create recipe to seed open records 1 and 2.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo --write done-http http POST /todos/1/done --expect 200 --expect-json 'id=1' --expect-json 'status=done'` completes the first record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo --read-back done-http http GET /todos --expect-json '0.status=done'` observes completion.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo --write done-retry http POST /todos/1/done --expect 409 --expect-json 'error=cannot move done to done'` rejects repeated completion.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo --read-back done-retry http GET /todos --expect-json '0.status=done'` checks that rejection changes nothing.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli --write done-cli --expect-json 'id=2' --expect-json 'status=done' -- python3 -m todo done 2 --data {DATA_DIR}` completes the second record.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli --read-back done-cli --expect-json '1.status=done' -- python3 -m todo list --data {DATA_DIR}` observes the CLI completion.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` retains the requests, assertions, read-backs, and stored todos.json.

## Gotchas

Only open -> done is legal. Completion of a missing ID returns 404. Start a new run before replaying literal IDs.
