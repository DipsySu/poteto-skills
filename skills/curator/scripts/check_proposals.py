#!/usr/bin/env python3
"""Check a curator proposals file against the acceptance bar.

  check_proposals.py proposals/YYYY-MM-DD.md [--fetch] [--skills-dir DIR] [--json]

Each proposal is an H2 section (`## 1. Title`) with these bullet fields:
  - Source: <http(s) URL the curator actually fetched>   (one or more URLs)
  - Level: architecture | types | lint | test | verification | ci | prose | review
  - Skill: <existing skill name> | new:<name> | collection
  - Change: <the concrete change, preferably a script, lint, test, or type>
  - Why: <one sentence>                                   (optional)
A file with no H2 sections is valid only with an explicit `No proposals` line.

--fetch re-requests every source URL with curl (the curator is the one skill allowed to use
the network), following redirects. Only a final 2xx passes; 401/403/429 and anything else
fail, since a source must be fetched, not just exist. Exit 0 valid, 1 invalid, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LEVELS = ["architecture", "types", "lint", "test", "verification", "ci", "prose", "review"]
ALIASES = {"static-analysis": "lint", "static analysis": "lint", "docs": "prose", "doc": "prose",
           "codebase": "architecture", "type": "types", "tests": "test", "formal": "verification"}
DETERMINISTIC = {"architecture", "types", "lint", "test", "verification", "ci"}
URL_RE = re.compile(r"https?://[^\s<>)\]`'\"]+")


def parse(text: str) -> list[dict]:
    items = []
    parts = re.split(r"^##\s+", text, flags=re.M)
    for chunk in parts[1:]:
        title, _, body = chunk.partition("\n")
        fields: dict[str, str] = {}
        for m in re.finditer(r"^\s*[-*]\s*\**([A-Za-z][\w ]*?)\**\s*:\s*(.*)$", body, re.M):
            fields.setdefault(m.group(1).strip().lower(), m.group(2).strip())
        items.append({"title": title.strip(), "fields": fields, "urls": URL_RE.findall(fields.get("source", ""))})
    return items


def fetch_status(url: str) -> int:
    try:
        r = subprocess.run(["curl", "-sS", "-L", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "20",
                            "-r", "0-2047", "-A", "poteto-skills-curator/0.1", url],
                           capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def check(path: Path, skills_dir: Path | None, do_fetch: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    items = parse(text)
    skills = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()} if skills_dir and skills_dir.is_dir() else None
    problems, warnings = [], []
    if not items and not re.search(r"^No proposals(?:[.:]|$)", text, re.M):
        problems.append("no proposal sections and no explicit No proposals outcome")
    for n, it in enumerate(items, 1):
        f, where = it["fields"], f"#{n} \"{it['title'][:60]}\""
        if not it["urls"]:
            problems.append(f"{where}: no Source URL (every proposal needs a real, fetched URL)")
        level = ALIASES.get(f.get("level", "").lower(), f.get("level", "").lower())
        if level not in LEVELS:
            problems.append(f"{where}: Level must be one of {', '.join(LEVELS)}, got {f.get('level')!r}")
        it["level"] = level
        if len(f.get("change", "").split()) < 4:
            problems.append(f"{where}: Change must say concretely what to change")
        skill = f.get("skill", "")
        if not skill:
            problems.append(f"{where}: Skill missing (an existing skill, new:<name>, or collection)")
        elif skills is not None and skill not in skills and skill != "collection" and not skill.startswith("new:"):
            problems.append(f"{where}: Skill {skill!r} is not a skill in {skills_dir}")
        if do_fetch:
            it["fetch"] = {}
            for u in it["urls"]:
                code = fetch_status(u)
                it["fetch"][u] = code
                if not 200 <= code < 300:
                    problems.append(f"{where}: {u} is unreachable (HTTP {code or 'error'})")
    det = sum(1 for it in items if it.get("level") in DETERMINISTIC)
    return {"file": str(path), "items": len(items), "deterministic": det, "prose_or_review": len(items) - det,
            "problems": problems, "warnings": warnings, "ok": not problems,
            "proposals": [{"title": it["title"], "level": it.get("level"), "skill": it["fields"].get("skill"),
                           "urls": it["urls"], "fetch": it.get("fetch")} for it in items]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_proposals.py",
        description="Validate a curator proposals file: every proposal has a source URL, a pyramid level, a target "
                    "skill, and a concrete change. --fetch re-checks that each URL is reachable.",
        epilog="examples:\n  check_proposals.py proposals/2026-10-19.md\n"
               "  check_proposals.py proposals/2026-10-19.md --fetch --skills-dir skills",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("proposals", type=Path, help="proposals/YYYY-MM-DD.md")
    ap.add_argument("--fetch", action="store_true", help="re-request each source URL with curl")
    ap.add_argument("--skills-dir", type=Path, help="validate Skill: names against this skills/ dir")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if not args.proposals.is_file():
        print(f"check_proposals: {args.proposals} not found", file=sys.stderr)
        return 2
    res = check(args.proposals, args.skills_dir, args.fetch)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for p in res["problems"]:
            print(f"FAIL  {p}")
        for w in res["warnings"]:
            print(f"warn  {w}")
        print(f"check_proposals: {'PASS' if res['ok'] else 'FAIL'}: {res['items']} proposal(s), "
              f"{res['deterministic']} above prose, {res['prose_or_review']} prose/review ({args.proposals})")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
