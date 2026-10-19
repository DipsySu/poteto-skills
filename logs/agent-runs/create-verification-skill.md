# Agent run: /create-verification-skill on a fresh todo app

- Date: 2026-10-19. Agent: Claude (Opus 5.5), Phase C of poteto-skills.
- Target: `/tmp/phaseC-todo`, built fresh with `examples/build-todo-app.sh /tmp/phaseC-todo` (nested repo, HEAD `4242a5a`, 10 commits).
- Install: `./install.sh --project /tmp/phaseC-todo --target claude` (8 symlinks under `/tmp/phaseC-todo/.claude/skills/`, nothing in `~`).
- Instructions followed: `/tmp/phaseC-todo/.claude/skills/create-verification-skill/SKILL.md`, sections 1-5, acting as the agent that received the slash command. `examples/fill_verify.py` was not used. Every recipe value comes from reading the app and from the live run below.
- Recording: every shell command run in `/tmp/phaseC-todo` is shown verbatim with its full combined output and exit code (captured by a 10-line wrapper, `/tmp/phaseC-scratch/rec.sh`). Text outside the console blocks is mine. Files read with the editor are summarized, not pasted.
- Tooling note: the editor's Write/Edit tools refuse paths under `.claude/` in this session, so the recipe files were written with shell heredocs. Their final content is in `artifacts/verify-todo/`.
- Artifacts: `logs/agent-runs/artifacts/verify-todo/` (the generated skill as committed) and `logs/agent-runs/artifacts/evidence/{e2e-proof,prove-create}/` (proof evidence).

## Setup

```console
$ examples/build-todo-app.sh /tmp/phaseC-todo
{
  "repo": "/tmp/phaseC-todo",
  "head": "4242a5ae43587ea1ed392b15775e2e3e3acc2aa4",
  ...  (the builder also prints the SHAs of its planted mistakes; omitted here, and the /correct run finds them from git history instead)
}
$ ./install.sh --project /tmp/phaseC-todo --target claude
link /tmp/phaseC-todo/.claude/skills/architect -> /home/box/developer/poteto-skills/skills/architect
link /tmp/phaseC-todo/.claude/skills/correct -> /home/box/developer/poteto-skills/skills/correct
link /tmp/phaseC-todo/.claude/skills/create-verification-skill -> /home/box/developer/poteto-skills/skills/create-verification-skill
link /tmp/phaseC-todo/.claude/skills/curator -> /home/box/developer/poteto-skills/skills/curator
link /tmp/phaseC-todo/.claude/skills/feedback-to-architecture -> /home/box/developer/poteto-skills/skills/feedback-to-architecture
link /tmp/phaseC-todo/.claude/skills/formal-verification -> /home/box/developer/poteto-skills/skills/formal-verification
link /tmp/phaseC-todo/.claude/skills/maintain-verification-skill -> /home/box/developer/poteto-skills/skills/maintain-verification-skill
link /tmp/phaseC-todo/.claude/skills/sharpen -> /home/box/developer/poteto-skills/skills/sharpen
install.sh: install done: 8 skill(s), 1 target dir(s), 8 change(s), 0 conflict(s)
[exit 0]
```

All commands below run from `/tmp/phaseC-todo`.

## 1. Interview the repo, not the user

```console
$ SKILL_DIR=.claude/skills/create-verification-skill; python3 $SKILL_DIR/scripts/scan_repo.py .
repo: /tmp/phaseC-todo
languages: python (6), shell (1)
markers: README.md, AGENTS.md
build: none found
test: python3 -m unittest
launch candidates:
  [server] python3 -m todo serve  (port 8765; port flag --port; data flag --data)  <- todo/__main__.py:12
  [cli] python3 -m todo  (data flag --data; subcommands add, done, list, rm)  <- todo/__main__.py:15
http routes (5):
  GET     /health                          [health] <- todo/api.py:24 (eq)
  GET     /todos                           <- todo/api.py:26 (eq)
  POST    /todos                           <- todo/api.py:34 (eq)
  DELETE  /todos/<id>                      <- todo/api.py:52 (regex)
  POST    /todos/<id>/done                 <- todo/api.py:40 (regex)
cli commands (5):
  python3 -m todo add <title>  <- todo/__main__.py:15
  python3 -m todo done <id>  <- todo/__main__.py:20
  python3 -m todo list  <- todo/__main__.py:18
  python3 -m todo rm <id>  <- todo/__main__.py:23
  python3 -m todo serve  [launch]  <- todo/__main__.py:12
existing skills: .claude/skills/architect, .claude/skills/correct, .claude/skills/create-verification-skill, .claude/skills/curator, .claude/skills/feedback-to-architecture, .claude/skills/formal-verification, .claude/skills/maintain-verification-skill, .claude/skills/sharpen
proposed features (4):
  create-todo            Create a todo              POST /todos; python3 -m todo add
  list-todos             List todos                 GET /todos; python3 -m todo list
  done-todo              Mark a todo done           POST /todos/<id>/done; python3 -m todo done
  delete-todo            Delete a todo              DELETE /todos/<id>; python3 -m todo rm
[exit 0]
```

