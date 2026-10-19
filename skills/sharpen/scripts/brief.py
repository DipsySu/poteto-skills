#!/usr/bin/env python3
"""Write (or check) a pre-work brief for a task.

  brief.py "task" [--repo DIR] [--out BRIEF.md] [--force] [--json]
  brief.py --check BRIEF.md

The brief has four parts: the task in your own words, how a top expert would approach it
(raw sources, method, prior art), whether the task is over- or under-specified, and one
outside check that the result is right. When run inside a git repo it lists files that
match the task's keywords as candidate raw sources, so the expert section starts from
real files, not memory.

--check exits 1 while any TODO remains or the outside check has no runnable command or URL.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

STOP = set("""a an and any are as at be but by can do does for from has have how i if in into is it its make
me my not of on or our please should so some than that the their them then there these this those to too
up use using want we what when where which while who why will with would you your add fix new get set
change update improve make sure also just like need needs task work""".split())


def keywords(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", task)
    out = []
    for w in words:
        lw = w.lower()
        if lw not in STOP and lw not in out:
            out.append(lw)
    return out[:8]


def candidate_files(repo: Path, kws: list[str], limit: int = 8) -> list[tuple[str, int]]:
    if not kws:
        return []
    cmd = ["git", "-C", str(repo), "grep", "-c", "-I", "-i", "-w"]
    for k in kws:
        cmd += ["-e", k]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if res.returncode not in (0, 1):
        return []
    counts = Counter()
    for line in res.stdout.splitlines():
        path, _, n = line.rpartition(":")
        if path and n.isdigit():
            counts[path] += int(n)
    names = Counter()
    for k in kws:  # file names that contain a keyword count double
        for path in counts:
            if k in path.lower():
                names[path] += 1
    ranked = sorted(counts, key=lambda p: (-(counts[p] + 5 * names[p]), p))
    return [(p, counts[p]) for p in ranked[:limit]]


def render(task: str, repo: Path | None) -> tuple[str, dict]:
    kws = keywords(task)
    cands = candidate_files(repo, kws) if repo else []
    src = ", ".join(f"`{p}` ({n} hits)" for p, n in cands) if cands else "none found by keyword; name them yourself"
    today = dt.date.today().isoformat()
    text = f"""# Brief: {task}

Written {today}. Fill in every blank below, then run `brief.py --check` on this file. Keep it short: the point is better work, not a bigger plan.

## In my own words

- What the user is really after: TODO
- The problem behind the request: TODO
- Not the goal (effort that would be wasted): TODO

## How a top expert would approach it

- Raw sources they would read first: TODO
  - Candidates from the repo for {', '.join(kws) or 'the task'}: {src}
- Their method, step by step: TODO
- Prior art or a known-good reference to compare against: TODO

## Specification check

- Over-specified (constraints that don't serve the goal; loosen them): TODO
- Under-specified (decisions that are the user's to make; leave them open or ask): TODO
- Choices I'll make myself, and why they're mine to make: TODO

## Outside check

One check outside my own judgment that shows the result is right: a command, a reference output, a dataset, or a named person.

- Check: TODO
- Command or link: `TODO`
- Pass condition: TODO
"""
    return text, {"task": task, "keywords": kws, "candidate_sources": [{"path": p, "hits": n} for p, n in cands]}


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    errs = []
    todos = [n for n, line in enumerate(text.splitlines(), 1) if re.search(r"\bTODO\b", line)]
    if todos:
        errs.append(f"TODO left on line(s) {', '.join(map(str, todos[:10]))}")
    for head in ("In my own words", "How a top expert would approach it", "Specification check", "Outside check"):
        if not re.search(rf"^##\s+{re.escape(head)}\s*$", text, re.M):
            errs.append(f"missing section `## {head}`")
    m = re.search(r"^##\s+Outside check\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m:
        body = m.group(1)
        runnable = re.search(r"`[^`\n]*[A-Za-z][^`\n]*`", body) or re.search(r"https?://\S+", body)
        if not runnable:
            errs.append("the outside check needs a runnable command in backticks or a URL to compare against")
        if not re.search(r"pass condition:\s*\S", body, re.I):
            errs.append("state the pass condition for the outside check")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="brief.py",
        description="Write a pre-work brief (in your own words, expert approach, over/under-specification, outside "
                    "check), or --check a filled one.",
        epilog="examples:\n"
               "  brief.py \"add json output to the export command\" --out BRIEF.md\n"
               "  brief.py --check BRIEF.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("task", nargs="?", help="the task, in the user's words")
    ap.add_argument("--repo", type=Path, default=Path("."), help="repo to search for raw sources (default: .)")
    ap.add_argument("--out", type=Path, help="write the brief here instead of stdout")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--json", action="store_true", help="print the brief and its metadata as JSON")
    ap.add_argument("--check", type=Path, metavar="BRIEF.md", help="check a filled brief; exit 1 if incomplete")
    args = ap.parse_args(argv)

    if args.check:
        if not args.check.is_file():
            print(f"brief: {args.check} not found", file=sys.stderr)
            return 2
        errs = check(args.check)
        for e in errs:
            print(f"FAIL  {e}")
        print(f"brief: {'PASS' if not errs else 'FAIL'} {args.check}")
        return 0 if not errs else 1
    if not args.task:
        ap.error("give the task in quotes, or --check BRIEF.md")
    repo = args.repo if (args.repo / ".git").exists() or subprocess.run(
        ["git", "-C", str(args.repo), "rev-parse", "--git-dir"], capture_output=True).returncode == 0 else None
    text, meta = render(args.task, repo)
    if args.out:
        if args.out.exists() and not args.force:
            print(f"brief: {args.out} exists; pass --force to overwrite", file=sys.stderr)
            return 1
        args.out.write_text(text, encoding="utf-8")
        meta["out"] = str(args.out)
    if args.json:
        print(json.dumps({**meta, "brief": text}, indent=2))
    elif args.out:
        print(f"brief: wrote {args.out} ({len(meta['candidate_sources'])} candidate sources)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
