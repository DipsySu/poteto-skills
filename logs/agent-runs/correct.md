# Agent run: /correct on the same todo app

- Date: 2026-10-19. Agent: Claude (Opus 5.5), Phase C of poteto-skills.
- Target: `/tmp/phaseC-todo` after the /create-verification-skill run (HEAD `687c90e`, which added `.claude/skills/verify-todo`). The collection is installed there with `./install.sh --project /tmp/phaseC-todo --target claude`.
- Instructions followed: `/tmp/phaseC-todo/.claude/skills/correct/SKILL.md` (mine, pick the highest level, fix and prove, keep the rule table, reply). The mistake commits were found from history with the skill's miner and `git show`, not from the example builder's metadata.
- Recording: every shell command is shown verbatim with its full combined output and exit code (`/tmp/phaseC-scratch/rec.sh`). Text outside the console blocks is mine. Files read or edited with the editor (`todo/store.py`, `tests/test_todo.py`, `scripts/lint.sh`, `AGENTS.md`) are summarized; the full change is the commit diff.
- Artifact: `logs/agent-runs/artifacts/correct-commit-0f00719.diff` (`git show --format=fuller 0f00719`).

All commands run from `/tmp/phaseC-todo`. The optional `gh api` step does not apply: the repo has no remote, and its review comments ship as `review-comments.json`.

## 1. Mine the classes

```console
$ SKILL_DIR=.claude/skills/correct; python3 $SKILL_DIR/scripts/mine_mistakes.py . --since "90 days ago" --feedback review-comments.json chat-feedback.md
mine_mistakes: 13 items (3 chat, 6 commit, 4 review) from /tmp/phaseC-todo since 90 days ago

C1  7 occurrences [chat×2, commit×4, review×2]  signature: store.py, bypassing, direct, json, store, writes
  rule enforced, mistake still happened (check the check): AGENTS.md:5 "Persist JSON only through store.py; use Store.add or Store.transition" <- scripts/lint.sh
  - review   review-comments.json#102 @reviewer todo/api.py:1 "Direct JSON writes bypassing store.py repeat in the HTTP handler. Use Store.add; only sto…"
  - review   review-comments.json#101 @reviewer todo/__main__.py:1 "Direct JSON writes bypassing store.py again: store.path.write_text(json.dumps(rows)) lose…"
  - fix      2fd7988731 2026-09-18 fix: remove direct JSON writes bypassing store.py; use Store.add atomic transac…  [todo/api.py]
  - fix      dbd4400868 2026-09-15 fix: persist API additions with direct JSON writes bypassing store.py  [todo/api.py]
  - revert   97a3f30001 2026-09-09 Revert "fix: persist CLI additions with direct JSON writes bypassing store.py"  [todo/__main__.py]
  - reverted 570c1e91c2 2026-09-07 fix: persist CLI additions with direct JSON writes bypassing store.py  [todo/__main__.py]
  - chat     chat-feedback.md:4 "Direct JSON writes bypassing store.py lose concurrent updates; centralize persistence and…"
  - chat     chat-feedback.md:3 "Direct JSON writes bypassing store.py happened in the CLI and HTTP handler. Use Store.add…"

C2  5 occurrences [chat×1, commit×2, review×2]  signature: done, status, transition, machine, reject, state
  rule enforced, mistake still happened (check the check): AGENTS.md:5 "Persist JSON only through store.py; use Store.add or Store.transition" <- scripts/lint.sh
  rule enforced, mistake still happened (check the check): AGENTS.md:6 "Status changes follow open -> done -> archived" <- tests/test_todo.py
  - review   review-comments.json#104 @reviewer todo/store.py:1 "Status transition done to done accepted again. Reject repeated completion in the shared s…"
  - review   review-comments.json#103 @reviewer todo/store.py:1 "Status transition open to archived skips done. Keep the forward-only state machine."
  - fix      8801d95dfc 2026-10-06 fix: reject repeated status transition done to done; enforce forward-only state…  [todo/store.py]
  - fix      86baa1827c 2026-09-25 fix: reject status transition open to archived; cannot skip done  [todo/store.py]
  - chat     chat-feedback.md:5 "Status transition bugs returned twice: skipping done and accepting done to done. Reject i…"
[exit 0]
```


