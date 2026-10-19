#!/usr/bin/env python3
"""Check or extend the `| Rule | Enforced by |` table in an agent instruction file.

  rule_table.py check AGENTS.md [--json]
  rule_table.py add AGENTS.md --rule TEXT --enforced-by TEXT

`check` flags every row whose enforcement is prose only (docs, AGENTS.md, "code
review", empty) and every row that names an enforcer file that does not exist.
Nothing fails when an agent skips prose, so a flagged rule that gets broken again
is a repeat: fix it higher up (architecture > types > lint that names the fix >
test). Exit 0 when every row is enforced by something that runs, 1 otherwise,
2 on usage errors.

`add` appends a row to the first rule table (creating a `## Rules` section if the
file has none) and prints how the new row classifies.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

NONE_VALUES = {"", "-", "--", "—", "–", "none", "n/a", "na", "nothing", "tbd", "todo", "?", "nobody"}

# Words that name something that runs and fails. Matched case-insensitively.
CHECK_WORDS = re.compile(
    r"\b(lint(?:er|s|ed|ing)?|eslint|ruff|flake8|pylint|mypy|pyright|tsc|clippy|"
    r"typecheck(?:er|ing)?|types?|typed|enum|newtype|tests?|unittest|pytest|jest|vitest|"
    r"go test|cargo test|ci|pre-commit|git hook|hook|schema|compiler|compile[sd]?|patlint|"
    r"import-linter|codemod|assert(?:ion)?s?|runtime check|architecture|module boundary|"
    r"private module|bounded_check|tla\+|model[- ]check(?:er)?|kani|verify-[\w-]+|check_[\w]+)\b",
    re.I,
)
# Words that only describe a human or an agent remembering to do something.
PROSE_WORDS = re.compile(
    r"\b(prose|docs?|documentation|agents\.md|claude\.md|readme|wiki|guidelines?|conventions?|"
    r"code review|reviews?|reviewers?|manual(?:ly)?|honou?r system|agent rules?|this file|"
    r"comments?|memory|instructions?|skills?|reminders?|by hand)\b",
    re.I,
)
CODE_SPAN = re.compile(r"`([^`]+)`")
PATHLIKE = re.compile(r"^(?:\./)?[\w.@-]+(?:/[\w.@-]+)*\.(?:py|sh|bash|js|mjs|cjs|ts|tsx|go|rs|rb|java|kt|json|toml|ya?ml|cfg|ini)$")
TOOL_PREFIX = re.compile(r"^(python3?|node|npx|npm|pnpm|yarn|make|go|cargo|bash|sh|\./|uv|tox|nox|pytest|ruff|mypy|tsc|eslint)\b")


def split_row(line: str) -> list[str]:
    """Split a markdown table row on unescaped pipes outside code spans."""
    cells, buf, in_code, i = [], [], False, 0
    s = line.strip()
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    if cells and cells[0] == "" and s.startswith("|"):
        cells = cells[1:]
    if cells and cells[-1] == "" and s.endswith("|"):
        cells = cells[:-1]
    return cells


def is_separator(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def find_tables(lines: list[str]) -> list[dict]:
    """Return every table whose header has a Rule column and an Enforced-by column."""
    tables = []
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        if line.lstrip().startswith("|") and is_separator(lines[i + 1]):
            header = [c.lower() for c in split_row(line)]
            rule_idx = next((k for k, c in enumerate(header) if re.match(r"^rules?\b", c)), None)
            enf_idx = next((k for k, c in enumerate(header) if "enforc" in c), None)
            j = i + 2
            rows = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append((j + 1, split_row(lines[j])))
                j += 1
            if rule_idx is not None and enf_idx is not None:
                tables.append({"header_line": i + 1, "last_line": j, "rule_idx": rule_idx,
                               "enf_idx": enf_idx, "ncols": len(header), "rows": rows})
            i = j
            continue
        i += 1
    return tables


def git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return Path(out) if out else None
    except (OSError, subprocess.CalledProcessError):
        return None


def referenced_paths(text: str) -> list[str]:
    """Path-like tokens inside code spans, including script paths inside commands."""
    found = []
    for span in CODE_SPAN.findall(text):
        for tok in span.split():
            tok = tok.strip("'\"(),;")
            if PATHLIKE.match(tok) or ("/" in tok and PATHLIKE.match(tok.split("::")[0])):
                found.append(tok.split("::")[0])
    return found


def classify(enforced_by: str, bases: list[Path]) -> tuple[str, str]:
    """Return (status, reason). status is enforced | prose | none | missing-enforcer."""
    text = enforced_by.strip()
    if text.strip("`*_ ").lower() in NONE_VALUES:
        return "none", "nothing enforces this rule"
    paths = referenced_paths(text)
    missing = [p for p in paths if not any((b / p).exists() for b in bases)]
    if missing:
        return "missing-enforcer", "enforcer not found: " + ", ".join(missing)
    spans = CODE_SPAN.findall(text)
    runnable_span = any(TOOL_PREFIX.match(s.strip()) or PATHLIKE.match(s.strip()) for s in spans)
    if PROSE_WORDS.search(text) and not runnable_span:
        return "prose", "enforced only by prose or human attention"
    if paths or runnable_span or CHECK_WORDS.search(text):
        return "enforced", "names a check that runs" if not paths else "runs " + ", ".join(paths)
    if PROSE_WORDS.search(text):
        return "prose", "enforced only by prose or human attention"
    return "prose", "names no check that runs (lint, type, test, CI, script)"


def check(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tables = find_tables(lines)
    bases = [path.parent.resolve()]
    top = git_toplevel(path.parent.resolve())
    if top and top not in bases:
        bases.append(top)
    rows = []
    for t in tables:
        for lineno, cells in t["rows"]:
            if len(cells) <= max(t["rule_idx"], t["enf_idx"]):
                cells = cells + [""] * (max(t["rule_idx"], t["enf_idx"]) + 1 - len(cells))
            rule = cells[t["rule_idx"]]
            enf = cells[t["enf_idx"]]
            if not rule.strip():
                continue
            status, reason = classify(enf, bases)
            rows.append({"line": lineno, "rule": rule, "enforced_by": enf, "status": status, "reason": reason})
    flagged = [r for r in rows if r["status"] != "enforced"]
    return {"file": str(path), "tables": len(tables), "rules": rows, "flagged": len(flagged),
            "ok": bool(tables) and not flagged}


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def add(path: Path, rule: str, enforced_by: str) -> dict:
    row = f"| {escape_cell(rule)} | {escape_cell(enforced_by)} |"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = [f"# {path.stem}", ""]
    tables = find_tables(lines)
    if tables:
        t = tables[0]
        cells = [""] * t["ncols"]
        cells[t["rule_idx"]] = escape_cell(rule)
        cells[t["enf_idx"]] = escape_cell(enforced_by)
        row = "| " + " | ".join(cells) + " |"
        lines.insert(t["last_line"], row)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["## Rules", "", "| Rule | Enforced by |", "|---|---|", row]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bases = [path.parent.resolve()]
    top = git_toplevel(path.parent.resolve())
    if top:
        bases.append(top)
    status, reason = classify(enforced_by, bases)
    return {"file": str(path), "row": row, "status": status, "reason": reason}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rule_table.py",
        description="Check or extend the `| Rule | Enforced by |` table in AGENTS.md (or CLAUDE.md). "
                    "`check` exits 1 when a rule is enforced only by prose or names an enforcer that does not exist.",
        epilog="examples:\n"
               "  rule_table.py check AGENTS.md\n"
               "  rule_table.py check AGENTS.md --json\n"
               "  rule_table.py add AGENTS.md --rule 'Write todos only through store.save()' \\\n"
               "      --enforced-by '`python3 scripts/patlint.py --rules lint-rules.json todo/`'\n\n"
               "Enforced: names a lint, type, test, CI job, hook, schema, or an existing script/test file in backticks.\n"
               "Flagged:  prose, docs, AGENTS.md, code review, empty, or a backticked path that does not exist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="flag rules enforced only by prose (exit 1 if any)")
    c.add_argument("file", type=Path, help="agent instruction file, e.g. AGENTS.md")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    a = sub.add_parser("add", help="append a row to the rule table")
    a.add_argument("file", type=Path)
    a.add_argument("--rule", required=True, help="the rule, phrased as what to do")
    a.add_argument("--enforced-by", required=True, help="what fails when the rule is broken (command, test, lint id)")
    a.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        if not args.file.is_file():
            print(f"rule_table: {args.file} not found. Create it with `rule_table.py add {args.file} --rule ... --enforced-by ...`.",
                  file=sys.stderr)
            return 2
        res = check(args.file)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if not res["tables"]:
                print(f"{args.file}: no `| Rule | Enforced by |` table. Add one: every rule paired with the check that fails when it is broken.")
            else:
                print(f"{args.file}: {len(res['rules'])} rules, {res['flagged']} flagged")
                for r in res["rules"]:
                    tag = {"enforced": "OK", "prose": "PROSE", "none": "NONE", "missing-enforcer": "MISSING"}[r["status"]]
                    print(f"  {tag:<8} line {r['line']:<4} {r['rule'][:70]}")
                    if r["status"] != "enforced":
                        print(f"           enforced by: {r['enforced_by'] or '(empty)'} -> {r['reason']}")
                if res["flagged"]:
                    print("FAIL: move each flagged rule up the pyramid (architecture > types > lint that names the fix > test), "
                          "then name that check in the Enforced by column. Drop rules whose mistake can no longer happen.")
                else:
                    print("PASS: every rule names a check that runs.")
        return 0 if res["ok"] else 1

    res = add(args.file, args.rule, args.enforced_by)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"added to {res['file']}: {res['row']}")
        if res["status"] != "enforced":
            print(f"warning: {res['status']}: {res['reason']}. This rule will fail `rule_table.py check`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
