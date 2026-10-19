# Complete a todo

Move an open task to done. A second completion is rejected.

## Sub-features

- `POST /todos/<id>/done` completes an open record.
- `python3 -m todo done` performs the same transition through the CLI.

## How to get to it (user POV)

On a fresh run use the create recipe to seed open records 1 and 2.

## Driving it with control.sh

- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 200` returns ID 1, status done.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 409` reports an illegal repeated completion.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done 2 --data {DATA_DIR}` exits 0 with ID 2, status done.
- `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http GET /todos --expect 200` shows both records done.
- `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data` preserves the rejected retry and the stored done states.

## Gotchas

Only open -> done is legal. Completion of a missing ID returns 404. Start a new run before replaying literal IDs.
