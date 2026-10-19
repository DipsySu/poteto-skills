#!/usr/bin/env python3
"""Normalize review feedback into one list where every item names its source.

Inputs:
  --repo REPO           git history: fix commits, reverts, and the commits they revert
  --pr-comments JSON    PR review comments in `gh api` shape (body/path/line/user/html_url);
                        concatenated pages from `gh api --paginate` work too. Repeatable.
  --chat MD             chat or meeting notes in markdown; each bullet or paragraph is one item. Repeatable.

Output (--out, default stdout): {"version", "repo", "generated_at", "inputs", "counts", "items": [...]}
Each item: {id, source (commit|review|chat|code-comment), ref (sha, URL, or file:line), text, paths, line,
author, date, url, kind (commits: fix|revert|reverted), diff_added/diff_removed (commits)}.

Uses the item readers from the sibling `correct` skill (correct/scripts/mine_mistakes.py),
so /correct and /feedback-to-architecture read evidence the same way.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path


def load_mine_mistakes():
    sys.dont_write_bytecode = True  # importing the correct skill must not leave __pycache__ behind
    here = Path(__file__)
    for base in (here.resolve().parent, here.parent):
        cand = base.parent.parent / "correct" / "scripts"
        if (cand / "mine_mistakes.py").is_file():
            sys.path.insert(0, str(cand))
            import mine_mistakes  # noqa: PLC0415
            return mine_mistakes
    sys.exit("collect_feedback: needs the `correct` skill installed next to this one "
             "(skills/correct/scripts/mine_mistakes.py). Run install.sh from poteto-skills.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="collect_feedback.py",
        description="Normalize PR review comments, commit-history signals, and chat notes into one feedback.json "
                    "where every item has a source and a ref.",
        epilog="examples:\n"
               "  gh api repos/OWNER/REPO/pulls/comments --paginate > review-comments.json   # read-only\n"
               "  collect_feedback.py --repo . --pr-comments review-comments.json --chat chat-feedback.md --out feedback.json\n"
               "  collect_feedback.py --repo . --since 2026-07-01 --out feedback.json\n"
               "next: plan.py feedback.json --out-dir refactor/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo", required=True, type=Path, help="repository (git history is read from here)")
    ap.add_argument("--pr-comments", action="append", type=Path, default=[], metavar="JSON",
                    help="PR review comments, gh api JSON shape (repeatable)")
    ap.add_argument("--chat", action="append", type=Path, default=[], metavar="MD",
                    help="chat/meeting notes in markdown (repeatable)")
    ap.add_argument("--since", help="only commits after this date (YYYY-MM-DD or git --since syntax)")
    ap.add_argument("--max-commits", type=int, default=2000, help="newest N commits to read (default 2000)")
    ap.add_argument("--no-git", action="store_true", help="skip commit history")
    ap.add_argument("--code-comments", action="store_true", help="also include HACK/WORKAROUND/FIXME/XXX comments")
    ap.add_argument("--out", type=Path, help="write feedback.json here (default: stdout)")
    args = ap.parse_args(argv)

    mm = load_mine_mistakes()
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"collect_feedback: {repo} is not a directory", file=sys.stderr)
        return 2
    items: list[dict] = []
    git_used = False
    if not args.no_git:
        if mm.is_git_repo(repo):
            items += mm.git_items(repo, args.since, args.max_commits)
            git_used = True
            if args.code_comments:
                items += mm.code_comment_items(repo)
        else:
            print(f"collect_feedback: {repo} is not a git repo; skipping commit history", file=sys.stderr)
    for f in args.pr_comments:
        if not f.is_file():
            print(f"collect_feedback: --pr-comments {f} not found", file=sys.stderr)
            return 2
        got = mm.load_feedback(f, source="review")
        for it in got:
            if it["source"] == "chat":  # a markdown file passed as review comments
                it["source"] = "review"
        items += got
    for f in args.chat:
        if not f.is_file():
            print(f"collect_feedback: --chat {f} not found", file=sys.stderr)
            return 2
        items += mm.markdown_items(f.read_text(encoding="utf-8", errors="replace"), f.name, "chat")

    seen, out_items, dropped = set(), [], 0
    for it in items:
        if not it.get("source") or not it.get("ref"):
            dropped += 1
            continue
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out_items.append(it)
    if dropped:
        print(f"collect_feedback: dropped {dropped} item(s) without a source ref", file=sys.stderr)

    doc = {
        "version": 1,
        "tool": "collect_feedback",
        "repo": str(repo),
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "inputs": {"git": git_used, "since": args.since, "pr_comments": [str(p) for p in args.pr_comments],
                   "chat": [str(p) for p in args.chat]},
        "counts": dict(Counter(it["source"] for it in out_items)),
        "items": out_items,
    }
    text = json.dumps(doc, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        counts = ", ".join(f"{v} {k}" for k, v in sorted(doc["counts"].items())) or "no items"
        print(f"collect_feedback: wrote {args.out} ({counts})", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
