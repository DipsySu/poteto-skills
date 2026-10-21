#!/usr/bin/env python3
"""Find drift between a verify-<app> skill's feature map and the app's source.

  drift_check.py SKILL_DIR --repo REPO [--json]

Reports, and exits 1 if any are found:
  index     features/README.md hygiene: missing (feature file not indexed), dead (link to nothing),
            duplicate, extra (links to a non-feature file)
  contract  feature files without exactly the four H2s in order
  routes    missing: HTTP routes in source that no feature file mentions
            dead:    `METHOD /path` mentions in the map that match no route in source
  commands  missing: CLI subcommands in source that no feature file mentions
            dead:    `<prog> <subcommand>` mentions in the map that the source does not define

Health routes and launch commands (serve/--port) count as covered when SKILL.md or
scripts/ mention them (Launch and Doctor use them; they are not user features).
Mentions are read from `METHOD /path` text, curl command lines, `control.sh drive ... http
METHOD PATH`, and code spans or fenced blocks that invoke the CLI.

Source routes and commands come from create-verification-skill/scripts/scan_repo.py, so
this tool needs that skill installed next to this one. A clean result is not a clean
pass: the live pass in SKILL.md is still required.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path


def sibling_scripts() -> Path:
    here = Path(__file__)
    for base in (here.resolve().parent, here.parent):
        cand = base.parent.parent / "create-verification-skill" / "scripts"
        if (cand / "scan_repo.py").is_file() and (cand / "check_verify_skill.py").is_file():
            return cand
    sys.exit("drift_check: needs the create-verification-skill skill installed next to this one "
             "(skills/create-verification-skill/scripts/scan_repo.py). Run install.sh from poteto-skills.")


sys.dont_write_bytecode = True  # importing the sibling skill must not leave __pycache__ behind
sys.path.insert(0, str(sibling_scripts()))
import check_verify_skill as cvs  # noqa: E402
import scan_repo  # noqa: E402

METHOD_PATH = re.compile(r"(?<![\w/])(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[\w\-./<>{}:*$~%]*)")
CURL_METHOD = re.compile(r"(?:-X|--request)\s*['\"]?([A-Za-z]+)")
URL = re.compile(r"https?://[^\s'\"`]+")


def code_lines(text: str) -> list[tuple[int, str]]:
    """(line, code) for inline code spans and fenced code lines."""
    out, fence = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
            continue
        if fence:
            out.append((n, line))
            continue
        out.extend((n, m.group(1)) for m in re.finditer(r"`([^`]+)`", line))
    return out


def norm_mention(path: str) -> str:
    path = path.split("?", 1)[0].rstrip(".,;:)'\"")
    path = re.sub(r"\$\{?(\w+)\}?", r"<\1>", path)
    path = re.sub(r"\{(\w+)\}", r"<\1>", path)
    path = re.sub(r"(?<=/):(\w+)", r"<\1>", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def route_mentions(text: str) -> list[tuple[str, str, int]]:
    """(METHOD, path, line) found in prose, code, curl lines, and control.sh drive lines."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in METHOD_PATH.finditer(line):
            found.append((m.group(1), norm_mention(m.group(2)), n))
    for n, code in code_lines(text):
        if not re.search(r"\bcurl\b", code):
            continue
        u = URL.search(code)
        if not u:
            continue
        method = CURL_METHOD.search(code)
        meth = method.group(1).upper() if method else ("POST" if re.search(r"\s(-d|--data\S*|--json)\b", code) else "GET")
        path = re.sub(r"^https?://[^/]+", "", u.group(0)) or "/"
        found.append((meth, norm_mention(path), n))
    return found


def split_tokens(code: str) -> list[str]:
    try:
        return shlex.split(code)
    except ValueError:
        return code.split()


def prog_tokens(prog: str) -> list[str]:
    toks = split_tokens(prog)
    while toks and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", toks[0]):
        toks = toks[1:]  # PYTHONPATH=src python3 -m pkg
    return toks


def command_mentions(text: str, progs: dict[str, dict]) -> list[tuple[str, str, int]]:
    """(prog, subcommand, line) for each CLI invocation in code spans and fences."""
    found = []
    for n, code in code_lines(text):
        toks = split_tokens(code)
        for prog, info in progs.items():
            for seq in info["seqs"]:
                k = len(seq)
                for i in range(len(toks) - k + 1):
                    if toks[i:i + k] != seq:
                        continue
                    j = i + k
                    while j < len(toks) and toks[j].startswith("-"):
                        flag = toks[j]
                        j += 1 if ("=" in flag or flag not in info["value_flags"]) else 2
                    if j < len(toks) and re.fullmatch(r"[a-z][\w:-]*", toks[j]):
                        found.append((prog, toks[j], n))
    return found


