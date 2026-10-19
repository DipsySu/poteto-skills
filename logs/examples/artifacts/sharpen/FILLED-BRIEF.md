# Brief: prevent lost todo writes and invalid transitions

## In my own words

Keep the CLI and HTTP API on the same JSON persistence path. Reject skipped and repeated status transitions.
Success means the real past mistakes fail deterministic checks and the current implementation passes.

## How a top expert would approach it

Read todo/store.py, the two caller modules, tests/test_todo.py, and the recorded review comments.
Compare the unsafe history to HEAD. Put mutation ownership and locking in Store, then prove the lint on history.
Use a finite concurrent-writer model to explore interleavings the serial unit tests do not cover.

## Specification check

The status machine and API contract are fixed by the example spec. This small fixture needs no external database.
Choose the isolated data directory and free loopback port for each live run. A sandbox denying sockets leaves the live pass failed.

## Outside check

- Check: run the application's regression tests in the built todo repository.
- Command or link: `python3 -m unittest -q`
- Pass condition: all seven regression tests pass, including skipped and repeated completion failures.