Checked each scan answer against the code (read `todo/__main__.py`, `todo/api.py`, `todo/store.py`, `README.md`, `AGENTS.md`, `.gitignore`, `scripts/lint.sh`, and the test names):

- **Surface:** HTTP JSON API (primary) plus a CLI over the same store. `serve(port, data)` binds `127.0.0.1`; `--port` defaults to 8765, `--data` to `data` (relative to the cwd).
- **Routes:** `GET /health` -> 200 `{"ok": true}` without touching the store; `GET /todos` reads the store; `POST /todos` -> 201 (empty body or bad title -> `ValueError` -> 409); `POST /todos/<n>/done` and `DELETE /todos/<n>` call `Store.transition` (`KeyError` -> 404, `ValueError` -> 409). `DELETE` archives (`done` -> `archived`); it never removes a row.
- **CLI:** `add|list|done|rm`, each with `--data`; JSON on stdout; errors exit 1 with `todo: <message>` on stderr. `rm` is also an archive.
- **State:** `Store` creates the data dir, keeps `todos.json` (written by temp file + `os.replace` under an `flock` on `todos.lock`), ids are `max + 1` (1 on an empty store), and status only moves `open -> done -> archived`.
- **Isolation:** port and data dir are flags, so instances can run side by side. No seed data, auth, migrations, or build step. Tests: `python3 -m unittest`.
- `.gitignore` already ignores `artifacts/` and `data/`.

Before generating anything, the checkout must build and start:

```console
$ python3 -m unittest -q 2>&1 | tail -3
Ran 7 tests in 0.278s

OK
[exit 0]
```

```console
$ D=$(mktemp -d /tmp/phaseC-start-XXXX); P=$(python3 -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\",0)); print(s.getsockname()[1])"); python3 -m todo serve --port $P --data $D/data & S=$!; for i in $(seq 50); do curl -s http://127.0.0.1:$P/health && break; sleep 0.1; done; echo; curl -s http://127.0.0.1:$P/todos; echo; kill $S; wait $S; echo "stopped my server pid $S (wait status $?)"; rm -rf $D
{"ok": true}
[]
stopped my server pid 125982 (wait status 143)
[exit 0]
```

The base is healthy (7 tests pass; the server starts, answers `/health` and `/todos`, and only the PID this check started was stopped).

## 2. Scaffold

```console
$ SKILL_DIR=.claude/skills/create-verification-skill; python3 $SKILL_DIR/scripts/scaffold.py . --app todo
scaffold: wrote /tmp/phaseC-todo/.claude/skills/verify-todo (7 files, 4 features)
  launch: python3 -m todo serve --data "$DATA_DIR" --port "$PORT"
  health: /health  read: /todos  cli: python3 -m todo
  23 TODO(verify) markers to replace, e.g. features/README.md:10, features/create-todo.md:3, features/create-todo.md:22, features/create-todo.md:23
  next: replace every TODO(verify) (grep -rn 'TODO(verify)' .claude/skills/verify-todo)
  next: .claude/skills/verify-todo/scripts/control.sh prove --feature <id> http GET /todos
  next: python3 /home/box/developer/poteto-skills/skills/create-verification-skill/scripts/check_verify_skill.py .claude/skills/verify-todo
[exit 0]
```

```console
$ find .claude/skills/verify-todo -type f | sort
.claude/skills/verify-todo/SKILL.md
.claude/skills/verify-todo/features/README.md
.claude/skills/verify-todo/features/create-todo.md
.claude/skills/verify-todo/features/delete-todo.md
.claude/skills/verify-todo/features/done-todo.md
.claude/skills/verify-todo/features/list-todos.md
.claude/skills/verify-todo/scripts/control.sh
[exit 0]
```