def drift(skill: Path, repo: Path) -> dict:
    inv = scan_repo.scan(repo)
    fdir = skill / "features"
    feats = cvs.feature_files(skill)
    feat_texts = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in feats}
    readme = fdir / "README.md"
    readme_text = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
    infra_text = ""
    for p in [skill / "SKILL.md", *sorted((skill / "scripts").rglob("*") if (skill / "scripts").is_dir() else [])]:
        if p.is_file():
            infra_text += p.read_text(encoding="utf-8", errors="replace") + "\n"

    res = {"skill_dir": str(skill), "repo": str(repo), "index": cvs.index_report(skill), "contract": [],
           "routes": {"source": len(inv["http_routes"]), "missing": [], "dead": []},
           "commands": {"source": len(inv["cli_commands"]), "missing": [], "dead": []},
           "unanchored_features": [], "notes": []}
    if not readme.is_file():
        res["index"]["missing_readme"] = True
    for name, text in feat_texts.items():
        errs = cvs.h2_contract_errors(text)
        if errs:
            res["contract"].append({"file": f"features/{name}", "errors": errs})

    # ---- routes
    src_routes = [(r, scan_repo.route_regex(r["path"])) for r in inv["http_routes"]]

    def matches(method: str, path: str) -> list[dict]:
        return [r for r, rx in src_routes if (r["method"] in (method, "ANY") or method == "ANY") and rx.match(path)]

    feat_mentions = {name: route_mentions(t) for name, t in feat_texts.items()}
    infra_mentions = route_mentions(infra_text)
    covered = set()
    for name, ms in feat_mentions.items():
        for meth, path, _ in ms:
            for r in matches(meth, path):
                covered.add(id(r))
    for meth, path, _ in infra_mentions:
        for r in matches(meth, path):
            if r.get("health"):
                covered.add(id(r))
    for r, _ in src_routes:
        if id(r) not in covered and not (r.get("health") and r["path"] in infra_text):
            res["routes"]["missing"].append({"method": r["method"], "path": r["path"], "source": r["source"]})
    for where, text in [*((f"features/{k}", v) for k, v in feat_texts.items()), ("features/README.md", readme_text)]:
        for meth, path, line in route_mentions(text):
            if meth in ("HEAD", "OPTIONS"):
                continue
            if not matches(meth, path):
                res["routes"]["dead"].append({"method": meth, "path": path, "where": f"{where}:{line}"})

    # ---- commands
    progs: dict[str, dict] = {}
    for c in inv["cli_commands"]:
        if c["prog"].startswith("TODO"):
            continue
        p = progs.setdefault(c["prog"], {"subs": set(), "seqs": [], "value_flags": set()})
        p["subs"].add(c["name"])
        p["subs"].update(c.get("aliases_sub", []))
        for o in c.get("options", []) + c.get("global_options", []):
            if not o["positional"] and not isinstance(o.get("default"), bool):
                p["value_flags"].update(n for n in o["names"] if n.startswith("-"))
        for inv_ in [c["prog"], *c.get("aliases", [])]:
            seq = prog_tokens(inv_)
            if seq and seq not in p["seqs"]:
                p["seqs"].append(seq)
    # A removed CLI still appears in the map. Recover its invocation vocabulary from the
    # recipes' CLI drives, including assertion flags before -- (not incidental unit tests),
    # so deleting the final subcommand cannot look clean.
    for text in feat_texts.values():
        for tokens in cvs.code_commands(text):
            drive = cvs.parse_drive(tokens)
            if drive is None or drive['kind'] != 'cli':
                continue
            argv = drive['argv']
            if len(argv) >= 3 and re.fullmatch(r'python3?', argv[0]) and argv[1] == '-m':
                size = 3
            elif len(argv) >= 2 and ((re.fullmatch(r'python3?', argv[0]) and argv[1].endswith('.py')) or
                                    (argv[0] == 'node' and re.search(r'\.[cm]?js$', argv[1]))):
                size = 2
            else:
                continue
            if len(argv) <= size:
                continue
            prog = shlex.join(argv[:size])
            if prog not in progs:
                progs[prog] = {"subs": set(), "seqs": [prog_tokens(prog)],
                               "value_flags": {"--data", "--port", "--data-dir"}}
    launch_subs = {(c["prog"], c["name"]) for c in inv["cli_commands"] if c.get("launch")}
    feat_cmds = {name: command_mentions(t, progs) for name, t in feat_texts.items()}
    infra_cmds = command_mentions(infra_text, progs) + [
        (prog, sub, 0) for prog, info in progs.items() for sub in info["subs"]
        if (prog, sub) in launch_subs and re.search(rf"\b{re.escape(sub)}\b", infra_text)]
    mentioned = {(p, s) for ms in feat_cmds.values() for p, s, _ in ms}
    mentioned_infra = {(p, s) for p, s, _ in infra_cmds}
    for c in inv["cli_commands"]:
        key = (c["prog"], c["name"])
        if c["prog"].startswith("TODO") or c["name"] in scan_repo.SKIP_COMMANDS:
            continue
        if key in mentioned or (key in launch_subs and key in mentioned_infra):
            continue
        res["commands"]["missing"].append({"prog": c["prog"], "name": c["name"], "source": c["source"],
                                           "launch": bool(c.get("launch"))})
    for where, text in [*((f"features/{k}", v) for k, v in feat_texts.items()), ("features/README.md", readme_text)]:
        for prog, sub, line in command_mentions(text, progs):
            if sub not in progs[prog]["subs"] and sub not in ("help",):
                res["commands"]["dead"].append({"prog": prog, "name": sub, "where": f"{where}:{line}"})

    for name in feat_texts:
        if not feat_mentions[name] and not feat_cmds[name]:
            res["unanchored_features"].append(f"features/{name}")
    if not inv["http_routes"] and not inv["cli_commands"]:
        res["notes"].append("no routes or CLI commands detected in source, so every route the map names and every "
                            "command its recipes drive (`cli -- PROG`) is reported dead; confirm each in source before "
                            "deleting a recipe (the scanner may not know this framework). The live pass carries the "
                            "whole check")
    idx = res["index"]
    res["drift"] = bool(any(idx.get(k) for k in ("missing", "dead", "duplicate", "extra", "missing_readme"))
                        or res["contract"] or res["routes"]["missing"] or res["routes"]["dead"]
                        or res["commands"]["missing"] or res["commands"]["dead"])
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="drift_check.py",
        description="Compare a verify-<app> skill's feature map with the app's source: index hygiene, the four-H2 "
                    "contract, routes and CLI commands missing from the map, and map entries the source no longer "
                    "has. Exit 1 on drift.",
        epilog="examples:\n"
               "  drift_check.py .claude/skills/verify-todo --repo .\n"
               "  drift_check.py .cursor/skills/verify-shop --repo . --json\n\n"
               "A clean result does not replace the live pass: drive every feature anyway.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("skill_dir", type=Path, help="the verify-<app> skill directory")
    ap.add_argument("--repo", required=True, type=Path, help="the app's repository root")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if not args.skill_dir.is_dir():
        print(f"drift_check: {args.skill_dir} is not a directory", file=sys.stderr)
        return 2
    if not args.repo.is_dir():
        print(f"drift_check: --repo {args.repo} is not a directory", file=sys.stderr)
        return 2
    res = drift(args.skill_dir.resolve(), args.repo.resolve())
    if args.json:
        print(json.dumps(res, indent=2))
        return 1 if res["drift"] else 0
    idx = res["index"]
    for k in ("missing", "dead", "duplicate", "extra"):
        for v in idx.get(k, []):
            print(f"index {k}: {v}")
    if idx.get("missing_readme"):
        print("index missing: features/README.md does not exist")
    for c in res["contract"]:
        print(f"contract: {c['file']}: {'; '.join(c['errors'])}")
    for r in res["routes"]["missing"]:
        print(f"route missing from map: {r['method']} {r['path']}  (source {r['source']})")
    for r in res["routes"]["dead"]:
        print(f"route dead in map: {r['method']} {r['path']}  ({r['where']})")
    for c in res["commands"]["missing"]:
        print(f"command missing from map: {c['prog']} {c['name']}  (source {c['source']})")
    for c in res["commands"]["dead"]:
        print(f"command dead in map: {c['prog']} {c['name']}  ({c['where']})")
    for f in res["unanchored_features"]:
        print(f"note: {f} mentions no route or command; check it in the live pass")
    for n in res["notes"]:
        print(f"note: {n}")
    print(f"drift_check: {'DRIFT' if res['drift'] else 'clean'} ({res['routes']['source']} routes, "
          f"{res['commands']['source']} commands in source) {args.skill_dir}")
    return 1 if res["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