## 2. Pick the highest level that works

Reading the evidence (the clustering is a start, not a verdict):

- **C1 is one class** with two real mistakes of the same shape. `570c1e9` (CLI) and `dbd4400` (API) both read with `list_all()`, computed `len(rows) + 1`, and wrote with `store.path.write_text(json.dumps(rows))`. That skips the `flock`, the atomic replace, and title validation. `97a3f30` reverted the first and `2fd7988` fixed the second. Review #101/#102 and chat lines 3-4 complain about the same thing. Both mistakes used the public `Store.path` handle.
- **C2 is a separate class** (status transitions: `86baa18`, `8801d95`, review #103/#104). The miner attached the AGENTS.md persistence row to C2 only because both mention `Store.transition`. C2 is the next class, not part of this commit.
- **"Rule enforced, mistake still happened":** see the check-the-check run below.

### The evidence for C1

```console
$ git show --format="%h %ad %s" --date=short 570c1e9 -- todo; git show --format="%h %ad %s" --date=short dbd4400 -- todo
570c1e9 2026-09-07 fix: persist CLI additions with direct JSON writes bypassing store.py

diff --git a/todo/__main__.py b/todo/__main__.py
index a58a68b..84c0115 100644
--- a/todo/__main__.py
+++ b/todo/__main__.py
@@ -30,7 +30,10 @@ def main():
     store = Store(args.data)
     try:
         if args.command == "add":
-            result = store.add(args.title)
+            rows = store.list_all()
+            result = {"id": len(rows) + 1, "title": args.title, "status": "open"}
+            rows.append(result)
+            store.path.write_text(json.dumps(rows))
         elif args.command == "list":
             result = store.list_all()
         elif args.command == "done":
dbd4400 2026-09-15 fix: persist API additions with direct JSON writes bypassing store.py

diff --git a/todo/api.py b/todo/api.py
index ca0a837..6f5d02b 100644
--- a/todo/api.py
+++ b/todo/api.py
@@ -36,7 +36,12 @@ class Handler(BaseHTTPRequestHandler):
                 if not 0 < length <= 65536:
                     raise ValueError("body must be 1..65536 bytes")
                 body = json.loads(self.rfile.read(length))
-                self.reply(201, self.server.store.add(body.get("title")))
+                store = self.server.store
+                rows = store.list_all()
+                row = {"id": len(rows) + 1, "title": body.get("title"), "status": "open"}
+                rows.append(row)
+                store.path.write_text(json.dumps(rows))
+                self.reply(201, row)
             elif match := re.fullmatch(r"/todos/([0-9]+)/done", path):
                 self.reply(200, self.server.store.transition(int(match[1]), "done"))
             else:
[exit 0]
```

```console
$ git show --format="%h %ad %s" --date=short 2fd7988 -- todo
2fd7988 2026-09-18 fix: remove direct JSON writes bypassing store.py; use Store.add atomic transaction

diff --git a/todo/api.py b/todo/api.py
index 6f5d02b..ca0a837 100644
--- a/todo/api.py
+++ b/todo/api.py
@@ -36,12 +36,7 @@ class Handler(BaseHTTPRequestHandler):
                 if not 0 < length <= 65536:
                     raise ValueError("body must be 1..65536 bytes")
                 body = json.loads(self.rfile.read(length))
-                store = self.server.store
-                rows = store.list_all()
-                row = {"id": len(rows) + 1, "title": body.get("title"), "status": "open"}
-                rows.append(row)
-                store.path.write_text(json.dumps(rows))
-                self.reply(201, row)
+                self.reply(201, self.server.store.add(body.get("title")))
             elif match := re.fullmatch(r"/todos/([0-9]+)/done", path):
                 self.reply(200, self.server.store.transition(int(match[1]), "done"))
             else:
[exit 0]
```

### Check the check: `scripts/lint.sh` vs. the two mistakes

```console
$ git log --format="%h %ad %s" --date=short -- scripts/lint.sh AGENTS.md; grep -rn "lint" tests/ README.md AGENTS.md; ls -a; ls .github 2>&1 | head -1
9fa8837 2026-09-02 feat: introduce todo API, CLI and locked JSON store
AGENTS.md:5:| Persist JSON only through store.py; use Store.add or Store.transition | scripts/lint.sh |
.
..
.claude
.git
.gitignore
AGENTS.md
README.md
artifacts
chat-feedback.md
review-comments.json
scripts
tests
todo
ls: cannot access '.github': No such file or directory
[exit 0]
```

```console
$ for rev in 570c1e9 dbd4400 HEAD; do git worktree add -q --detach /tmp/phaseC-scratch/wt-$rev $rev && (cd /tmp/phaseC-scratch/wt-$rev && scripts/lint.sh; echo "lint.sh at $rev: exit $?"); git worktree remove --force /tmp/phaseC-scratch/wt-$rev; done
todo/__main__.py:36: persist through Store.add/Store.transition in store.py
lint.sh at 570c1e9: exit 1
todo/api.py:43: persist through Store.add/Store.transition in store.py
lint.sh at dbd4400: exit 1
persistence boundary: clean
lint.sh at HEAD: exit 0
[exit 0]
```

Where does code outside `store.py` touch `Store.path`?

```console
$ grep -rn "\.path\b\|\.data\b\|todos.json" todo/ tests/ scripts/ | grep -v "^todo/store.py"; echo "--- store attributes:"; grep -n "self\.\(path\|data\)" todo/store.py
grep: todo/__pycache__/store.cpython-313.pyc: binary file matches
todo/api.py:23:        path = urlsplit(self.path).path
todo/api.py:32:        path = urlsplit(self.path).path
todo/api.py:50:        path = urlsplit(self.path).path
todo/__main__.py:28:        serve(args.port, args.data)
todo/__main__.py:30:    store = Store(args.data)
tests/test_todo.py:48:        before = self.store.path.read_bytes()
tests/test_todo.py:53:        self.assertEqual(self.store.path.read_bytes(), before)
--- store attributes:
13:        self.data = Path(data)
14:        self.data.mkdir(parents=True, exist_ok=True)
15:        self.path = self.data / "todos.json"
19:        with (self.data / "todos.lock").open("a+") as lock:
21:            rows = json.loads(self.path.read_text()) if self.path.exists() else []
24:                fd, name = tempfile.mkstemp(prefix=".todos-", dir=self.data)
30:                    os.replace(name, self.path)
[exit 0]
```


Decision for C1 (most frequent, 7 occurrences):

- **Level: architecture.** Make `Store` the only owner of `todos.json`, with no public `path` or `data` attribute, so the handle both mistakes wrote through no longer exists. Callers can persist only through `add()` and `transition()`, and the shortest path for an agent is the right one.
- **Why not only a lint (level 3):** the repo already had a lint for this class (`scripts/lint.sh`, since `9fa8837`). It fires on both mistakes, but nothing ran it: no test calls it and there is no CI. A second copy of the same check as a patlint rule would not change that, so no patlint rule was vendored.
- **Python can't hide attributes,** so the boundary needs a guard that runs. `tests/test_persistence_boundary.py` runs in `python3 -m unittest`, the command the README tells every contributor to run (and the one a CI would call). It pins that `Store` exposes no public attribute and runs `scripts/lint.sh`. The lint was extended to flag builtin `open()`, `write_bytes`, and access to another object's private members outside `store.py`; those are the bypasses left once `path` is gone.
- `tests/test_todo.py` read `store.path` to check the stored bytes; it now reads `<data>/todos.json`, the observable side effect.
- AGENTS.md row: `Persist JSON only through store.py; use Store.add or Store.transition` is now enforced by `` `python3 -m unittest` runs `tests/test_persistence_boundary.py`: `Store` exposes no file handle, and `scripts/lint.sh` must pass ``.

## 3. Fix and prove, one commit per class

### The fix (edited `todo/store.py`, `tests/test_todo.py`, `scripts/lint.sh`, `AGENTS.md`; added `tests/test_persistence_boundary.py`)

```console
$ git status --short && python3 -m unittest -v 2>&1 | tail -14 && scripts/lint.sh
 M AGENTS.md
 M scripts/lint.sh
 M tests/test_todo.py
 M todo/store.py
?? .claude/skills/architect
?? .claude/skills/correct
?? .claude/skills/create-verification-skill
?? .claude/skills/curator
?? .claude/skills/feedback-to-architecture
?? .claude/skills/formal-verification
?? .claude/skills/maintain-verification-skill
?? .claude/skills/sharpen
?? tests/test_persistence_boundary.py
test_persistence_lint_passes (tests.test_persistence_boundary.PersistenceBoundaryTests.test_persistence_lint_passes) ... ok
test_store_exposes_no_file_handle (tests.test_persistence_boundary.PersistenceBoundaryTests.test_store_exposes_no_file_handle) ... ok
test_archived_is_terminal (tests.test_todo.StoreTests.test_archived_is_terminal) ... ok
test_cannot_complete_twice (tests.test_todo.StoreTests.test_cannot_complete_twice) ... ok
test_cannot_skip_done (tests.test_todo.StoreTests.test_cannot_skip_done) ... ok
test_cli_uses_shared_data (tests.test_todo.StoreTests.test_cli_uses_shared_data) ... ok
test_forward_transitions (tests.test_todo.StoreTests.test_forward_transitions) ... ok
test_persistence_and_ids (tests.test_todo.StoreTests.test_persistence_and_ids) ... ok
test_validation_preserves_file (tests.test_todo.StoreTests.test_validation_preserves_file) ... ok

----------------------------------------------------------------------
Ran 9 tests in 0.304s

OK
persistence boundary: clean
[exit 0]
```

```console
$ SKILL_DIR=.claude/skills/correct; python3 $SKILL_DIR/scripts/rule_table.py check AGENTS.md
AGENTS.md: 3 rules, 1 flagged
  OK       line 5    Persist JSON only through store.py; use Store.add or Store.transition
  OK       line 6    Status changes follow open -> done -> archived
  PROSE    line 7    Keep error messages concise and useful
           enforced by: prose in this document -> enforced only by prose or human attention
FAIL: move each flagged rule up the pyramid (architecture > types > lint that names the fix > test), then name that check in the Enforced by column. Drop rules whose mistake can no longer happen.
[exit 1]
```

```console
$ git add AGENTS.md scripts/lint.sh tests/test_todo.py tests/test_persistence_boundary.py todo/store.py && git -c user.name=claude-code -c user.email=claude@local commit -q -F - <<'MSG' && git log --oneline -n 3 && git show --stat --format='%h %s' HEAD
fix(arch): make Store the only owner of todos.json

Class C1, 7 occurrences: direct JSON writes bypassing store.py.
Evidence: 570c1e9 (CLI, reverted by 97a3f30), dbd4400 (API, fixed by
2fd7988), review-comments.json #101 #102, chat-feedback.md:3-4. Both
mistakes wrote through the public Store.path handle.

- todo/store.py: no public path or data dir; callers persist only
  through add() and transition().
- tests/test_persistence_boundary.py: Store exposes no file handle, and
  scripts/lint.sh runs under `python3 -m unittest` (it caught both
  mistakes, but nothing ran it).
- scripts/lint.sh: also flags open(), write_bytes, and access to
  another object's private members outside store.py.
- AGENTS.md: the rule names the check that runs.
MSG
0f00719 fix(arch): make Store the only owner of todos.json
687c90e verify: add verify-todo skill (control.sh harness, four-feature map)
4242a5a docs: retain review and chat evidence of repeated persistence and status mistakes
0f00719 fix(arch): make Store the only owner of todos.json

 AGENTS.md                          |  2 +-
 scripts/lint.sh                    |  7 ++++++-
 tests/test_persistence_boundary.py | 21 +++++++++++++++++++++
 tests/test_todo.py                 |  5 +++--
 todo/store.py                      | 15 ++++++++-------
 5 files changed, 39 insertions(+), 11 deletions(-)
[exit 0]
```

### Proof: the new check fails on the real past mistakes and passes at HEAD

The new check is `python3 -m unittest tests.test_persistence_boundary` (part of `python3 -m unittest`). For each real mistake commit: a detached worktree at that commit, its own test suite first, then the new check overlaid from HEAD (`tests/test_persistence_boundary.py` plus `scripts/lint.sh`).

```console
$ for rev in 570c1e9 dbd4400; do
  wt=/tmp/phaseC-scratch/wt-$rev; git worktree add -q --detach $wt $rev
  echo "== $rev: $(git log -1 --format=%s $rev)"
  (cd $wt && python3 -m unittest 2>&1 | tail -1 | sed "s/^/its own suite: /")
  git show HEAD:tests/test_persistence_boundary.py > $wt/tests/test_persistence_boundary.py
  git show HEAD:scripts/lint.sh > $wt/scripts/lint.sh
  (cd $wt && python3 -m unittest tests.test_persistence_boundary; echo "new check at $rev: exit $?")
  git worktree remove --force $wt
done
== 570c1e9: fix: persist CLI additions with direct JSON writes bypassing store.py
its own suite: OK
FF
======================================================================
FAIL: test_persistence_lint_passes (tests.test_persistence_boundary.PersistenceBoundaryTests.test_persistence_lint_passes)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/phaseC-scratch/wt-570c1e9/tests/test_persistence_boundary.py", line 21, in test_persistence_lint_passes
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 1 != 0 : todo/__main__.py:36: persist through Store.add/Store.transition in store.py


======================================================================
FAIL: test_store_exposes_no_file_handle (tests.test_persistence_boundary.PersistenceBoundaryTests.test_store_exposes_no_file_handle)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/phaseC-scratch/wt-570c1e9/tests/test_persistence_boundary.py", line 16, in test_store_exposes_no_file_handle
    self.assertEqual(public, [], "Store owns its file: persist through Store.add or Store.transition, never a path")
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Lists differ: ['data', 'path'] != []

First list contains 2 additional elements.
First extra element 0:
'data'

- ['data', 'path']
+ [] : Store owns its file: persist through Store.add or Store.transition, never a path

----------------------------------------------------------------------
Ran 2 tests in 0.031s

FAILED (failures=2)
new check at 570c1e9: exit 1
== dbd4400: fix: persist API additions with direct JSON writes bypassing store.py
its own suite: OK
FF
======================================================================
FAIL: test_persistence_lint_passes (tests.test_persistence_boundary.PersistenceBoundaryTests.test_persistence_lint_passes)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/phaseC-scratch/wt-dbd4400/tests/test_persistence_boundary.py", line 21, in test_persistence_lint_passes
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 1 != 0 : todo/api.py:43: persist through Store.add/Store.transition in store.py


======================================================================
FAIL: test_store_exposes_no_file_handle (tests.test_persistence_boundary.PersistenceBoundaryTests.test_store_exposes_no_file_handle)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/phaseC-scratch/wt-dbd4400/tests/test_persistence_boundary.py", line 16, in test_store_exposes_no_file_handle
    self.assertEqual(public, [], "Store owns its file: persist through Store.add or Store.transition, never a path")
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Lists differ: ['data', 'path'] != []

First list contains 2 additional elements.
First extra element 0:
'data'

- ['data', 'path']
+ [] : Store owns its file: persist through Store.add or Store.transition, never a path

----------------------------------------------------------------------
Ran 2 tests in 0.033s

FAILED (failures=2)
new check at dbd4400: exit 1
[exit 0]
```

```console
$ git log -1 --format="== HEAD %h: %s"; python3 -m unittest -v tests.test_persistence_boundary; echo "new check at HEAD: exit $?"; python3 -m unittest 2>&1 | tail -3
== HEAD 0f00719: fix(arch): make Store the only owner of todos.json
test_persistence_lint_passes (tests.test_persistence_boundary.PersistenceBoundaryTests.test_persistence_lint_passes) ... ok
test_store_exposes_no_file_handle (tests.test_persistence_boundary.PersistenceBoundaryTests.test_store_exposes_no_file_handle) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.034s

OK
new check at HEAD: exit 0
Ran 9 tests in 0.365s

OK
[exit 0]
```


Architecture proof (SKILL.md: "show the old code no longer ... passes its test"): put each mistake's caller code back on top of HEAD and run the existing test, or the verify recipe for the API.

```console
$ wt=/tmp/phaseC-scratch/wt-arch; git worktree add -q --detach $wt HEAD
git show 570c1e9:todo/__main__.py > $wt/todo/__main__.py
(cd $wt && python3 -m unittest tests.test_todo.StoreTests.test_cli_uses_shared_data 2>&1 | grep -E "AttributeError|^Ran|^OK|^FAILED"; echo "570c1e9 CLI code on HEAD's Store: exit ${PIPESTATUS[0]}")
git -C $wt checkout -q -- todo/__main__.py
git show dbd4400:todo/api.py > $wt/todo/api.py
C=$wt/.claude/skills/verify-todo/scripts/control.sh; export VERIFY_ARTIFACTS_DIR=/tmp/phaseC-scratch/arch-proof-evidence
VERIFY_RUN_ID=arch-proof-api $C launch >/dev/null && $C drive --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201; echo "dbd4400 API code on HEAD's Store, create-todo recipe: exit $?"
$C cleanup | grep -E '"killed"'
git worktree remove --force $wt
AttributeError: 'Store' object has no attribute 'path'
Ran 1 test in 0.070s
FAILED (failures=1)
570c1e9 CLI code on HEAD's Store: exit 1
evidence: /tmp/phaseC-scratch/arch-proof-evidence/arch-proof-api/001-create-todo-http.json
POST http://127.0.0.1:50211/todos -> 409
{"error": "'Store' object has no attribute 'path'"}
dbd4400 API code on HEAD's Store, create-todo recipe: exit 1
  "killed": true,
[exit 0]
```


The refactor must not change behaviour: re-prove the app at HEAD with the committed verify skill.

```console
$ VERIFY_RUN_ID=post-fix .claude/skills/verify-todo/scripts/control.sh prove --feature create-todo http POST /todos '{"title": "verify create-todo"}' --expect 201 2>&1 | grep -E "^POST|^\{\"id\"|^prove:|\"killed\""
/tmp/phaseC-scratch/replay.sh .claude/skills/verify-todo/features/delete-todo.md | grep -E "^\[exit|^cleanup" | sort | uniq -c
python3 .claude/skills/create-verification-skill/scripts/check_verify_skill.py .claude/skills/verify-todo && python3 .claude/skills/maintain-verification-skill/scripts/drift_check.py .claude/skills/verify-todo --repo .
POST http://127.0.0.1:45191/todos -> 201
{"id": 1, "title": "verify create-todo", "status": "open"}
  "killed": true,
prove: drive exit 0; evidence kept in /tmp/phaseC-todo/artifacts/verify/post-fix (1 drive record(s))
     12 [exit 0]
      1 [exit 1]
      1 cleanup: killed True | evidence kept: 28 files in /tmp/phaseC-todo/artifacts/verify/replay-delete-todo
check_verify_skill: PASS (28/28 checks) .claude/skills/verify-todo
drift_check: clean (5 routes, 5 commands in source) .claude/skills/verify-todo
[exit 0]
```

Note on the last block: the `delete-todo` replay reused run id `replay-delete-todo` from the earlier run. `launch` only checked for leftover *state*, so the new drive records were appended to that run's kept evidence dir (28 files across two runs). All 13 drives behaved as the recipe says: 12 exit 0, plus the expected CLI refusal with exit 1. Phase C fixed this in `scaffold.py`'s control.sh template: `launch` now refuses a run id that already has kept evidence. Re-checked on a regenerated harness: the second `VERIFY_RUN_ID=same launch` exits 1 with "run id same already has state or kept evidence; pick a new VERIFY_RUN_ID". The verify skill committed in the scratch repo predates this template change.

## 4. Rule table

`rule_table.py check AGENTS.md` (run before the commit, above): line 5 (persistence) is now `OK`, naming the check that runs, and line 6 (status, `tests/test_todo.py`) is `OK`. Line 7, "Keep error messages concise and useful", is still flagged `PROSE`. That is a judgment call with no mined repeat, so it stays prose. `check` keeps exiting 1 on it by design.

## Reply (the format SKILL.md asks for)

**C1: direct JSON writes bypassing `store.py` (7 occurrences).**

- Evidence: `570c1e9` (`todo/__main__.py:36`, reverted by `97a3f30`), `dbd4400` (`todo/api.py:43`, fixed by `2fd7988`), `review-comments.json#101` (`todo/__main__.py`), `#102` (`todo/api.py`), `chat-feedback.md:3`, `:4`.
- Level: architecture. `Store` is the sole owner of its file and has no public `path` or `data`. A guard runs in the default test command because Python can't make attributes private.
- Why not higher: architecture is the top level. A lint alone was not enough: one already existed, caught both mistakes, and nothing ran it.
- Proof, as commands:
  - `python3 -m unittest tests.test_persistence_boundary` with the new check overlaid on a checkout of each mistake: exit 1 at `570c1e9` (`todo/__main__.py:36` flagged; public `['data', 'path']`) and exit 1 at `dbd4400` (`todo/api.py:43`). Each revision's own suite passed, which is how the mistakes landed.
  - The same check at HEAD `0f00719`: exit 0. The full suite (`python3 -m unittest`, 9 tests) is OK.
  - Architecture: `570c1e9`'s CLI code on HEAD's `Store` fails `test_cli_uses_shared_data` with `AttributeError: 'Store' object has no attribute 'path'`. `dbd4400`'s API code on HEAD's `Store` fails the verify skill's create-todo recipe (`409` instead of `201`).
  - Behaviour is unchanged: `control.sh prove --feature create-todo ...` -> 201, the delete-todo recipe replays as written, `check_verify_skill` passes 28/28, and the drift check is clean.
- Rule-table row: `| Persist JSON only through store.py; use Store.add or Store.transition | `python3 -m unittest` runs `tests/test_persistence_boundary.py`: `Store` exposes no file handle, and `scripts/lint.sh` must pass |`.
- Commit: `0f00719 fix(arch): make Store the only owner of todos.json`. The diff is in `artifacts/correct-commit-0f00719.diff`.

**Next class, not done in this run: C2, status transitions (5 occurrences).** Evidence: `86baa18`, `8801d95`, review #103/#104, `chat-feedback.md:5`. Suggested level: types. A `Status` enum plus the single `TRANSITIONS` table in `store.py` replaces the raw strings that `api.py` and `__main__.py` pass to `transition()` today. Prove it on `4196703` and `2c383ec`, the commits that introduced the bugs.
