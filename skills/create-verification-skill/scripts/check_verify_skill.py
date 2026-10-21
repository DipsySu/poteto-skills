#!/usr/bin/env python3
"""Check a verify-<app> skill against its contract. Exit 1 on any failure.

Fails when:
  * SKILL.md is missing, has no YAML frontmatter, or its name/description is empty,
    or name != the directory name
  * a required section is missing: Launch, Doctor, Drive, Evidence, Cleanup, Helpers (H2s)
  * features/README.md is missing, or a feature file does not have exactly these H2s in order:
    Sub-features / How to get to it (user POV) / Driving it with <harness> / Gotchas
  * a drive command in a feature's `Driving it with` section still has a `<placeholder>`
  * a feature has no asserted drive, or an HTTP/CLI write lacks an associated asserted read-back
  * an index link is dead, or a feature file is not linked from features/README.md
  * a TODO(verify) marker remains anywhere in the skill
  * a file in scripts/ is not executable, has no shebang, fails `bash -n` / py_compile,
    or is never mentioned in SKILL.md (a helper nobody can find is not a helper)

drift_check.py (maintain-verification-skill) imports this module: keep the helpers stable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_SECTIONS = ["Launch", "Doctor", "Drive", "Evidence", "Cleanup", "Helpers"]
FEATURE_H2 = ["Sub-features", "How to get to it (user POV)", "Driving it with <harness>", "Gotchas"]
TODO = "TODO(verify)"
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def frontmatter(text: str) -> dict | None:
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(\n|$)", text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            val = kv.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[kv.group(1)] = val
    return out


def headings(text: str, level: int) -> list[tuple[int, str]]:
    """(line, title) for headings of exactly `level`, ignoring fenced code."""
    out, fence = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m and len(m.group(1)) == level:
            out.append((n, m.group(2)))
    return out


def h2_contract_errors(text: str) -> list[str]:
    """Errors for a feature file's H1 + four ordered H2s."""
    errs = []
    if not headings(text, 1):
        errs.append("missing H1 title")
    got = [t for _, t in headings(text, 2)]
    ok = (len(got) == 4 and got[0] == FEATURE_H2[0] and got[1] == FEATURE_H2[1]
          and re.fullmatch(r"Driving it with \S.*", got[2] or "") is not None and got[3] == FEATURE_H2[3])
    if not ok:
        errs.append(f"H2s must be exactly {FEATURE_H2} in order, got {got}")
    return errs


ASSERT_FLAGS = {"--expect-json", "--expect-text", "--expect-no-text"}
VALUE_FLAGS = ASSERT_FLAGS | {"--feature", "--expect", "--expect-exit", "--write", "--read-back"}


def code_commands(text: str) -> list[list[str]]:
    """Literal commands in inline spans or fenced lines; never execute Markdown."""
    out, fence, pending = [], False, ""
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
            continue
        spans = [line.strip()] if fence else re.findall(r"`([^`\n]+)`", line)
        for code in spans:
            code = pending + code
            if fence and code.endswith("\\"):
                pending = code[:-1] + " "
                continue
            pending = ""
            try:
                out.append(shlex.split(code))
            except ValueError:
                out.append([code])
    return out


