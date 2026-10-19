# Todo contribution rules

| Rule | Enforced by |
|---|---|
| Persist JSON only through store.py; use Store.add or Store.transition | scripts/lint.sh |
| Status changes follow open -> done -> archived | tests/test_todo.py |
| Keep error messages concise and useful | prose in this document |
