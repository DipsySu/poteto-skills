#!/usr/bin/env python3
"""Reproduce an isolated git history from committed sources and explicit edits."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("destination", nargs="?", type=Path, default=HERE / "todo-app",
                    help="new or empty destination (default examples/todo-app)")
    args = ap.parse_args()
    dest = args.destination.resolve()
    if dest.exists() and any(dest.iterdir()):
        ap.error(f"refusing to overwrite nonempty destination: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LC_ALL="C", TZ="UTC", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    # Prevent caller identity/configuration from changing object IDs.
    for key in list(env):
        if key.startswith("GIT_") and key not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"):
            del env[key]

    def git(*argv, date=None):
        e = dict(env)
        if date:
            e.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.check_output(["git", "-C", str(dest), "-c", "user.name=example-dev", "-c",
                                        "user.email=dev@example.local", "-c", "commit.gpgsign=false",
                                        "-c", "core.autocrlf=false", *argv], env=e, text=True).strip()

    git("init", "-q", "--initial-branch=main", "--object-format=sha1", "--template=")
    shutil.copytree(HERE / "todo-app-src", dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cli = dest / "todo/__main__.py"
    api = dest / "todo/api.py"
    store = dest / "todo/store.py"
    good_cli, good_api, good_store = cli.read_text(), api.read_text(), store.read_text()

    def commit(message, date):
        git("add", "-A")
        git("commit", "-q", "-m", message, date=date)
        return git("rev-parse", "HEAD")

    commit("feat: introduce todo API, CLI and locked JSON store", "2026-09-02T09:00:00+00:00")
    cli.write_text(good_cli.replace('result = store.add(args.title)',
        'rows = store.list_all()\n            result = {"id": len(rows) + 1, "title": args.title, "status": "open"}\n'
        '            rows.append(result)\n            store.path.write_text(json.dumps(rows))'))
    direct_cli = commit("fix: persist CLI additions with direct JSON writes bypassing store.py", "2026-09-07T10:00:00+00:00")
    git("revert", "--no-commit", direct_cli)
    commit('Revert "fix: persist CLI additions with direct JSON writes bypassing store.py"\n\nThis reverts commit ' + direct_cli + '.',
           "2026-09-09T11:00:00+00:00")
    api.write_text(good_api.replace('self.reply(201, self.server.store.add(body.get("title")))',
        'store = self.server.store\n                rows = store.list_all()\n'
        '                row = {"id": len(rows) + 1, "title": body.get("title"), "status": "open"}\n'
        '                rows.append(row)\n                store.path.write_text(json.dumps(rows))\n'
        '                self.reply(201, row)'))
    direct_api = commit("fix: persist API additions with direct JSON writes bypassing store.py", "2026-09-15T09:00:00+00:00")
    api.write_text(good_api)
    commit("fix: remove direct JSON writes bypassing store.py; use Store.add atomic transaction", "2026-09-18T14:00:00+00:00")
    store.write_text(good_store.replace('if TRANSITIONS.get(row["status"]) != target:', 'if target not in ("done", "archived"):'))
    skip = commit("feat: allow fast archive status transition", "2026-09-23T09:00:00+00:00")
    store.write_text(good_store)
    commit("fix: reject status transition open to archived; cannot skip done", "2026-09-25T09:00:00+00:00")
    store.write_text(good_store.replace('if TRANSITIONS.get(row["status"]) != target:',
                                       'if row["status"] != target and TRANSITIONS.get(row["status"]) != target:'))
    repeat = commit("feat: retry completion as idempotent status transition", "2026-10-03T09:00:00+00:00")
    store.write_text(good_store)
    commit("fix: reject repeated status transition done to done; enforce forward-only state machine", "2026-10-06T12:00:00+00:00")
    comments = []
    for num, path, sha, body in [
        (101, "todo/__main__.py", direct_cli, "Direct JSON writes bypassing store.py again: store.path.write_text(json.dumps(rows)) loses concurrent additions. Use Store.add atomic transaction."),
        (102, "todo/api.py", direct_api, "Direct JSON writes bypassing store.py repeat in the HTTP handler. Use Store.add; only store.py owns persistence and locking."),
        (103, "todo/store.py", skip, "Status transition open to archived skips done. Keep the forward-only state machine."),
        (104, "todo/store.py", repeat, "Status transition done to done accepted again. Reject repeated completion in the shared state machine.")]:
        comments.append({"id": num, "path": path, "line": 1, "commit_id": sha, "user": {"login": "reviewer"},
                         "body": body, "created_at": "2026-10-08T09:00:00Z"})
    (dest / "review-comments.json").write_text(json.dumps(comments, indent=2) + "\n")
    (dest / "chat-feedback.md").write_text(
        '# Repeated review feedback\n\n'
        '- Direct JSON writes bypassing store.py happened in the CLI and HTTP handler. Use Store.add atomic transaction and a lint naming that fix.\n'
        '- Direct JSON writes bypassing store.py lose concurrent updates; centralize persistence and locking.\n'
        '- Status transition bugs returned twice: skipping done and accepting done to done. Reject illegal transitions in one state machine.\n')
    commit("docs: retain review and chat evidence of repeated persistence and status mistakes", "2026-10-10T09:00:00+00:00")
    print(json.dumps({"repo": str(dest), "head": git("rev-parse", "HEAD"),
                      "direct_write_commits": [direct_cli, direct_api], "status_bug_commits": [skip, repeat]}, indent=2))


if __name__ == "__main__":
    main()