def driving_section(text: str) -> str:
    m = re.search(r"^##\s+Driving it with[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def parse_drive(tokens: list[str]) -> dict | None:
    """Parse harness options up to CLI --, preserving application arguments."""
    if "drive" not in tokens:
        return None
    args = tokens[tokens.index("drive") + 1:]
    rec = {"kind": None, "feature": "adhoc", "write": None, "read_back": [], "read_only": False,
           "assertions": [], "argv": [], "positional": [], "errors": []}
    while args:
        arg, args = args[0], args[1:]
        if arg == "--":
            rec["argv"] = args
            break
        if arg in VALUE_FLAGS:
            if not args:
                rec["errors"].append(f"{arg} needs a value")
                break
            value, args = args[0], args[1:]
            if arg in ASSERT_FLAGS:
                rec["assertions"].append((arg, value))
                if arg == "--expect-json" and ("=" not in value or not value.split("=", 1)[0]):
                    rec["errors"].append("--expect-json needs PATH=VALUE")
            elif arg == "--read-back": rec["read_back"].append(value)
            elif arg in ("--feature", "--write"): rec[arg[2:]] = value
        elif arg == "--read-only": rec["read_only"] = True
        elif rec["kind"] is None and arg in ("http", "cli"): rec["kind"] = arg
        elif rec["kind"] == "http" and not arg.startswith("--"): rec["positional"].append(arg)
        else: rec["errors"].append(f"unexpected drive argument {arg}")
    if rec["write"] and (rec["read_only"] or rec["read_back"]):
        rec["errors"].append("a drive cannot be both a write and a read")
    if rec["kind"] == "http" and len(rec["positional"]) in (2, 3) and not rec["argv"]:
        rec["method"], rec["url"] = rec["positional"][:2]
        rec["method"] = rec["method"].upper()
        if (rec["read_only"] or rec["read_back"]) and rec["method"] != "GET":
            rec["errors"].append("HTTP read-backs must use GET")
        rec["read_only"] = rec["method"] == "GET"
    elif rec["kind"] == "cli" and rec["argv"] and not rec["positional"]:
        rec["read_only"] |= bool(rec["read_back"])
    else:
        rec["errors"].append("expected http METHOD PATH [JSON] or cli [flags] -- CMD [ARGS...]")
    return rec


def recipe_drives(text: str) -> list[dict]:
    return [drive for tokens in code_commands(driving_section(text)) if (drive := parse_drive(tokens)) is not None]


def recipe_placeholders(text: str) -> list[str]:
    """Check command operands, excluding literal assertion payloads such as HTML."""
    found = []
    for tokens in code_commands(driving_section(text)):
        if not any(re.search(r"\b(drive|curl|control\.sh|python3?|npm|go|make)\b", t) for t in tokens[:2]):
            continue
        operands, app = [], False
        while tokens:
            token, tokens = tokens[0], tokens[1:]
            if token == "--": app = True
            if not app and token in ASSERT_FLAGS:
                tokens = tokens[1:]
            else:
                operands.append(token)
        for operand in operands:
            found += [p for p in re.findall(r"<[A-Za-z_*][\w*-]*>", operand) if p not in found]
    return found


def second_view_errors(drives: list[dict]) -> list[str]:
    """Pair each declared write with a later asserted read in the same feature."""
    errors, pending = [], {}
    for drive in drives:
        write, reads = drive.get("write"), drive.get("read_back", [])
        feature = drive.get("feature", "adhoc")
        is_read = drive.get("read_only") is True
        mutation = (drive.get("kind") == "http" and drive.get("method") in ("POST", "PUT", "PATCH", "DELETE")) or \
                   (drive.get("kind") == "cli" and not is_read)
        if mutation and not write:
            errors.append("mutation needs --write KEY and a later asserted --read-back KEY")
        if write:
            if is_read or reads:
                errors.append("a write cannot also declare a read")
            key = (feature, write)
            if key in pending:
                errors.append(f"write {write} was not read back before the next write")
            pending[key] = drive
        for label in reads:
            key = (feature, label)
            previous = pending.get(key)
            if not previous:
                errors.append(f"read-back {label} has no preceding write in this feature")
                continue
            if not is_read or not drive.get("assertions"):
                errors.append(f"read-back {label} needs an asserted GET or CLI read command")
                continue
            if previous.get("kind") == drive.get("kind") == "http":
                written = urlsplit(previous.get("url", "")).path.rstrip("/")
                read = urlsplit(drive.get("url", "")).path.rstrip("/")
                if not read or not (written == read or written.startswith(read + "/")):
                    errors.append(f"read-back {label} must observe the written resource, not {read or '/'}")
                    continue
            del pending[key]
    errors += [f"write {label} needs a later asserted --read-back {label}" for _, label in pending]
    return errors


def feature_files(skill: Path) -> list[Path]:
    fdir = skill / "features"
    return sorted(p for p in fdir.glob("*.md") if p.name.lower() != "readme.md") if fdir.is_dir() else []


def local_links(text: str) -> list[str]:
    out = []
    for target in LINK_RE.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        out.append(target.split("#", 1)[0])
    return [t for t in out if t]


def index_report(skill: Path) -> dict:
    """missing (feature file not indexed), dead (link to nothing), duplicate, extra (link to a non-feature file)."""
    readme = skill / "features" / "README.md"
    rep = {"missing": [], "dead": [], "duplicate": [], "extra": []}
    if not readme.is_file():
        return rep
    feats = {p.resolve() for p in feature_files(skill)}
    seen = set()
    for target in local_links(readme.read_text(encoding="utf-8", errors="replace")):
        p = (readme.parent / target).resolve()
        if not p.exists():
            rep["dead"].append(target)
        elif p in seen and p in feats:
            rep["duplicate"].append(target)
        elif p not in feats and p.suffix == ".md" and p.parent == readme.parent.resolve():
            rep["extra"].append(target)
        seen.add(p)
    rep["missing"] = sorted(p.name for p in feats if p not in seen)
    return rep


def check(skill: Path) -> list[dict]:
    """Return a list of {check, ok, detail}."""
    results: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "ok": ok, "detail": detail})

    sk = skill / "SKILL.md"
    if not sk.is_file():
        add("SKILL.md exists", False, f"{sk} not found")
        return results
    text = sk.read_text(encoding="utf-8", errors="replace")
    fm = frontmatter(text)
    if fm is None:
        add("frontmatter", False, "SKILL.md must start with --- YAML frontmatter --- (without it the skill never registers)")
    else:
        missing = [k for k in ("name", "description") if not fm.get(k)]
        add("frontmatter", not missing, "missing " + ", ".join(missing) if missing else f"name={fm['name']}")
        if fm.get("name"):
            add("name matches dir", fm["name"] == skill.name, f"name={fm['name']} dir={skill.name}")
    h2 = [t for _, t in headings(text, 2)]
    for sec in REQUIRED_SECTIONS:
        present = any(t == sec or t.startswith(sec + " ") or t.startswith(sec + ":") for t in h2)
        add(f"section {sec}", present, "" if present else f"add a `## {sec}` section")
    for target in local_links(text):
        p = (skill / target)
        add(f"SKILL.md link {target}", p.exists(), "" if p.exists() else "dead link")

    readme = skill / "features" / "README.md"
    add("features/README.md exists", readme.is_file(), "" if readme.is_file() else "create the feature map index")
    feats = feature_files(skill)
    add("feature files", bool(feats), f"{len(feats)} file(s)" if feats else "add one file per user-facing feature")
    for f in feats:
        ftext = f.read_text(encoding="utf-8", errors="replace")
        errs = h2_contract_errors(ftext)
        add(f"contract {f.name}", not errs, "; ".join(errs))
        holes = recipe_placeholders(ftext)
        add(f"literal recipes {f.name}", not holes,
            f"placeholder(s) {', '.join(holes)} in a drive command; write the real value" if holes else "")
        drives = recipe_drives(ftext)
        errors = [error for drive in drives for error in drive['errors']]
        errors += second_view_errors(drives)
        if not any(drive['assertions'] for drive in drives):
            errors.append("add --expect-json, --expect-text, or --expect-no-text to a drive")
        add(f"asserted recipes {f.name}", not errors, "; ".join(errors))
    idx = index_report(skill)
    for kind in ("dead", "missing", "duplicate", "extra"):
        vals = idx[kind]
        add(f"index {kind}", not vals, ", ".join(vals) if vals else "")

    todos = []
    for p in sorted(skill.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo") and p.stat().st_size < 2_000_000:
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if TODO in line:
                    todos.append(f"{p.relative_to(skill).as_posix()}:{n}")
    add("no TODO(verify) left", not todos, (f"{len(todos)}: " + ", ".join(todos[:12]) + (" …" if len(todos) > 12 else ""))
        if todos else "")

    scripts = sorted(p for p in (skill / "scripts").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")) if (skill / "scripts").is_dir() else []
    control = skill / 'scripts/control.sh'
    add("assertion-capable harness", control.is_file() and "--expect-json" in control.read_text(encoding="utf-8", errors="replace"),
        "regenerate control.sh with scaffold.py, then reapply the app's CONFIG values if assertion flags are missing")
    for s in scripts:
        rel = s.relative_to(skill).as_posix()
        execable = os.access(s, os.X_OK)
        add(f"executable {rel}", execable, "" if execable else f"chmod +x {rel}")
        head = s.read_bytes()[:2]
        add(f"shebang {rel}", head == b"#!", "" if head == b"#!" else "first line must be #!/usr/bin/env ...")
        if s.suffix in (".sh", ".bash"):
            r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
            add(f"bash -n {rel}", r.returncode == 0, r.stderr.strip()[:200])
        if s.suffix == ".py":
            try:
                compile(s.read_bytes(), str(s), "exec")
                add(f"compile {rel}", True)
            except (SyntaxError, ValueError) as e:
                add(f"compile {rel}", False, str(e)[:200])
        mentioned = rel in text or s.name in text
        add(f"documented {rel}", mentioned, "" if mentioned else "show its invocation in SKILL.md (Helpers)")
    return results


def proof_result(root: Path) -> dict:
    """Open manifested drive records; require execution success, assertions, and read-backs."""
    runs = []
    for m in sorted(root.glob("*/manifest.json")) if root.is_dir() else []:
        try:
            manifest = json.loads(m.read_text(encoding="utf-8"))
            files = manifest["files"]
            if not isinstance(files, list):
                continue
            paths = [f["path"] for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)
                     and re.fullmatch(r"\d{3,}-(?:[A-Za-z0-9_-]+-)?(?:http|cli)\.json", f["path"])]
            drives = []
            for path in sorted(paths):
                record_path = m.parent / path
                if record_path.is_symlink():
                    raise ValueError("drive record is a symlink")
                drive = json.loads(record_path.read_text(encoding="utf-8"))
                assertions = drive.get("assertions")
                if (drive.get("ok") is not True or drive.get("execution_ok") is not True or
                        not isinstance(assertions, list) or not assertions or
                        any(not isinstance(a, dict) or a.get("ok") is not True or not a.get("expr") or 'got' not in a
                            for a in assertions)):
                    raise ValueError("drive failed execution or has missing/failed assertions")
                if drive.get('kind') == 'http':
                    status, expected = drive.get('status', 0), drive.get('expect')
                    if type(status) is not int or status == 0 or not (str(status) == str(expected) if expected is not None else 200 <= status < 400):
                        raise ValueError("HTTP status failed")
                elif drive.get('kind') == 'cli':
                    if type(drive.get('exit_code')) is not int or drive['exit_code'] != drive.get('expect_exit', 0):
                        raise ValueError("CLI exit failed")
                else:
                    raise ValueError("unknown drive kind")
                drives.append(drive)
            if second_view_errors(drives):
                continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if drives:
            runs.append(f"{m.parent.name} ({len(drives)} drive records)")
    return {"check": "proof run with evidence", "ok": bool(runs),
            "detail": "; ".join(runs[-3:]) if runs else f"no {root}/<run-id>/manifest.json with successful, asserted drive records "
                                                         "and mutation read-backs: run the end-to-end proof first"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_verify_skill.py",
        description="Check a verify-<app> skill: frontmatter, Launch/Doctor/Drive/Evidence/Cleanup/Helpers sections, "
                    "asserted feature recipes with write read-backs and the four ordered H2s, live index links, no TODO(verify) left, and executable, "
                    "documented scripts. Exit 1 on any failure.",
        epilog="example:\n  check_verify_skill.py .claude/skills/verify-todo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("skill_dir", type=Path, help="the verify-<app> skill directory")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--require-proof", metavar="EVIDENCE_ROOT", type=Path,
                    help="also open the drive records in a proof manifest: every drive must pass execution and "
                         "nonempty assertions, with read-backs for mutations (e.g. artifacts/verify).")
    args = ap.parse_args(argv)
    if not args.skill_dir.is_dir():
        print(f"check_verify_skill: {args.skill_dir} is not a directory", file=sys.stderr)
        return 2
    results = check(args.skill_dir)
    if args.require_proof is not None:
        results.append(proof_result(args.require_proof))
    failed = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({"skill_dir": str(args.skill_dir), "ok": not failed, "failed": len(failed),
                          "results": results}, indent=2))
    else:
        for r in results:
            if not r["ok"]:
                print(f"FAIL  {r['check']}: {r['detail']}")
        print(f"check_verify_skill: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)} checks)"
              f" {args.skill_dir}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
