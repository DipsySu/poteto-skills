#!/usr/bin/env python3
"""Turn feedback.json into REFACTOR_PLAN.md and patlint rules.

  plan.py feedback.json --out-dir DIR [--min-count 2] [--threshold 0.3] [--no-prove]

Clusters feedback items into themes (the same clustering /correct uses), keeps
themes seen at least --min-count times, and for each theme writes: evidence,
the pyramid level chosen (architecture > types > lint > test > docs) and why
not higher, a draft target design, steps, and proof commands.

For themes where the evidence names a bad pattern and the fix (from review
wording like "don't X, use Y" and from fix-commit diffs: removed lines = bad,
added lines = good), it writes a patlint rule to DIR/lint-rules.json. Unless
--no-prove, it replays every rule against the repo's own history: a rule is
`proven` when it reports more hits at the mistake revision than at the fixed
revision (fix commit: sha^ vs sha; reverted commit: sha vs sha^).

Outputs: DIR/REFACTOR_PLAN.md, DIR/lint-rules.json, DIR/plan.json.
Needs the sibling `correct` skill (mine_mistakes.py, patlint.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path


def correct_scripts() -> Path:
    here = Path(__file__)
    for base in (here.resolve().parent, here.parent):
        cand = base.parent.parent / "correct" / "scripts"
        if (cand / "mine_mistakes.py").is_file() and (cand / "patlint.py").is_file():
            return cand
    sys.exit("plan: needs the `correct` skill installed next to this one "
             "(skills/correct/scripts/{mine_mistakes,patlint}.py). Run install.sh from poteto-skills.")


sys.dont_write_bytecode = True  # importing the correct skill must not leave __pycache__ behind
CORRECT = correct_scripts()
sys.path.insert(0, str(CORRECT))
import mine_mistakes as mm  # noqa: E402
import patlint  # noqa: E402

TOKEN = r"`?([A-Za-z_][\w./-]*(?:\(\))?)`?"
CODE_TOKEN = re.compile(r"^(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?:\(\))?|[A-Za-z_]\w*\(\)|[\w-]+\.(?:json|ya?ml|toml|csv|db|sqlite|txt|ini|cfg))$")
DATAFILE = re.compile(r"\.(json|ya?ml|toml|csv|db|sqlite|txt|ini|cfg)$", re.I)
SOURCE_EXT = re.compile(r"\.(py|js|jsx|ts|tsx|mjs|cjs|go|rs|rb|java|kt|swift|cs|php|c|cc|cpp|h)$")
GOOD_PATS = [
    re.compile(r"\b(?:use|call|via|through|prefer|switch to|route (?:it )?through|go(?:es)? through|must use|"
               r"should use|always use)\s+(?:the\s+|a\s+)?" + TOKEN, re.I),
    re.compile(TOKEN + r"\s+instead\b(?!\s+of)", re.I),
]
BAD_PATS = [
    re.compile(r"\b(?:don'?t|do not|never|avoid|stop|no more|shouldn'?t|should not)\s+(?:use\s+|call\s+|using\s+|"
               r"calling\s+)?(?:the\s+)?" + TOKEN, re.I),
    re.compile(TOKEN + r"\s+(?:file\s+)?(?:directly|by hand)\b", re.I),
]
INSTEAD_OF = re.compile(TOKEN + r"\s+instead of\s+(?:using\s+|calling\s+)?" + TOKEN, re.I)
USE_BEFORE = re.compile(r"\b(?:use|call|prefer|switch to)\s+(?:the\s+)?$", re.I)

ARCH_SIGNALS = re.compile(
    r"\b(directly|bypass\w*|instead of|go(?:es)? through|route[sd]? through|only (?:place|module|way|writer)|"
    r"single source|source of truth|owner\w*|duplicat\w*|copy[- ]?past\w*|two ways|old way|legacy|deprecated|"
    r"internal|private|hand[- ]?sync\w*|by hand)\b", re.I)
TYPE_SIGNALS = re.compile(
    r"\b(status|state|transition\w*|enum|none|null|nil|optional|invalid|illegal|typo|string literal|magic string|"
    r"stringly|types?|typed|schema|validat\w*|unrepresentable)\b", re.I)
TEST_SIGNALS = re.compile(
    r"\b(regression|crash\w*|broke|broken|returns?|edge case|off[- ]by[- ]one|500|exception|traceback|flaky|race|"
    r"wrong (?:result|output|value))\b", re.I)
TEST_GLOBS = ["**/tests/**", "**/test/**", "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*.test.*", "**/*.spec.*"]


def norm(tok: str) -> str:
    return tok.strip().rstrip(".,;:").removesuffix("()").lower()


def clean(tok: str) -> str:
    return tok.strip().rstrip(".,;:")


def vote_tokens(items: list[dict]) -> tuple[list[str], list[str], dict]:
    """Return (bad, good, detail) code tokens for a theme, by majority vote."""
    good, bad = Counter(), Counter()
    surface: dict[str, Counter] = {}

    def add(counter: Counter, tok: str, w: int) -> None:
        tok = clean(tok)
        if not CODE_TOKEN.match(tok):
            return
        key = norm(tok)
        counter[key] += w
        surface.setdefault(key, Counter())[tok] += 1

    texts = [it.get("text") or "" for it in items]
    for text in texts:
        for pat in GOOD_PATS:
            for m in pat.finditer(text):
                add(good, m.group(1), 1)
        for pat in BAD_PATS:
            for m in pat.finditer(text):
                add(bad, m.group(1), 1)
        for m in INSTEAD_OF.finditer(text):
            x, y = m.group(1), m.group(2)
            if USE_BEFORE.search(text[: m.start()]):
                add(good, x, 1)
                add(bad, y, 1)
            else:
                add(bad, x, 1)
                add(good, y, 1)
    mentioned = " ".join(texts).lower()
    diff_seen = Counter(norm(t) for it in items for t in set(it.get("diff_ids") or []))
    for it in items:
        if it.get("source") != "commit":
            continue
        added = {clean(t) for t in it.get("diff_added") or []}
        removed = {clean(t) for t in it.get("diff_removed") or []}
        only_added, only_removed = added - removed, removed - added
        kind = it.get("kind")
        bad_side, good_side = (only_added, only_removed) if kind == "reverted" else (only_removed, only_added)
        for t in bad_side:
            if norm(t) in mentioned or diff_seen[norm(t)] >= 2:
                add(bad, t, 2)
        for t in good_side:
            if norm(t) in mentioned or diff_seen[norm(t)] >= 2:
                add(good, t, 2)
    keys = set(good) | set(bad)
    margin = {k: bad[k] - good[k] for k in keys}
    bad_toks = sorted((k for k in keys if margin[k] > 0), key=lambda k: (-margin[k], k))
    good_toks = sorted((k for k in keys if margin[k] < 0), key=lambda k: (margin[k], k))

    def best(k: str) -> str:
        return surface[k].most_common(1)[0][0]

    detail = {best(k): {"bad": bad[k], "good": good[k]} for k in sorted(keys)}
    return [best(k) for k in bad_toks], [best(k) for k in good_toks], detail


def choose_level(items: list[dict], bad: list[str], good: list[str]) -> tuple[str, str, str]:
    """(level, why, why_not_higher)."""
    text = " ".join(it.get("text") or "" for it in items)
    arch = len(ARCH_SIGNALS.findall(text))
    typ = len(TYPE_SIGNALS.findall(text))
    test = len(TEST_SIGNALS.findall(text))
    if good and arch >= 2:
        return ("architecture",
                f"the evidence describes bypassing an owner ({arch} owner/bypass signals) and names the supported way `{good[0]}`",
                "already the highest level; keep the lint as a ratchet while call sites migrate")
    if typ >= 2:
        return ("types",
                f"the evidence is about values or states that should not be writable ({typ} state/type signals)",
                "no single owner or bypass is named, so moving code alone would not stop the bad state being constructed")
    if bad and good:
        return ("lint",
                f"a concrete bad pattern (`{bad[0]}`) and its fix (`{good[0]}`) are named, but no ownership or state change is implied",
                "no owner/bypass or state signal in the evidence; a lint whose message names the fix is the cheapest check that fails")
    if test or bad:
        return ("test",
                "the evidence describes behavior that broke, with no code pattern a regex can catch",
                "no owner, type, or lintable pattern is named; a behavior test through the public entry point is the highest check that fits")
    return ("docs",
            "no code token, owner, or state signal: this reads as a judgment call",
            "nothing in the evidence can be checked mechanically yet; write one AGENTS.md line with a failure example and re-run /correct when it repeats")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().removesuffix("()")).strip("-")[:40] or "rule"


def owner_module(good: str) -> str | None:
    core = good.removesuffix("()")
    if DATAFILE.search(core):
        return None
    if "." in core:
        return core.split(".")[0]
    return None


def derive_rule(n: int, title: str, items: list[dict], bad: list[str], good: list[str]):
    bad = [b for b in bad if b.removesuffix("()") not in {g.removesuffix("()") for g in good}][:3]
    if not bad:
        return None, "no bad code pattern named in the evidence"
    if not good:
        return None, "the evidence never names the fix; choose the owner API (/architect) before adding a lint"
    paths = [p for it in items for p in (it.get("paths") or [])]
    exts = Counter(m.group(1) for p in paths for m in [SOURCE_EXT.search(p)] if m)
    include = [f"**/*.{e}" for e, _ in exts.most_common(2)] or ["**/*"]
    exclude = list(TEST_GLOBS)
    owner = owner_module(good[0])
    owner_globs = [f"**/{owner}.{e}" for e, _ in exts.most_common(2)] if owner else []
    exclude = owner_globs + exclude
    parts = ["\\b" + re.escape(b.removesuffix("()")) + "\\b" for b in bad]
    regex = parts[0] if len(parts) == 1 else "(?:" + "|".join(parts) + ")"
    rid = f"no-{slug(bad[0])}" + (f"-outside-{slug(owner)}" if owner else "")
    bad_txt = ", ".join(f"`{b}`" for b in bad)
    owner_txt = f" ({owner_globs[0].removeprefix('**/')} owns this)" if owner_globs else ""
    message = f"Use `{good[0]}` instead of {bad_txt}{owner_txt}. Theme T{n}: {title}."
    rule = {"id": rid, "regex": regex, "include": include, "exclude": exclude, "message": message}
    return rule, None


def mistake_revs(it: dict) -> tuple[str, str] | None:
    sha, kind = (it.get("ref") or "")[:12], it.get("kind")
    if it.get("source") != "commit" or not sha:
        return None
    if kind == "reverted":
        return sha, sha + "^"
    return sha + "^", sha  # fix and revert commits: the mistake is in the parent


def count_hits(patlint_py: Path, rules_file: Path, repo: Path, rule_id: str, rev: str | None, paths: list[str]):
    cmd = [str(patlint_py), "--rules", str(rules_file), "--root", str(repo)]
    if rev:
        cmd += ["--rev", rev]
    cmd += paths
    res = subprocess.run([sys.executable, *cmd, "--json"], capture_output=True, text=True, cwd=str(repo))
    shown = shlex.join(["python3", *cmd])
    if res.returncode not in (0, 1):
        return None, shown, res.stderr.strip().splitlines()[-1:] or ["error"]
    data = json.loads(res.stdout)
    hits = [v for v in data["violations"] + data["expired"] + data["invalid_exceptions"] if v["rule"] == rule_id]
    return len(hits), shown, [f"{v['path']}:{v['line']}" for v in hits[:5]]


def prove(rule: dict, items: list[dict], rules_file: Path, repo: Path) -> dict:
    patlint_py = CORRECT / "patlint.py"
    inc = [patlint.glob_to_regex(g) for g in rule["include"]]
    proof = {"replays": [], "proven": False, "head_hits": None, "head_cmd": None}
    for it in items:
        revs = mistake_revs(it)
        if not revs:
            continue
        paths = [p for p in it.get("paths") or [] if any(g.match(p) for g in inc)]
        if not paths:
            continue
        bad_rev, good_rev = revs
        before, cmd, where = count_hits(patlint_py, rules_file, repo, rule["id"], bad_rev, paths)
        after, _, _ = count_hits(patlint_py, rules_file, repo, rule["id"], good_rev, paths)
        ok = before is not None and before > (after or 0)
        proof["replays"].append({"commit": it["ref"][:10], "kind": it.get("kind"), "mistake_rev": bad_rev,
                                 "fixed_rev": good_rev, "hits_at_mistake": before, "hits_at_fixed": after,
                                 "where": where, "cmd": cmd, "distinguishes": ok})
        proof["proven"] = proof["proven"] or ok
    n, cmd, where = count_hits(patlint_py, rules_file, repo, rule["id"], None, [str(repo)])
    proof["head_hits"], proof["head_cmd"], proof["head_where"] = n, cmd, where
    return proof


TARGETS = {
    "architecture": "Make `{owner}` the single owner: callers use `{good}`, nothing else touches what {bad} touched. "
                    "Hide or delete the second path (move the internals behind `{owner}`, delete copies an agent could imitate), "
                    "so a change that looks right from one file is right for the repo. Keep `{rule}` as a ratchet until no call site is left.",
    "types": "Model the values in this theme ({sig}) as a type the bad state cannot be written in: an enum or newtype plus "
             "one transition function owned by one module, validated once at the boundary. Callers stop passing raw strings.",
    "lint": "Add patlint rule `{rule}` whose message names `{good}`. Adopt a ratchet baseline if the pattern is already common.",
    "test": "Write one behavior test through the public entry point that reproduces the past failure ({refs}). "
            "It must fail on the mistake revision and pass on the fix.",
    "docs": "Judgment call: add one line to AGENTS.md with a concrete failure example from {refs}. Re-run /correct when it repeats.",
}
STEPS = {
    "architecture": ["Run /architect on the owner boundary: sketch the owner API ({good}) and the module layout, two candidates.",
                     "Route every call site through the owner; list them with the lint: `{lint_cmd}`.",
                     "Delete the old path and any helper an agent could copy.",
                     "Keep the lint with a baseline while migrating; drop the AGENTS.md rule once the lint makes the mistake unmergeable."],
    "types": ["Sketch the type (enum/newtype + transition table) with /architect.",
              "Convert the boundary (CLI/HTTP/storage parsing) to construct the type once; internal code takes the type.",
              "Make illegal transitions raise in one function; delete scattered string comparisons.",
              "Add a test that the illegal transition from the evidence is rejected."],
    "lint": ["Review the generated rule (regex, include/exclude) against the evidence.",
             "Run it on HEAD: `{lint_cmd}`; adopt a baseline if hits exist: add `--baseline .patlint-baseline.json --update-baseline`.",
             "Wire the same command into CI and the AGENTS.md rule table (`rule_table.py add`)."],
    "test": ["Write the failing test first against the mistake revision.",
             "Fix, then keep the test in the default test command."],
    "docs": ["Add the rule with an example to AGENTS.md (`rule_table.py add AGENTS.md --rule ... --enforced-by docs`).",
             "Expect `rule_table.py check` to flag it until something enforces it."],
}


def evidence_line(it: dict) -> str:
    return mm.describe_item(it)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="plan.py",
        description="Cluster feedback.json into themes and write REFACTOR_PLAN.md (evidence, pyramid level, target "
                    "design, steps, proof) plus lint-rules.json (patlint format) for lintable themes.",
        epilog="examples:\n"
               "  plan.py feedback.json --out-dir refactor/\n"
               "  plan.py feedback.json --out-dir refactor/ --no-prove --min-count 3\n"
               "then prove a rule by hand on a past mistake:\n"
               "  python3 <skills>/correct/scripts/patlint.py --rules refactor/lint-rules.json --rev SHA^ path/to/file.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("feedback", type=Path, help="feedback.json from collect_feedback.py")
    ap.add_argument("--out-dir", required=True, type=Path, help="where to write REFACTOR_PLAN.md, lint-rules.json, plan.json")
    ap.add_argument("--min-count", type=int, default=2, help="minimum occurrences for a theme (default 2)")
    ap.add_argument("--threshold", type=float, default=0.3, help="cosine similarity to join a theme (default 0.3)")
    ap.add_argument("--repo", type=Path, help="repo for proofs (default: the repo recorded in feedback.json)")
    ap.add_argument("--no-prove", action="store_true", help="skip replaying rules against git history")
    args = ap.parse_args(argv)

    if not args.feedback.is_file():
        print(f"plan: {args.feedback} not found; make it with collect_feedback.py", file=sys.stderr)
        return 2
    doc = json.loads(args.feedback.read_text(encoding="utf-8"))
    items = mm.review_items_from_json(json.dumps(doc), args.feedback.name)
    repo = (args.repo or Path(doc.get("repo") or ".")).resolve()
    can_prove = not args.no_prove and mm.is_git_repo(repo)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    rules_file = out / "lint-rules.json"

    classes, unclassified = mm.cluster_items(items, args.threshold, args.min_count)
    themes, rules = [], []
    for n, c in enumerate(classes, 1):
        members = c["evidence"]
        title = " / ".join(c["signature"][:3]) or f"theme {n}"
        bad, good, votes = vote_tokens(members)
        level, why, why_not = choose_level(members, bad, good)
        rule, no_rule = derive_rule(n, title, members, bad, good)
        if rule:
            used = {r["id"] for r in rules}
            if rule["id"] in used:
                rule["id"] = f"{rule['id']}-t{n}"
            rules.append(rule)
        refs = ", ".join(m["ref"][:10] if m["source"] == "commit" else m["ref"] for m in members[:3])
        themes.append({"id": f"T{n}", "title": title, "count": c["count"], "sources": c["sources"],
                       "signature": c["signature"], "level": level, "why": why, "why_not_higher": why_not,
                       "bad": bad, "good": good, "votes": votes, "rule": rule, "no_rule_reason": no_rule,
                       "refs": refs, "evidence": members, "proof": None})

    rules_doc = {"rules": rules, "generated_by": "feedback-to-architecture/scripts/plan.py",
                 "source": str(args.feedback)}
    rules_file.write_text(json.dumps(rules_doc, indent=2) + "\n", encoding="utf-8")
    if rules:
        try:
            patlint.load_rules(rules_file)
        except patlint.RulesError as e:
            print(f"plan: generated rules failed patlint validation:\n{e}", file=sys.stderr)
            return 1
        if can_prove:
            for t in themes:
                if t["rule"]:
                    t["proof"] = prove(t["rule"], t["evidence"], rules_file, repo)

    patlint_cmd = f"python3 {CORRECT / 'patlint.py'} --rules {rules_file} --root {repo} {repo}"
    lines = [
        "# Refactor plan: make repeated feedback impossible",
        "",
        f"Generated {dt.date.today().isoformat()} by `plan.py` from `{args.feedback}` "
        f"({len(items)} items: " + ", ".join(f"{v} {k}" for k, v in sorted(Counter(i['source'] for i in items).items())) + ")."
        f" Repo: `{repo}`.",
        "",
        "Each theme is a cluster of feedback seen at least "
        f"{args.min_count} times. Its level is the highest that can work: architecture > types > lint whose message "
        "names the fix > test > docs. Target designs are drafts; refine them with /architect before implementing.",
        "",
        "## Summary",
        "",
        "| Theme | Items | Level | Lint rule | Proof on history |",
        "|---|---|---|---|---|",
    ]
    for t in themes:
        proof = "-"
        if t["proof"]:
            reps = t["proof"]["replays"]
            proof = (("proven on " + ", ".join(r["mistake_rev"] for r in reps if r["distinguishes"]))
                     if t["proof"]["proven"] else ("not proven" if reps else "no commit to replay"))
        rule_cell = f"`{t['rule']['id']}`" if t["rule"] else "none"
        lines.append(f"| {t['id']} {t['title']} | {t['count']} | {t['level']} | {rule_cell} | {proof} |")
    if not themes:
        lines.append(f"| - | - | - | - | no theme reached {args.min_count} occurrences |")
    for t in themes:
        r = t["rule"]
        fill = {"owner": (owner_module(t["good"][0]) if t["good"] else None) or (t["good"][0] if t["good"] else "the owner module"),
                "good": t["good"][0] if t["good"] else "the supported API",
                "bad": ", ".join(f"`{b}`" for b in t["bad"][:3]) or "the bypass",
                "rule": r["id"] if r else "a lint", "sig": ", ".join(t["signature"][:5]), "refs": t["refs"],
                "lint_cmd": patlint_cmd}
        lines += ["", f"## {t['id']}: {t['title']}", "",
                  f"Occurrences: {t['count']} ({', '.join(f'{k}×{v}' for k, v in sorted(t['sources'].items()))}). "
                  f"Signature: {', '.join(t['signature'][:6])}.", "", "### Evidence", ""]
        lines += [f"- {evidence_line(m)}" for m in t["evidence"]]
        lines += ["", "### Level", "",
                  f"**{t['level']}**: {t['why']}.", f"Why not higher: {t['why_not_higher']}."]
        if t["bad"] or t["good"]:
            lines.append(f"Named in evidence: bad {', '.join(f'`{b}`' for b in t['bad'][:3]) or '-'}; "
                         f"fix {', '.join(f'`{g}`' for g in t['good'][:3]) or '-'}.")
        lines += ["", "### Target design (draft)", "", TARGETS[t["level"]].format(**fill), "", "### Steps", ""]
        lines += [f"{i}. {s.format(**fill)}" for i, s in enumerate(STEPS[t["level"]], 1)]
        lines += ["", "### Proof", ""]
        if r:
            lines.append(f"Rule `{r['id']}` in `{rules_file}`: regex `{r['regex']}`, include {r['include']}, "
                         f"message: {r['message']}")
            p = t["proof"]
            if p:
                for rp in p["replays"]:
                    mark = "fires on the mistake and not on the fix" if rp["distinguishes"] else "does NOT distinguish mistake from fix"
                    lines.append(f"- {rp['kind']} {rp['commit']}: {rp['hits_at_mistake']} hit(s) at `{rp['mistake_rev']}`, "
                                 f"{rp['hits_at_fixed']} at `{rp['fixed_rev']}`: {mark}. Rerun: `{rp['cmd']}`")
                if not p["replays"]:
                    lines.append("- No commit in this theme touches a file the rule includes; prove it on a scratch "
                                 "copy of the mistake from the review comment.")
                lines.append(f"- HEAD: {p['head_hits']} hit(s) now"
                             + (f" ({', '.join(p['head_where'])})" if p.get("head_where") else "")
                             + f". Rerun: `{p['head_cmd']}`. After the refactor this must be 0, "
                               "or held by a ratchet baseline.")
            else:
                lines.append(f"- Not replayed (--no-prove or no git repo). Replay with patlint `--rev SHA^` on a fix commit's files.")
        else:
            lines.append(f"No lint rule: {t['no_rule_reason']}.")
            if t["level"] in ("types", "test"):
                lines.append("Prove the change with a test that fails on the mistake revision and passes after it.")
        lines.append("Record the enforcement: `rule_table.py add AGENTS.md --rule \"...\" --enforced-by \"...\"` "
                     "(from the correct skill).")
    if unclassified:
        lines += ["", "## Seen once (not a theme yet)", ""]
        lines += [f"- {evidence_line(m)}" for m in unclassified[:30]]
        if len(unclassified) > 30:
            lines.append(f"- … {len(unclassified) - 30} more in plan.json")
    (out / "REFACTOR_PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    plan_doc = {"version": 1, "feedback": str(args.feedback), "repo": str(repo), "themes": themes,
                "unclassified": unclassified, "rules_file": str(rules_file), "proved": can_prove}
    (out / "plan.json").write_text(json.dumps(plan_doc, indent=2, default=str) + "\n", encoding="utf-8")

    summary = {"out_dir": str(out), "themes": len(themes), "rules": [r["id"] for r in rules],
               "proven": [t["rule"]["id"] for t in themes if t["rule"] and t["proof"] and t["proof"]["proven"]],
               "levels": {t["id"]: t["level"] for t in themes},
               "files": [str(out / "REFACTOR_PLAN.md"), str(rules_file), str(out / "plan.json")]}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
