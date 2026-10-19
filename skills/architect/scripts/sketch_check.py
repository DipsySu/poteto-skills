#!/usr/bin/env python3
"""Check a design sketch before any code is written.

  sketch_check.py SKETCH.md [--json]
  sketch_check.py --template        # print the sketch template

Fails (exit 1) unless the sketch has:
  * at least two candidates (`## Candidate A: ...`, `## Option 2 ...`), each with a sketch:
    a code block declaring types/signatures, or a module map of file paths
  * structurally distinct candidates: no pair declares nearly the same symbols and modules
    (Jaccard >= 0.8) or carries an identical code block
  * a red-flags screen (a heading with "red flag") that rates every candidate, as a table
    column or a labeled bullet, with at least two flags
  * a synthesis decision (a heading with "synthesis" or "decision") that names the chosen
    candidate and gives a reason (because / since / over / instead of / trade-off ...)
Warns when there is no caller-first usage section or a candidate's code has no stub bodies.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

CAND_RE = re.compile(r"^(#{2,4})\s+(?:Candidate|Option|Design|Alternative|Sketch)\s+([A-Z]|\d+|[IVX]+)\b[\s:.)\-—–]*(.*)$", re.I)
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
DECL_RES = [
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:data\s+)?class\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum|struct|trait|protocol|record|module)\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_]\w*)\s*=\s*(?:\(|async|function)", re.M),
    re.compile(r"^\s*([A-Z][A-Za-z0-9_]*)\s*=\s*(?:Literal|Enum|NewType|TypedDict|Union)\b", re.M),
]
MODULE_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|go|rs|rb|java|kt|swift|cs)|[\w-]+\.(?:py|ts|tsx|js|go|rs|rb))\b")
STUB_RE = re.compile(r"NotImplementedError|not implemented|todo!\(\)|unimplemented!\(\)|^\s*\.\.\.\s*$|:\s*\.\.\.\s*$|^\s*pass\s*$|"
                     r"panic\(\"not implemented|throw new Error\(['\"]not implemented", re.I | re.M)
REASON_RE = re.compile(r"\b(because|since|so that|trade-?offs?|over|rather than|instead of|wins?|cheaper|simpler|deeper|"
                       r"fewer|smaller|removes?|eliminates?)\b", re.I)
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)


def sections(text: str) -> list[dict]:
    """Split markdown into heading sections (ignores headings inside fences)."""
    lines = text.splitlines()
    out, fence = [], False
    for n, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = HEAD_RE.match(line)
        if m:
            out.append({"level": len(m.group(1)), "title": m.group(2).strip(), "start": n})
    for i, s in enumerate(out):
        end = len(lines)
        for t in out[i + 1:]:
            if t["level"] <= s["level"]:
                end = t["start"]
                break
        s["body"] = "\n".join(lines[s["start"] + 1:end])
        s["line"] = s["start"] + 1
    return out


def label_mentioned(label: str, text: str) -> bool:
    lab = re.escape(label)
    pats = [rf"\b(?i:candidate|option|design|alternative|sketch)\s+{lab}\b", rf"\|\s*\**{lab}\**\s*\|",
            rf"\*\*{lab}\*\*", rf"^\s*[-*+]\s*\**{lab}\**\s*[:—–-]", rf"\({lab}\)", rf"^\s*{lab}\s*[:—–]"]
    return any(re.search(p, text, re.M) for p in pats)


def analyze(text: str) -> dict:
    secs = sections(text)
    cands = []
    for s in secs:
        m = CAND_RE.match("#" * s["level"] + " " + s["title"])
        if m:
            code = "\n".join(FENCE_RE.findall(s["body"]))
            decls = set()
            for rx in DECL_RES:
                decls.update(x.lower() for x in rx.findall(code))
            modules = {x.lower() for x in MODULE_RE.findall(s["body"])}
            cands.append({"label": m.group(2).upper(), "title": m.group(3).strip(), "line": s["line"],
                          "decls": sorted(decls), "modules": sorted(modules), "code": code.strip(),
                          "stubs": bool(STUB_RE.search(code)), "has_shape": bool(re.search(r"\bshape\b", s["body"], re.I))})
    checks, warnings = [], []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("at least two candidates", len(cands) >= 2,
        f"found {len(cands)}: " + ", ".join(c["label"] for c in cands) if cands else
        "add `## Candidate A: <shape>` and `## Candidate B: <shape>` sections")
    for c in cands:
        has = bool(c["decls"] or c["modules"])
        add(f"candidate {c['label']} has a sketch", has,
            f"{len(c['decls'])} declarations, {len(c['modules'])} modules" if has else
            "add a code block with types/signatures (stub bodies) or a module map with file paths")
        if c["code"] and not c["stubs"]:
            warnings.append(f"candidate {c['label']}: code has no stub bodies (raise NotImplementedError / ... / todo!()); "
                            "sketch signatures, not implementations")
    pairs = []
    for a, b in combinations(cands, 2):
        sa, sb = set(a["decls"]) | set(a["modules"]), set(b["decls"]) | set(b["modules"])
        jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0
        same_code = bool(a["code"]) and a["code"] == b["code"]
        ok = jac < 0.8 and not same_code
        pairs.append({"pair": f"{a['label']}/{b['label']}", "jaccard": round(jac, 2), "identical_code": same_code})
        add(f"{a['label']} and {b['label']} structurally distinct", ok,
            f"symbol/module overlap {jac:.2f}" + (", identical code" if same_code else "") +
            ("" if ok else ": design whole-shape alternatives (ownership, module boundaries, data flow), not point fixes"))

    red = next((s for s in secs if re.search(r"red[- ]?flag", s["title"], re.I)), None)
    if red is None:
        add("red-flags screen", False, "add `## Red-flags screen` rating every candidate (see references/design-red-flags.md)")
    else:
        unrated = [c["label"] for c in cands if not label_mentioned(c["label"], red["body"])]
        rows = len(re.findall(r"^\s*\|(?!\s*-)(?:[^|\n]*\|){2,}", red["body"], re.M)) - 1
        bullets = len(re.findall(r"^\s*[-*+]\s+\S", red["body"], re.M))
        enough = rows >= 2 or bullets >= 2
        add("red-flags screen", not unrated and enough,
            ("unrated: " + ", ".join(unrated) + ". " if unrated else "") +
            ("" if enough else "screen at least two flags (table rows or bullets)"))
    syn = next((s for s in secs if re.search(r"synthesis|decision", s["title"], re.I)), None)
    if syn is None:
        add("synthesis decision", False, "add `## Synthesis decision`: the chosen candidate, what it borrows, what was rejected and why")
    else:
        body = syn["body"].strip()
        named = [c["label"] for c in cands if label_mentioned(c["label"], body)]
        reason = bool(REASON_RE.search(body))
        words = len(body.split())
        ok = bool(named) and reason and words >= 12
        detail = []
        if not named:
            detail.append("name the chosen candidate (e.g. 'Candidate B')")
        if not reason:
            detail.append("give the reason (because / over / instead of / trade-off)")
        if words < 12:
            detail.append(f"only {words} words")
        add("synthesis decision", ok, "; ".join(detail) if detail else "chooses " + ", ".join(named))
    if not any(re.search(r"usage|caller|call site", s["title"], re.I) for s in secs):
        warnings.append("no caller-first usage section (`## Usage`): write the call sites first, derive the types from them")
    return {"candidates": [{k: v for k, v in c.items() if k != "code"} for c in cands], "pairs": pairs,
            "checks": checks, "warnings": warnings, "ok": all(c["ok"] for c in checks)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sketch_check.py",
        description="Check a design sketch: >= 2 structurally distinct candidates with type/signature sketches, a "
                    "red-flags screen rating each candidate, and a synthesis decision with a reason. Exit 1 on failure.",
        epilog="examples:\n  sketch_check.py --template > SKETCH.md\n  sketch_check.py SKETCH.md\n  sketch_check.py SKETCH.md --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("sketch", nargs="?", type=Path, help="the sketch markdown file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--template", action="store_true", help="print references/sketch-template.md and exit")
    args = ap.parse_args(argv)
    if args.template:
        tpl = Path(__file__).resolve().parent.parent / "references" / "sketch-template.md"
        sys.stdout.write(tpl.read_text(encoding="utf-8"))
        return 0
    if args.sketch is None:
        ap.error("SKETCH.md is required (or pass --template)")
    if not args.sketch.is_file():
        print(f"sketch_check: {args.sketch} not found; start from `sketch_check.py --template`", file=sys.stderr)
        return 2
    res = analyze(args.sketch.read_text(encoding="utf-8", errors="replace"))
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for c in res["checks"]:
            print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']}" + (f": {c['detail']}" if c["detail"] else ""))
        for w in res["warnings"]:
            print(f"warn  {w}")
        print(f"sketch_check: {'PASS' if res['ok'] else 'FAIL'} {args.sketch}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