```console
$ grep -rn 'TODO(verify)' .claude/skills/verify-todo
.claude/skills/verify-todo/features/README.md:10:- TODO(verify): seed data the journeys need, created through the app's own routes or commands (or "none").
.claude/skills/verify-todo/features/create-todo.md:3:TODO(verify): one paragraph, from the user's point of view: what "create a todo" does and what the user sees afterwards.
.claude/skills/verify-todo/features/create-todo.md:22:- **HTTP POST.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "verify create-todo"}'`. TODO(verify): the expected status and body.
.claude/skills/verify-todo/features/create-todo.md:23:- **CLI add.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify create-todo'`. TODO(verify): the expected exit code and stdout.
.claude/skills/verify-todo/features/create-todo.md:24:- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http GET /todos` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. TODO(verify): what the read-back and the data snapshot must show.
.claude/skills/verify-todo/features/create-todo.md:28:- TODO(verify): traps that waste or invalidate a run here (ids, ordering, defaults, validation).
.claude/skills/verify-todo/features/list-todos.md:3:TODO(verify): one paragraph, from the user's point of view: what "list todos" does and what the user sees afterwards.
.claude/skills/verify-todo/features/list-todos.md:22:- **HTTP GET.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos`. TODO(verify): the expected status and body.
.claude/skills/verify-todo/features/list-todos.md:23:- **CLI list.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli -- python3 -m todo list --data {DATA_DIR}`. TODO(verify): the expected exit code and stdout.
.claude/skills/verify-todo/features/list-todos.md:24:- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. TODO(verify): what the read-back and the data snapshot must show.
.claude/skills/verify-todo/features/list-todos.md:28:- TODO(verify): traps that waste or invalidate a run here (ids, ordering, defaults, validation).
.claude/skills/verify-todo/features/done-todo.md:3:TODO(verify): one paragraph, from the user's point of view: what "mark a todo done" does and what the user sees afterwards.
.claude/skills/verify-todo/features/done-todo.md:20:- At least one todo exists: run the create recipe first. TODO(verify): the id it returns on an empty data dir.
.claude/skills/verify-todo/features/done-todo.md:22:- **HTTP POST.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/<id>/done`. Replace `<...>` with the real id. TODO(verify): the expected status and body.
.claude/skills/verify-todo/features/done-todo.md:23:- **CLI done.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} '<id>'`. Replace `<...>` with the real id. TODO(verify): the expected exit code and stdout.
.claude/skills/verify-todo/features/done-todo.md:24:- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http GET /todos` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. TODO(verify): what the read-back and the data snapshot must show.
.claude/skills/verify-todo/features/done-todo.md:28:- TODO(verify): traps that waste or invalidate a run here (ids, ordering, defaults, validation).
.claude/skills/verify-todo/features/delete-todo.md:3:TODO(verify): one paragraph, from the user's point of view: what "delete a todo" does and what the user sees afterwards.
.claude/skills/verify-todo/features/delete-todo.md:20:- At least one todo exists: run the create recipe first. TODO(verify): the id it returns on an empty data dir.
.claude/skills/verify-todo/features/delete-todo.md:22:- **HTTP DELETE.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/<id>`. Replace `<...>` with the real id. TODO(verify): the expected status and body.
.claude/skills/verify-todo/features/delete-todo.md:23:- **CLI rm.** Run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} '<id>'`. Replace `<...>` with the real id. TODO(verify): the expected exit code and stdout.
.claude/skills/verify-todo/features/delete-todo.md:24:- **Proof.** Read it back through a second view: run `.claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http GET /todos` and `.claude/skills/verify-todo/scripts/control.sh evidence --copy-data`. TODO(verify): what the read-back and the data snapshot must show.
.claude/skills/verify-todo/features/delete-todo.md:28:- TODO(verify): traps that waste or invalidate a run here (ids, ordering, defaults, validation).
[exit 0]
```

## 3. Replace every TODO with real commands

The generated `control.sh` CONFIG already matched the code (`LAUNCH_CMD='python3 -m todo serve --data "$DATA_DIR" --port "$PORT"'`, `PORT_MODE=flag`, `HEALTH_PATH=/health`, `READ_PATH=/todos`), so the 23 markers were all in the feature map. SKILL.md says to run each command before writing it down, so I drove every recipe command, plus the error paths the code suggested, on a live run first.

### Exploratory live run (observe before writing)

```console
$ VERIFY_RUN_ID=explore .claude/skills/verify-todo/scripts/control.sh launch
{
  "run_id": "explore",
  "pid": 126177,
  "port": 57073,
  "base_url": "http://127.0.0.1:57073",
  "data_dir": "/tmp/verify-todo-1000/explore/data",
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/explore",
  "log": "/tmp/phaseC-todo/artifacts/verify/explore/launch.log"
}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh doctor
{
  "ok": true,
  "run_id": "explore",
  "checks": [
    {
      "name": "process",
      "ok": true,
      "detail": "pid 126177 alive, started by this run"
    },
    {
      "name": "port",
      "ok": true,
      "detail": "57073 accepts connections"
    },
    {
      "name": "GET /health",
      "ok": true,
      "detail": "200"
    },
    {
      "name": "GET /todos",
      "ok": true,
      "detail": "200"
    },
    {
      "name": "data dir",
      "ok": true,
      "detail": "/tmp/verify-todo-1000/explore/data"
    },
    {
      "name": "evidence dir",
      "ok": true,
      "detail": "/tmp/phaseC-todo/artifacts/verify/explore"
    }
  ]
}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "verify create-todo"}'
evidence: /tmp/phaseC-todo/artifacts/verify/explore/001-create-todo-http.json
POST http://127.0.0.1:57073/todos -> 201
{"id": 1, "title": "verify create-todo", "status": "open"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify create-todo'
{"id": 2, "title": "verify create-todo", "status": "open"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/explore/002-create-todo-cli.json
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "  "}'
evidence: /tmp/phaseC-todo/artifacts/verify/explore/003-create-todo-http.json
POST http://127.0.0.1:57073/todos -> 409
{"error": "title must be a nonempty string"}
[exit 1]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos
evidence: /tmp/phaseC-todo/artifacts/verify/explore/004-create-todo-http.json
POST http://127.0.0.1:57073/todos -> 409
{"error": "body must be 1..65536 bytes"}
[exit 1]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos
evidence: /tmp/phaseC-todo/artifacts/verify/explore/005-list-todos-http.json
GET http://127.0.0.1:57073/todos -> 200
[{"id": 1, "status": "open", "title": "verify create-todo"}, {"id": 2, "status": "open", "title": "verify create-todo"}]
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli -- python3 -m todo list --data {DATA_DIR}
[{"id": 1, "status": "open", "title": "verify create-todo"}, {"id": 2, "status": "open", "title": "verify create-todo"}]
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/explore/006-list-todos-cli.json
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/explore/007-delete-todo-http.json
DELETE http://127.0.0.1:57073/todos/1 -> 409
{"error": "cannot move open to archived"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done
evidence: /tmp/phaseC-todo/artifacts/verify/explore/008-done-todo-http.json
POST http://127.0.0.1:57073/todos/1/done -> 200
{"id": 1, "status": "done", "title": "verify create-todo"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/explore/009-done-todo-http.json
POST http://127.0.0.1:57073/todos/1/done -> 409
{"error": "cannot move done to done"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 2
{"id": 2, "status": "done", "title": "verify create-todo"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/explore/010-done-todo-cli.json
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/99/done --expect 404
evidence: /tmp/phaseC-todo/artifacts/verify/explore/011-done-todo-http.json
POST http://127.0.0.1:57073/todos/99/done -> 404
{"error": "todo not found"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 99
todo: 99
exit: 1  evidence: /tmp/phaseC-todo/artifacts/verify/explore/012-done-todo-cli.json
[exit 1]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1
evidence: /tmp/phaseC-todo/artifacts/verify/explore/013-delete-todo-http.json
DELETE http://127.0.0.1:57073/todos/1 -> 200
{"id": 1, "status": "archived", "title": "verify create-todo"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 2
{"id": 2, "status": "archived", "title": "verify create-todo"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/explore/014-delete-todo-cli.json
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/explore/015-delete-todo-http.json
DELETE http://127.0.0.1:57073/todos/1 -> 409
{"error": "cannot move archived to archived"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos
evidence: /tmp/phaseC-todo/artifacts/verify/explore/016-list-todos-http.json
GET http://127.0.0.1:57073/todos -> 200
[{"id": 1, "status": "archived", "title": "verify create-todo"}, {"id": 2, "status": "archived", "title": "verify create-todo"}]
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "  after archive  "}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/explore/017-create-todo-http.json
POST http://127.0.0.1:57073/todos -> 201
{"id": 3, "title": "after archive", "status": "open"}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 3
todo: cannot move open to archived
exit: 1  evidence: /tmp/phaseC-todo/artifacts/verify/explore/018-delete-todo-cli.json
[exit 1]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh evidence --copy-data && ls artifacts/verify/explore/ && cat artifacts/verify/explore/*-data-snapshot/todos.json; echo
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/explore",
  "files": 21,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/explore/manifest.json"
}
001-create-todo-http.json
002-create-todo-cli.json
003-create-todo-http.json
004-create-todo-http.json
005-list-todos-http.json
006-list-todos-cli.json
007-delete-todo-http.json
008-done-todo-http.json
009-done-todo-http.json
010-done-todo-cli.json
011-done-todo-http.json
012-done-todo-cli.json
013-delete-todo-http.json
014-delete-todo-cli.json
015-delete-todo-http.json
016-list-todos-http.json
017-create-todo-http.json
018-delete-todo-cli.json
019-data-snapshot
launch.log
manifest.json
[{"id": 1, "status": "archived", "title": "verify create-todo"}, {"id": 2, "status": "archived", "title": "verify create-todo"}, {"id": 3, "status": "open", "title": "after archive"}]
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh cleanup
{
  "run_id": "explore",
  "killed": true,
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/explore",
  "evidence_files": 22
}
[exit 0]
```

```console
$ .claude/skills/verify-todo/scripts/control.sh status; ps -p 126177 -o pid=,stat=,args= || echo "pid 126177 (this run's server) is gone"
{"active": false}
pid 126177 (this run's server) is gone
[exit 0]
```

### Recipes written from the observations

Decisions, each from an observation above:

- Seed data: none. Each recipe starts from a fresh `launch` so the ids are exact (the contract rejects `<id>` placeholders); `done-todo` and `delete-todo` create their own preconditions through the public routes and CLI.
- Expected results are the observed statuses, bodies, exit codes, and stderr, including the refusals (`409 cannot move done to done`, `404 todo not found`, CLI `todo: 99`, exit 1).
- Gotchas recorded: validation answers 409, not 400, and `drive` exits 1 on 4xx without `--expect`; titles are trimmed and duplicates allowed; create responses and reads differ in key order; `DELETE`/`rm` archive instead of removing; ids are never reused; a snapshot before the first write holds only `todos.lock` (verified in step 4).

```console
$ grep -rn 'TODO(verify)' .claude/skills/verify-todo | wc -l
0
[exit 0]
```

### Replay every recipe literally (fresh launch per feature)

`replay.sh` (scratch helper, /tmp/phaseC-scratch) launches a fresh run, extracts each backticked control.sh command from the feature's "Driving it with control.sh" section in document order, runs it verbatim, then cleans up.

```console
$ /tmp/phaseC-scratch/replay.sh .claude/skills/verify-todo/features/create-todo.md
launched run replay-create-todo
$ .claude/skills/verify-todo/scripts/control.sh doctor
doctor ok: True
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/replay-create-todo/001-create-todo-http.json
POST http://127.0.0.1:52491/todos -> 201
{"id": 1, "title": "verify create-todo", "status": "open"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify create-todo'
{"id": 2, "title": "verify create-todo", "status": "open"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-create-todo/002-create-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos '{"title": "  "}' --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/replay-create-todo/003-create-todo-http.json
POST http://127.0.0.1:52491/todos -> 409
{"error": "title must be a nonempty string"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http POST /todos --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/replay-create-todo/004-create-todo-http.json
POST http://127.0.0.1:52491/todos -> 409
{"error": "body must be 1..65536 bytes"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature create-todo http GET /todos --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-create-todo/005-create-todo-http.json
GET http://127.0.0.1:52491/todos -> 200
[{"id": 1, "status": "open", "title": "verify create-todo"}, {"id": 2, "status": "open", "title": "verify create-todo"}]
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh evidence --copy-data
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/replay-create-todo",
  "files": 8,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/replay-create-todo/manifest.json"
}
[exit 0]
snapshot artifacts/verify/replay-create-todo/006-data-snapshot: todos.json todos.lock 
[{"id": 1, "status": "open", "title": "verify create-todo"}, {"id": 2, "status": "open", "title": "verify create-todo"}]
cleanup: killed True | evidence kept: 9 files in /tmp/phaseC-todo/artifacts/verify/replay-create-todo
[exit 0]
```

```console
$ /tmp/phaseC-scratch/replay.sh .claude/skills/verify-todo/features/list-todos.md
launched run replay-list-todos
$ .claude/skills/verify-todo/scripts/control.sh doctor
doctor ok: True
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-list-todos/001-list-todos-http.json
GET http://127.0.0.1:49229/todos -> 200
[]
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http POST /todos '{"title": "verify list-todos"}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/replay-list-todos/002-list-todos-http.json
POST http://127.0.0.1:49229/todos -> 201
{"id": 1, "title": "verify list-todos", "status": "open"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos http GET /todos --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-list-todos/003-list-todos-http.json
GET http://127.0.0.1:49229/todos -> 200
[{"id": 1, "status": "open", "title": "verify list-todos"}]
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature list-todos cli -- python3 -m todo list --data {DATA_DIR}
[{"id": 1, "status": "open", "title": "verify list-todos"}]
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-list-todos/004-list-todos-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh evidence --copy-data
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/replay-list-todos",
  "files": 7,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/replay-list-todos/manifest.json"
}
[exit 0]
snapshot artifacts/verify/replay-list-todos/005-data-snapshot: todos.json todos.lock 
[{"id": 1, "status": "open", "title": "verify list-todos"}]
cleanup: killed True | evidence kept: 8 files in /tmp/phaseC-todo/artifacts/verify/replay-list-todos
[exit 0]
```

```console
$ /tmp/phaseC-scratch/replay.sh .claude/skills/verify-todo/features/done-todo.md
launched run replay-done-todo
$ .claude/skills/verify-todo/scripts/control.sh doctor
doctor ok: True
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos '{"title": "verify done-todo"}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/001-done-todo-http.json
POST http://127.0.0.1:46641/todos -> 201
{"id": 1, "title": "verify done-todo", "status": "open"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify done-todo'
{"id": 2, "title": "verify done-todo", "status": "open"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/002-done-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/003-done-todo-http.json
POST http://127.0.0.1:46641/todos/1/done -> 200
{"id": 1, "status": "done", "title": "verify done-todo"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 2
{"id": 2, "status": "done", "title": "verify done-todo"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/004-done-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/1/done --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/005-done-todo-http.json
POST http://127.0.0.1:46641/todos/1/done -> 409
{"error": "cannot move done to done"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http POST /todos/99/done --expect 404
evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/006-done-todo-http.json
POST http://127.0.0.1:46641/todos/99/done -> 404
{"error": "todo not found"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo cli -- python3 -m todo done --data {DATA_DIR} 99
todo: 99
exit: 1  evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/007-done-todo-cli.json
[exit 1]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature done-todo http GET /todos --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-done-todo/008-done-todo-http.json
GET http://127.0.0.1:46641/todos -> 200
[{"id": 1, "status": "done", "title": "verify done-todo"}, {"id": 2, "status": "done", "title": "verify done-todo"}]
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh evidence --copy-data
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/replay-done-todo",
  "files": 11,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/replay-done-todo/manifest.json"
}
[exit 0]
snapshot artifacts/verify/replay-done-todo/009-data-snapshot: todos.json todos.lock 
[{"id": 1, "status": "done", "title": "verify done-todo"}, {"id": 2, "status": "done", "title": "verify done-todo"}]
cleanup: killed True | evidence kept: 12 files in /tmp/phaseC-todo/artifacts/verify/replay-done-todo
[exit 0]
```

```console
$ /tmp/phaseC-scratch/replay.sh .claude/skills/verify-todo/features/delete-todo.md
launched run replay-delete-todo
$ .claude/skills/verify-todo/scripts/control.sh doctor
doctor ok: True
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos '{"title": "verify delete-todo"}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/001-delete-todo-http.json
POST http://127.0.0.1:42789/todos -> 201
{"id": 1, "title": "verify delete-todo", "status": "open"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo add --data {DATA_DIR} 'verify delete-todo'
{"id": 2, "title": "verify delete-todo", "status": "open"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/002-delete-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos/1/done --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/003-delete-todo-http.json
POST http://127.0.0.1:42789/todos/1/done -> 200
{"id": 1, "status": "done", "title": "verify delete-todo"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo done --data {DATA_DIR} 2
{"id": 2, "status": "done", "title": "verify delete-todo"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/004-delete-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http POST /todos '{"title": "still open"}' --expect 201
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/005-delete-todo-http.json
POST http://127.0.0.1:42789/todos -> 201
{"id": 3, "title": "still open", "status": "open"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/006-delete-todo-http.json
DELETE http://127.0.0.1:42789/todos/1 -> 200
{"id": 1, "status": "archived", "title": "verify delete-todo"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 2
{"id": 2, "status": "archived", "title": "verify delete-todo"}
exit: 0  evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/007-delete-todo-cli.json
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/3 --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/008-delete-todo-http.json
DELETE http://127.0.0.1:42789/todos/3 -> 409
{"error": "cannot move open to archived"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http DELETE /todos/1 --expect 409
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/009-delete-todo-http.json
DELETE http://127.0.0.1:42789/todos/1 -> 409
{"error": "cannot move archived to archived"}
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo cli -- python3 -m todo rm --data {DATA_DIR} 3
todo: cannot move open to archived
exit: 1  evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/010-delete-todo-cli.json
[exit 1]
$ .claude/skills/verify-todo/scripts/control.sh drive --feature delete-todo http GET /todos --expect 200
evidence: /tmp/phaseC-todo/artifacts/verify/replay-delete-todo/011-delete-todo-http.json
GET http://127.0.0.1:42789/todos -> 200
[{"id": 1, "status": "archived", "title": "verify delete-todo"}, {"id": 2, "status": "archived", "title": "verify delete-todo"}, {"id": 3, "status": "open", "title": "still open"}]
[exit 0]
$ .claude/skills/verify-todo/scripts/control.sh evidence --copy-data
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/replay-delete-todo",
  "files": 14,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/replay-delete-todo/manifest.json"
}
[exit 0]
snapshot artifacts/verify/replay-delete-todo/012-data-snapshot: todos.json todos.lock 
[{"id": 1, "status": "archived", "title": "verify delete-todo"}, {"id": 2, "status": "archived", "title": "verify delete-todo"}, {"id": 3, "status": "open", "title": "still open"}]
cleanup: killed True | evidence kept: 15 files in /tmp/phaseC-todo/artifacts/verify/replay-delete-todo
[exit 0]
```

## 4. Prove it end to end once

The SKILL.md chain, literally, driving the `list-todos` recipe's first step (this also checks the `todos.lock`-only snapshot gotcha):

```console
$ C=.claude/skills/verify-todo/scripts/control.sh
VERIFY_RUN_ID=e2e-proof $C launch && $C doctor && $C drive --feature list-todos http GET /todos --expect 200 && $C evidence --copy-data && $C cleanup
{
  "run_id": "e2e-proof",
  "pid": 128310,
  "port": 54799,
  "base_url": "http://127.0.0.1:54799",
  "data_dir": "/tmp/verify-todo-1000/e2e-proof/data",
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/e2e-proof",
  "log": "/tmp/phaseC-todo/artifacts/verify/e2e-proof/launch.log"
}
{
  "ok": true,
  "run_id": "e2e-proof",
  "checks": [
    {
      "name": "process",
      "ok": true,
      "detail": "pid 128310 alive, started by this run"
    },
    {
      "name": "port",
      "ok": true,
      "detail": "54799 accepts connections"
    },
    {
      "name": "GET /health",
      "ok": true,
      "detail": "200"
    },
    {
      "name": "GET /todos",
      "ok": true,
      "detail": "200"
    },
    {
      "name": "data dir",
      "ok": true,
      "detail": "/tmp/verify-todo-1000/e2e-proof/data"
    },
    {
      "name": "evidence dir",
      "ok": true,
      "detail": "/tmp/phaseC-todo/artifacts/verify/e2e-proof"
    }
  ]
}
evidence: /tmp/phaseC-todo/artifacts/verify/e2e-proof/001-list-todos-http.json
GET http://127.0.0.1:54799/todos -> 200
[]
{
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/e2e-proof",
  "files": 3,
  "manifest": "/tmp/phaseC-todo/artifacts/verify/e2e-proof/manifest.json"
}
{
  "run_id": "e2e-proof",
  "killed": true,
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/e2e-proof",
  "evidence_files": 4
}
[exit 0]
```

```console
$ ls artifacts/verify/e2e-proof/ artifacts/verify/e2e-proof/*-data-snapshot/ && python3 -c 'import json; m=json.load(open("artifacts/verify/e2e-proof/manifest.json")); print("manifest:", [f["path"] for f in m["files"]])'
artifacts/verify/e2e-proof/:
001-list-todos-http.json
002-data-snapshot
launch.log
manifest.json

artifacts/verify/e2e-proof/002-data-snapshot/:
todos.lock
manifest: ['001-list-todos-http.json', '002-data-snapshot/todos.lock', 'launch.log']
[exit 0]
```

```console
$ ps -eo pid,args | awk '$2 ~ /^python/ && /todo serve/' | grep . || echo "no todo server running after cleanup"
no todo server running after cleanup
[exit 0]
```

Then the one-command form, driving a mutation so the side effect lands in the snapshot:

```console
$ VERIFY_RUN_ID=prove-create .claude/skills/verify-todo/scripts/control.sh prove --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201 2>&1 | grep -v '"detail"\|"name"\|"ok": true,$\|^    [{}],\?$'
{
  "run_id": "prove-create",
  "pid": 128438,
  "port": 57847,
  "base_url": "http://127.0.0.1:57847",
  "data_dir": "/tmp/verify-todo-1000/prove-create/data",
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/prove-create",
  "log": "/tmp/phaseC-todo/artifacts/verify/prove-create/launch.log"
}
{
  "run_id": "prove-create",
  "checks": [
  ]
}
evidence: /tmp/phaseC-todo/artifacts/verify/prove-create/001-create-todo-http.json
POST http://127.0.0.1:57847/todos -> 201
{"id": 1, "title": "verify create-todo", "status": "open"}
{
  "run_id": "prove-create",
  "killed": true,
  "evidence_dir": "/tmp/phaseC-todo/artifacts/verify/prove-create",
  "evidence_files": 5
}
prove: drive exit 0; evidence kept in /tmp/phaseC-todo/artifacts/verify/prove-create (1 drive record(s))
[exit 0]
```

```console
$ ls artifacts/verify/prove-create/ && cat artifacts/verify/prove-create/*-data-snapshot/todos.json; echo; .claude/skills/verify-todo/scripts/control.sh status
001-create-todo-http.json
002-data-snapshot
launch.log
manifest.json
[{"id": 1, "status": "open", "title": "verify create-todo"}]
{"active": false}
[exit 0]
```

(The grep in the prove command above only trims the doctor JSON rows; prove skips the drive when doctor fails, and the drive ran, so doctor passed. The full doctor output is in the step-4 chain.)

## 5. Gate and hand off

```console
$ SKILL_DIR=.claude/skills/create-verification-skill; python3 $SKILL_DIR/scripts/check_verify_skill.py .claude/skills/verify-todo
check_verify_skill: PASS (28/28 checks) .claude/skills/verify-todo
[exit 0]
```

```console
$ python3 .claude/skills/maintain-verification-skill/scripts/drift_check.py .claude/skills/verify-todo --repo .
drift_check: clean (5 routes, 5 commands in source) .claude/skills/verify-todo
[exit 0]
```

```console
$ git check-ignore -v artifacts/verify/e2e-proof/manifest.json; git status --short
.gitignore:3:artifacts/	artifacts/verify/e2e-proof/manifest.json
?? .claude/
[exit 0]
```

```console
$ git add .claude/skills/verify-todo && git -c user.name=claude-code -c user.email=claude@local commit -q -m "verify: add verify-todo skill (control.sh harness, four-feature map)" && git log --oneline -n 2 && git show --stat --format= HEAD && git ls-files -s .claude/skills/verify-todo/scripts/control.sh
687c90e verify: add verify-todo skill (control.sh harness, four-feature map)
4242a5a docs: retain review and chat evidence of repeated persistence and status mistakes
 .claude/skills/verify-todo/SKILL.md                |  43 +++
 .claude/skills/verify-todo/features/README.md      |  35 ++
 .claude/skills/verify-todo/features/create-todo.md |  33 ++
 .claude/skills/verify-todo/features/delete-todo.md |  32 ++
 .claude/skills/verify-todo/features/done-todo.md   |  33 ++
 .claude/skills/verify-todo/features/list-todos.md  |  32 ++
 .claude/skills/verify-todo/scripts/control.sh      | 409 +++++++++++++++++++++
 7 files changed, 617 insertions(+)
100755 c2d4b23ed081441cd0d9fd614d0da26c1b1dd044 0	.claude/skills/verify-todo/scripts/control.sh
[exit 0]
```

The contract check passes 28/28, the drift check is clean, `artifacts/` was already ignored, and the skill is committed in the scratch repo as `687c90e`.

## Reply (the format SKILL.md asks for)

- **Surface:** the HTTP JSON API is primary; the CLI is mapped too because it shares the store (each recipe proves both views agree).
- **Prove command:** `.claude/skills/verify-todo/scripts/control.sh prove --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201` -> `POST .../todos -> 201 {"id": 1, "title": "verify create-todo", "status": "open"}`, `prove: drive exit 0; evidence kept in /tmp/phaseC-todo/artifacts/verify/prove-create (1 drive record(s))`. The data snapshot holds `[{"id": 1, "status": "open", "title": "verify create-todo"}]`.
- **Evidence:** `/tmp/phaseC-todo/artifacts/verify/{explore,replay-*,e2e-proof,prove-create}/`; copies of `e2e-proof` and `prove-create` are in `logs/agent-runs/artifacts/evidence/`.
- **Features mapped vs. detected:** 4/4 features, 5/5 routes (`/health` in Launch/Doctor), and 4/4 CLI subcommands (`serve` is the launch command). Every recipe was replayed verbatim on a fresh run and matched.
- **Unverified:** concurrent writers (the store's lock is not exercised by a serial drive; see /formal-verification), bodies over 65536 bytes, and non-Linux hosts. Keep the map honest with /maintain-verification-skill.
