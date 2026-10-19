#!/usr/bin/env python3
"""patlint: a deterministic, rule-file-driven pattern lint.

Each rule has an id, a regex, include globs, and a message that names the fix
(the file, function, or type to use instead). Run the same command locally and
in CI.

Rules file (JSON):
  {"rules": [{
      "id": "no-direct-json-write",
      "regex": "\\\\bjson\\\\.dump\\\\(",
      "include": ["**/*.py"],
      "exclude": ["todo/store.py", "tests/**"],
      "message": "Write todos through `store.save()` in todo/store.py, never json.dump on the data file.",
      "ignore_case": false,
      "multiline": false
  }]}

Globs: `**/` spans directories, `*` stays inside one path segment, and a pattern
without `/` matches the file name at any depth (like .gitignore). Paths are
matched relative to --root (default: the git toplevel of the current directory).

Ratchet: with --baseline FILE, only (rule, file) counts above the baseline
fail, so an already-common pattern can't grow. --update-baseline records
current counts for new rules and lowers counts that went down; it refuses to
raise a count (exit 1).

Inline exception, on the offending line or the line above it:
  # patlint: allow no-direct-json-write reason=legacy import expires=2026-12-31 approved-by=alice
reason, expires (YYYY-MM-DD), and approved-by are required. An expired or
incomplete exception fails.

Exit codes: 0 clean, 1 violations / expired or invalid exceptions / ratchet refused, 2 bad usage or rules.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "dist", "build", ".next", "target", "vendor", "artifacts"}
MAX_BYTES = 2_000_000
ALLOW_RE = re.compile(r"patlint:\s*allow\s+([A-Za-z0-9][\w.,-]*)(.*)$")
COMMENT_ONLY = re.compile(r"^\s*(#|//|/\*|\*|--|<!--|;|%|\{#|\"\"\"|''')")
FIELD_RE = re.compile(r"(?<![\w-])(reason|expires|approved-by)=")
FIX_HINT = re.compile(r"`[^`]+`|\b\w+\(\)|\b[A-Za-z_]\w*\.[A-Za-z_]\w*|[\w-]+/[\w./-]+")
ID_RE = re.compile(r"[A-Za-z0-9][\w.-]*")


class RulesError(Exception):
    pass


# ---------------------------------------------------------------- rules

def glob_to_regex(pat: str) -> re.Pattern:
    pat = pat.strip()
    anchored = "/" in pat.rstrip("/")
    if pat.startswith("/"):
        pat = pat[1:]
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(pat[i]))
                i += 1
            else:
                cls = pat[i + 1:j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls + "]")
                i = j + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    if pat.endswith("/"):
        body += ".*"
    return re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")


def names_fix(message: str) -> bool:
    return len(message.strip()) >= 10 and bool(FIX_HINT.search(message))


def load_rules(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RulesError(f"cannot read rules file {path}: {e}")
    raw = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise RulesError(f"{path}: expected {{\"rules\": [...]}} or a list of rules")
    rules, errors, seen = [], [], set()
    for n, r in enumerate(raw):
        where = f"{path}: rule #{n + 1}"
        if not isinstance(r, dict):
            errors.append(f"{where}: must be an object")
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
            errors.append(f"{where}: `id` must match [A-Za-z0-9][\\w.-]*")
            continue
        where = f"{path}: rule {rid}"
        if rid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(rid)
        flags = re.I if r.get("ignore_case") else 0
        if r.get("multiline"):
            flags |= re.M | re.S
        try:
            rx = re.compile(r.get("regex") or "", flags)
            if not r.get("regex"):
                raise re.error("empty regex")
        except re.error as e:
            errors.append(f"{where}: bad regex: {e}")
            continue
        inc = r.get("include", ["**"])
        exc = r.get("exclude", [])
        if isinstance(inc, str):
            inc = [inc]
        if isinstance(exc, str):
            exc = [exc]
        if not inc or not all(isinstance(g, str) and g for g in inc + exc):
            errors.append(f"{where}: include/exclude must be non-empty glob strings")
            continue
        msg = r.get("message") or ""
        if not names_fix(msg):
            errors.append(f"{where}: message must name the fix (the file, function, or type to use instead, "
                          f"e.g. 'Use `store.save()` from todo/store.py'), got: {msg!r}")
            continue
        rules.append({"id": rid, "regex": rx, "include": [glob_to_regex(g) for g in inc],
                      "exclude": [glob_to_regex(g) for g in exc], "message": msg,
                      "multiline": bool(r.get("multiline")), "include_globs": inc, "exclude_globs": exc})
    if errors:
        raise RulesError("\n".join(errors))
    if not rules:
        raise RulesError(f"{path}: no rules")
    return rules


def applies(rule: dict, rel: str) -> bool:
    return any(g.match(rel) for g in rule["include"]) and not any(g.match(rel) for g in rule["exclude"])


# ---------------------------------------------------------------- files

def git(cwd: Path, *args: str, binary: bool = False):
    res = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout if binary else res.stdout.decode(errors="replace")


def git_toplevel(cwd: Path) -> Path | None:
    try:
        out = git(cwd, "rev-parse", "--show-toplevel").strip()
        return Path(out) if out else None
    except (RuntimeError, OSError):
        return None


def rel_to(root: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def walk(paths: list[Path], root: Path):
    """Yield (rel_path, reader) for files under PATHS in the working tree."""
    seen = set()
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"path not found: {p}")
        if p.is_file():
            cand = [p]
        else:
            cand = []
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                cand.extend(Path(dirpath) / f for f in sorted(filenames))
        for f in cand:
            rel = rel_to(root, f)
            if rel in seen:
                continue
            seen.add(rel)
            yield rel, (lambda f=f: f.read_bytes())


def walk_rev(paths: list[Path], root: Path, rev: str):
    """Yield (rel_path, reader) for files under PATHS as of git revision REV."""
    top = git_toplevel(root)
    if top is None:
        raise RuntimeError(f"--rev needs a git repo; {root} is not inside one")
    rels = []
    for p in paths:
        ap = p if p.is_absolute() else (Path.cwd() / p)
        try:
            rels.append(ap.resolve().relative_to(top.resolve()).as_posix() or ".")
        except ValueError:
            rels.append(p.as_posix())
    names = git(top, "ls-tree", "-r", "--name-only", rev, "--", *rels).splitlines()
    if not names:
        raise RuntimeError(f"no files match {' '.join(rels)} at {rev}")
    for name in sorted(set(names)):
        yield name, (lambda name=name: git(top, "show", f"{rev}:{name}", binary=True))


# ---------------------------------------------------------------- linting

def parse_allow(line: str):
    m = ALLOW_RE.search(line)
    if not m:
        return None
    ids = [x for x in m.group(1).split(",") if x]
    rest = m.group(2)
    fields = {}
    marks = list(FIELD_RE.finditer(rest))
    for n, fm in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(rest)
        val = rest[fm.end():end].strip()
        val = re.sub(r"\s*(\*/|-->|#}|%}|\"\"\"|''')\s*$", "", val).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        fields[fm.group(1)] = val
    return {"ids": ids, **fields}


def lint_text(rel: str, text: str, rules: list[dict], today: dt.date, report: dict) -> None:
    lines = text.splitlines()
    allows = {}
    for n, ln in enumerate(lines, 1):
        if "patlint:" in ln:
            a = parse_allow(ln)
            if a:
                a["standalone"] = bool(COMMENT_ONLY.match(ln))
                allows[n] = a
    used_allows = set()

    def exception_for(rule_id: str, lineno: int):
        a = allows.get(lineno)
        if a and rule_id in a["ids"]:
            return lineno, a
        a = allows.get(lineno - 1)  # a comment-only line directly above the violation
        if a and a["standalone"] and rule_id in a["ids"]:
            return lineno - 1, a
        return None, None

    for rule in rules:
        if not applies(rule, rel):
            continue
        if rule["multiline"]:
            hits = []
            for m in rule["regex"].finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                col = m.start() - (text.rfind("\n", 0, m.start()) + 1) + 1
                hits.append((lineno, col))
        else:
            hits = [(n, m.start() + 1) for n, ln in enumerate(lines, 1) for m in rule["regex"].finditer(ln)]
        for lineno, col in hits:
            src = lines[lineno - 1].strip() if lineno - 1 < len(lines) else ""
            v = {"rule": rule["id"], "path": rel, "line": lineno, "col": col, "message": rule["message"],
                 "text": src[:200]}
            at, a = exception_for(rule["id"], lineno)
            if a is None:
                report["violations"].append(v)
                continue
            used_allows.add(at)
            missing = [k for k in ("reason", "expires", "approved-by") if not a.get(k)]
            if missing:
                v["exception"] = f"line {at}: missing {', '.join(missing)}"
                report["invalid_exceptions"].append(v)
                continue
            try:
                exp = dt.date.fromisoformat(a["expires"])
            except ValueError:
                v["exception"] = f"line {at}: expires must be YYYY-MM-DD, got {a['expires']!r}"
                report["invalid_exceptions"].append(v)
                continue
            v.update({"reason": a["reason"], "expires": a["expires"], "approved_by": a["approved-by"]})
            if exp < today:
                v["exception"] = f"line {at}: expired {a['expires']}"
                report["expired"].append(v)
            else:
                report["allowed"].append(v)
    for at, a in allows.items():
        if at not in used_allows:
            report["stale_exceptions"].append({"path": rel, "line": at, "ids": a["ids"]})


def load_baseline(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r: dict(files) for r, files in (data.get("counts") or {}).items()}


def write_baseline(path: Path, counts: dict) -> None:
    clean = {r: dict(sorted((p, n) for p, n in files.items() if n > 0)) for r, files in sorted(counts.items())}
    path.write_text(json.dumps({"version": 1, "tool": "patlint",
                                "note": "Ratchet baseline: counts may only go down. Update with --update-baseline.",
                                "counts": clean}, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="patlint.py",
        description="Deterministic pattern lint. Each rule: id, regex, include globs, and a message that names the fix. "
                    "Supports a ratchet baseline and inline exceptions with reason, expiry, and approver.",
        epilog="examples:\n"
               "  patlint.py --rules lint-rules.json src/\n"
               "  patlint.py --rules lint-rules.json --baseline .patlint-baseline.json --update-baseline .   # adopt\n"
               "  patlint.py --rules lint-rules.json --baseline .patlint-baseline.json .                     # CI\n"
               "  patlint.py --rules lint-rules.json --rev abc123^ todo/   # prove the rule fires on a past mistake\n\n"
               "inline exception (same line or the line above):\n"
               "  # patlint: allow RULE_ID reason=why expires=2026-12-31 approved-by=human\n\n"
               "exit: 0 clean, 1 violations/expired or invalid exceptions/ratchet refused, 2 usage or rules error",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--rules", required=True, type=Path, help="rules JSON file")
    ap.add_argument("--baseline", type=Path, help="ratchet baseline JSON (fail only above recorded counts)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="write the baseline: adopt new rules at current counts, lower counts that went down")
    ap.add_argument("--root", type=Path, help="directory globs are relative to (default: git toplevel, else cwd)")
    ap.add_argument("--rev", help="lint files as of this git revision instead of the working tree")
    ap.add_argument("--today", help="date for exception expiry checks (YYYY-MM-DD, default: today)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--verbose", action="store_true", help="also list violations that are within the baseline")
    ap.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="files or directories to lint")
    args = ap.parse_args(argv)

    if args.update_baseline and not args.baseline:
        print("patlint: --update-baseline needs --baseline FILE", file=sys.stderr)
        return 2
    try:
        rules = load_rules(args.rules)
    except RulesError as e:
        print(f"patlint: {e}", file=sys.stderr)
        return 2
    try:
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    except ValueError:
        print("patlint: --today must be YYYY-MM-DD", file=sys.stderr)
        return 2
    root = (args.root or git_toplevel(Path.cwd()) or Path.cwd()).resolve()
    if args.rev:
        root = git_toplevel(root) or root

    report = {"violations": [], "allowed": [], "expired": [], "invalid_exceptions": [], "stale_exceptions": []}
    scanned = []
    try:
        files = walk_rev(args.paths, root, args.rev) if args.rev else walk(args.paths, root)
        for rel, read in files:
            if not any(applies(r, rel) for r in rules):
                continue
            data = read()
            if len(data) > MAX_BYTES or b"\0" in data[:8192]:
                continue
            scanned.append(rel)
            lint_text(rel, data.decode("utf-8", errors="replace"), rules, today, report)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"patlint: {e}", file=sys.stderr)
        return 2

    counts: dict[str, dict[str, int]] = {}
    for v in report["violations"] + report["expired"] + report["invalid_exceptions"]:
        counts.setdefault(v["rule"], {})
        counts[v["rule"]][v["path"]] = counts[v["rule"]].get(v["path"], 0) + 1

    ratchet = args.baseline is not None
    baseline = load_baseline(args.baseline) if ratchet else {}
    over, improved, refused = [], [], []
    if ratchet:
        scanned_set = set(scanned)
        for rid, files in counts.items():
            for path, n in files.items():
                b = baseline.get(rid, {}).get(path, 0)
                if n > b:
                    over.append({"rule": rid, "path": path, "count": n, "baseline": b})
        for rid, files in baseline.items():
            for path, b in files.items():
                n = counts.get(rid, {}).get(path, 0)
                if path in scanned_set and n < b:
                    improved.append({"rule": rid, "path": path, "count": n, "baseline": b})

    if args.update_baseline:
        new = {r: dict(f) for r, f in baseline.items()}
        rule_ids = {r["id"] for r in rules}
        scanned_set = set(scanned)
        for rid in rule_ids:
            cur = counts.get(rid, {})
            if rid not in baseline:
                new[rid] = dict(cur)  # adopting a new rule at its current count
                continue
            for path in scanned_set:
                n, b = cur.get(path, 0), baseline[rid].get(path, 0)
                if n > b:
                    refused.append({"rule": rid, "path": path, "count": n, "baseline": b})
                else:
                    new[rid][path] = n
        new = {r: f for r, f in new.items() if r in rule_ids}
        write_baseline(args.baseline, new)
        over = [o for o in over if o["rule"] in baseline]  # adopted rules are not "over"

    over_keys = {(o["rule"], o["path"]) for o in over}
    if ratchet:
        failing = [v for v in report["violations"] if (v["rule"], v["path"]) in over_keys]
    else:
        failing = report["violations"]
    hard_fail = report["expired"] + report["invalid_exceptions"]
    ok = not failing and not hard_fail and not refused

    if args.json:
        print(json.dumps({"ok": ok, "rules": [r["id"] for r in rules], "files_scanned": len(scanned),
                          "rev": args.rev, "root": str(root), "failing": failing, **report,
                          "baseline": {"path": str(args.baseline) if args.baseline else None, "over": over,
                                       "improved": improved, "refused": refused,
                                       "updated": bool(args.update_baseline)}}, indent=2))
        return 0 if ok else 1

    shown = report["violations"] if (args.verbose or not ratchet) else failing
    for v in shown:
        print(f"{v['path']}:{v['line']}:{v['col']}: {v['rule']}: {v['message']}")
        print(f"    | {v['text']}")
    for v in report["expired"]:
        print(f"{v['path']}:{v['line']}: {v['rule']}: exception {v['exception']} (approved by {v['approved_by']}). "
              f"Fix the violation or get a human to renew it.")
    for v in report["invalid_exceptions"]:
        print(f"{v['path']}:{v['line']}: {v['rule']}: invalid exception, {v['exception']}. "
              f"Format: patlint: allow {v['rule']} reason=... expires=YYYY-MM-DD approved-by=...")
    for o in over:
        print(f"ratchet: {o['rule']} in {o['path']}: {o['count']} found, baseline {o['baseline']}")
    for r in refused:
        print(f"ratchet: refused to raise {r['rule']} in {r['path']} from {r['baseline']} to {r['count']}; fix the new violations")
    for s in report["stale_exceptions"]:
        print(f"note: {s['path']}:{s['line']}: exception for {','.join(s['ids'])} matches nothing; delete it")
    for i in improved:
        print(f"note: {i['rule']} in {i['path']} went {i['baseline']} -> {i['count']}; lock it in with --update-baseline")
    if ratchet and not args.update_baseline and not args.baseline.is_file():
        print(f"note: baseline {args.baseline} does not exist, so every violation counts as new; "
              f"adopt current counts with --update-baseline")
    status = "PASS" if ok else "FAIL"
    within = len(report["violations"]) - len(failing)
    extra = f", {within} within baseline" if ratchet else ""
    print(f"patlint: {status}: {len(failing)} failing violation(s){extra}, {len(report['allowed'])} allowed, "
          f"{len(report['expired'])} expired, {len(report['invalid_exceptions'])} invalid exception(s); "
          f"{len(scanned)} file(s), {len(rules)} rule(s)" + (f" @ {args.rev}" if args.rev else "")
          + (f"; baseline written to {args.baseline}" if args.update_baseline else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
