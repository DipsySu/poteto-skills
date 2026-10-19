#!/usr/bin/env python3
"""Check a verify-<app> skill against its contract. Exit 1 on any failure.

Fails when:
  * SKILL.md is missing, has no YAML frontmatter, or its name/description is empty,
    or name != the directory name
  * a required section is missing: Launch, Doctor, Drive, Evidence, Cleanup, Helpers (H2s)
  * features/README.md is missing, or a feature file does not have exactly these H2s in order:
    Sub-features / How to get to it (user POV) / Driving it with <harness> / Gotchas
  * a drive command in a feature's `Driving it with` section still has a `<placeholder>`
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
import subprocess
import sys
from pathlib import Path

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


def recipe_placeholders(text: str) -> list[str]:
    """`<id>`-style placeholders left in commands of the `Driving it with ...` section."""
    m = re.search(r"^##\s+Driving it with[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return []
    found = []
    for span in re.findall(r"`([^`\n]+)`", m.group(1)):
        if re.search(r"\b(drive|curl|control\.sh|python3?|npm|go|make)\b", span):
            found += [p for p in re.findall(r"<[A-Za-z_*][\w*-]*>", span) if p not in found]
    return found


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
    """A proof run left a manifest whose files include a drive record (NNN-<feature>-http|cli.json)."""
    runs = []
    for m in sorted(root.glob("*/manifest.json")) if root.is_dir() else []:
        try:
            files = json.loads(m.read_text(encoding="utf-8")).get("files", [])
        except (OSError, json.JSONDecodeError):
            continue
        drives = [f["path"] for f in files if re.fullmatch(r"\d{3}-(.+-)?(http|cli)\.json", f.get("path", ""))]
        if drives:
            runs.append(f"{m.parent.name} ({len(drives)} drive records)")
    return {"check": "proof run with evidence", "ok": bool(runs),
            "detail": "; ".join(runs[-3:]) if runs else f"no {root}/<run-id>/manifest.json with a drive record: "
                                                         "run the end-to-end proof (control.sh prove) first"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_verify_skill.py",
        description="Check a verify-<app> skill: frontmatter, Launch/Doctor/Drive/Evidence/Cleanup/Helpers sections, "
                    "feature files with the four ordered H2s, live index links, no TODO(verify) left, and executable, "
                    "documented scripts. Exit 1 on any failure.",
        epilog="example:\n  check_verify_skill.py .claude/skills/verify-todo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("skill_dir", type=Path, help="the verify-<app> skill directory")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--require-proof", metavar="EVIDENCE_ROOT", type=Path,
                    help="also require a proof run: some EVIDENCE_ROOT/<run-id>/manifest.json listing at least one "
                         "drive record (e.g. artifacts/verify). A skill whose proof never ran is a draft.")
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
