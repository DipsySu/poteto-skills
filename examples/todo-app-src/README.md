# Todo example

Run `python3 -m unittest -q`. Start the API with `python3 -m todo serve --port 8765 --data data`.
Use `python3 -m todo add 'task' --data data`, then `list`, `done ID`, and `rm ID` with the same data flag.

GET /health returns readiness. GET/POST /todos lists/creates tasks. POST /todos/<id>/done completes an open task.
DELETE /todos/<id> archives a done task. Invalid transitions return 409; missing tasks return 404.
All writes lock and atomically replace the JSON file through store.py. Archived records retain their IDs.
The history intentionally includes unsafe direct writes and two status regressions; HEAD repairs them.
