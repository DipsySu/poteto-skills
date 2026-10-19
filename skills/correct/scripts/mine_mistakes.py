#!/usr/bin/env python3
"""Mine a repo for mistake classes that happened at least twice.

Evidence sources:
  * git log: reverts (and the commits they revert) and fix commits, with their
    messages and touched files
  * --feedback FILE...: review comments in `gh api` JSON shape
    (list of {id, body, path, line, user.login, html_url, created_at}), the
    normalized feedback.json written by collect_feedback.py, or markdown notes
    (each bullet or paragraph is one item)
  * code comments that explain workarounds (HACK, WORKAROUND, FIXME, XXX)
  * AGENTS.md / CLAUDE.md rules: matched against each class; a matching rule that
    nothing enforces marks the class as a repeat

Items are grouped into classes by keyword/path signature (TF-IDF over words,
dotted identifiers, code spans, and file names; deterministic leader clustering
with refinement). Classes with fewer than --min-count occurrences are dropped
(a revert and the commit it reverts count as one occurrence).

This module is also imported by feedback-to-architecture (collect_feedback.py,
plan.py): keep the item schema stable.

Item schema: {id, source, kind, ref, text, paths, line, author, date, url}
  source: commit | review | chat | code-comment
  kind (commits only): fix | revert | reverted
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # importing siblings must not leave __pycache__ in the skill dir
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import rule_table  # noqa: E402  (sibling script)

FIX_RE = re.compile(
    r"\b(fix(?:es|ed|ing|up)?|bug ?fix|hotfix|regression|broke(?:n)?|oops|typo|forgot|"
    r"wrong|incorrect|repair|correct(?:ed|s)?|again|mistake)\b", re.I)
CONVENTIONAL_FIX = re.compile(r"^(fix|bugfix|hotfix)(\([^)]*\))?!?:", re.I)
REVERT_SUBJECT = re.compile(r'^Revert\s+"', re.I)
REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
TRAILER = re.compile(r"^\s*(Co-Authored-By|Signed-off-by|Reviewed-by|Change-Id|Acked-by|Tested-by)\s*:", re.I)
WORKAROUND_RE = r"\b(HACK|WORKAROUND|FIXME|XXX)\b"

STOP = set("""
a about above after against all also am an and any are as at be because been before being below between both but by
can cannot could did do does doing down during each else ever every few for from further get gets got had has have having
he her here hers him his how however i if in into is it its itself just let lets like make makes made me more most much must my
never no nor not now of off on once only or other our ours out over own per please same she should so some such than that the
their theirs them then there these they this those through to too under until up us very via was we were what when where which
while who whom why will with within without would yet you your yours
don doesn isn won didn shouldn wouldn couldn aren wasn weren hasn haven dont doesnt isnt cant wont didnt shouldnt
fix fixes fixed fixing fixup bug bugs bugfix hotfix issue issues pr prs commit commits revert reverts reverted reverting
merge merged update updated updates updating change changes changed changing use used uses using instead again still
always really actually thing things stuff etc ok okay thanks thank lgtm nit nits wip tmp
this that one two new add adds added adding remove removed line lines code file files
""".split())

CODE_SPAN = re.compile(r"`([^`\n]{1,120})`")
FILE_LIKE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|go|rs|rb|java|kt|swift|c|h|cc|cpp|hpp|cs|php|"
    r"json|ya?ml|toml|ini|cfg|md|sh|sql|html|css|scss|lock|txt|csv))\b")
DOTTED = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(\(\))?")
CALL = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]{2,})\(\)")
WORD = re.compile(r"[a-z][a-z0-9_]{2,}")
STRONG_PREFIXES = ("file:", "id:", "code:")

SOURCE_ORDER = {"commit": 0, "review": 1, "chat": 2, "code-comment": 3}


# ---------------------------------------------------------------- tokenizing

def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    elif len(w) > 5 and w.endswith("ly"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def code_keys(blob: str, note) -> None:
    """file: and id: keys from code (diff lines, identifiers). No plain words."""
    consumed = []
    for m in FILE_LIKE.finditer(blob):
        name = m.group(1)
        note("file:" + name.rsplit("/", 1)[-1].lower(), name.rsplit("/", 1)[-1])
        consumed.append(m.span())
    for m in DOTTED.finditer(blob):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        ident = m.group(1)
        note("id:" + ident.lower(), ident + (m.group(2) or ""))


def tokens(text: str, paths: list[str] | None = None, surfaces: dict | None = None) -> set[str]:
    """Signature keys for one item: stemmed words, file:, id:, code: keys."""
    keys: set[str] = set()

    def note(key: str, surface: str) -> None:
        keys.add(key)
        if surfaces is not None:
            surfaces.setdefault(key, Counter())[surface] += 1

    text = text or ""
    code_keys(text, note)
    for m in CALL.finditer(text):
        note("id:" + m.group(1).lower(), m.group(1) + "()")
    for span in CODE_SPAN.findall(text):
        s = span.strip().rstrip("()").strip()
        if s and " " not in s and len(s) <= 40:
            note("code:" + s.lower(), span.strip())
    for raw in WORD.findall(text.lower()):
        if raw in STOP or raw.isdigit():
            continue
        st = stem(raw)
        if len(st) < 3 or st in STOP:
            continue
        note(st, raw)
    for p in (paths or [])[:8]:
        base = p.rsplit("/", 1)[-1]
        if base:
            note("file:" + base.lower(), base)
    return keys


def display_key(key: str, surfaces: dict) -> str:
    c = surfaces.get(key)
    if c:
        return c.most_common(1)[0][0]
    return key.split(":", 1)[-1]


# ---------------------------------------------------------------- collection

def run_git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def is_git_repo(repo: Path) -> bool:
    try:
        run_git(repo, "rev-parse", "--git-dir")
        return True
    except (RuntimeError, OSError):
        return False


def clean_message(body: str) -> str:
    keep = [ln for ln in body.splitlines() if not TRAILER.match(ln) and not REVERTS_SHA.search(ln)]
    return "\n".join(keep).strip()


def diff_identifiers(repo: Path, sha: str, limit: int = 25) -> tuple[list[str], list[str]]:
    """Dotted identifiers and file names on the added and removed lines of a commit."""
    try:
        out = run_git(repo, "show", "--format=", "--unified=0", "--no-color", "--no-ext-diff", sha)
    except RuntimeError:
        return [], []
    added: list[str] = []
    removed: list[str] = []
    changed = [x for x in out.splitlines() if x[:1] in "+-" and not x.startswith(("+++", "---"))]
    for ln in changed[:400]:
        bucket = added if ln[0] == "+" else removed
        if len(bucket) >= limit:
            continue

        def note(key: str, surface: str, bucket=bucket) -> None:
            if surface not in bucket:
                bucket.append(surface)

        code_keys(ln[1:], note)
    return added[:limit], removed[:limit]


def git_items(repo: Path, since: str | None = None, max_commits: int = 2000) -> list[dict]:
    """Fix commits, reverts, and reverted commits as items."""
    fmt = "%x1e%H%x1f%an%x1f%ad%x1f%s%x1f%b%x1d"
    args = ["log", "--no-merges", "--name-only", f"--format={fmt}", "--date=short", f"-n{max_commits}"]
    if since:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since.strip()):
            since = since.strip() + " 00:00:00"  # bare dates mean midnight, not "now" on that day
        args.append(f"--since={since}")
    out = run_git(repo, *args)
    commits = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        head, _, files = rec.partition("\x1d")
        parts = head.split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, date, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        paths = [f.strip() for f in files.splitlines() if f.strip()]
        commits.append({"sha": sha, "author": author, "date": date, "subject": subject, "body": body, "paths": paths})

    reverted: dict[str, str] = {}
    for c in commits:
        m = REVERTS_SHA.search(c["body"])
        if REVERT_SUBJECT.match(c["subject"]) or m:
            c["kind"] = "revert"
            if m:
                c["reverts"] = m.group(1)
                reverted[m.group(1)] = c["sha"]
        elif CONVENTIONAL_FIX.match(c["subject"]) or FIX_RE.search(c["subject"]) or FIX_RE.search(clean_message(c["body"])):
            c["kind"] = "fix"
    for c in commits:
        for short, by in reverted.items():
            if c["sha"].startswith(short) and c.get("kind") != "revert":
                c["kind"] = "reverted"
                c["reverted_by"] = by
    items = []
    for c in commits:
        kind = c.get("kind")
        if not kind:
            continue
        text = c["subject"]
        body = clean_message(c["body"])
        if body:
            text += "\n" + body
        added, removed = diff_identifiers(repo, c["sha"])
        item = {"id": f"commit:{c['sha'][:10]}", "source": "commit", "kind": kind, "ref": c["sha"],
                "text": text, "paths": c["paths"], "line": None, "author": c["author"], "date": c["date"], "url": None,
                "diff_ids": added + [x for x in removed if x not in added],
                "diff_added": added, "diff_removed": removed}
        if c.get("reverts"):
            item["reverts"] = c["reverts"]
        if c.get("reverted_by"):
            item["reverted_by"] = c["reverted_by"]
        items.append(item)
    return items


def code_comment_items(repo: Path, limit: int = 200) -> list[dict]:
    try:
        out = run_git(repo, "grep", "-n", "-I", "-E", WORKAROUND_RE)
    except RuntimeError:  # git grep exits 1 when nothing matches
        return []
    items = []
    for ln in out.splitlines()[:limit]:
        path, _, rest = ln.partition(":")
        lineno, _, text = rest.partition(":")
        text = re.sub(r"^\s*(#+|//+|/\*+|\*+|--|<!--)\s*", "", text).strip()
        if not text:
            continue
        items.append({"id": f"code:{path}:{lineno}", "source": "code-comment", "kind": None, "ref": f"{path}:{lineno}",
                      "text": text, "paths": [path], "line": int(lineno) if lineno.isdigit() else None,
                      "author": None, "date": None, "url": None})
    return items


def _json_values(raw: str) -> list:
    """Parse one JSON document, concatenated documents (`gh api --paginate`), or JSON lines."""
    dec = json.JSONDecoder()
    vals, i = [], 0
    raw = raw.strip()
    while i < len(raw):
        while i < len(raw) and raw[i] in " \t\r\n,":
            i += 1
        if i >= len(raw):
            break
        val, i = dec.raw_decode(raw, i)
        vals.append(val)
    return vals


def _flatten_comment_objects(val) -> list[dict]:
    if isinstance(val, list):
        out = []
        for v in val:
            out.extend(_flatten_comment_objects(v))
        return out
    if isinstance(val, dict):
        if "body" in val or "text" in val:
            return [val]
        out = []
        for key in ("items", "comments", "review_comments", "reviews", "nodes", "data"):
            if key in val:
                out.extend(_flatten_comment_objects(val[key]))
        return out
    return []


def _login(obj) -> str | None:
    if isinstance(obj, dict):
        return obj.get("login") or obj.get("name")
    return obj if isinstance(obj, str) else None


def review_items_from_json(raw: str, origin: str) -> list[dict]:
    items = []
    for n, obj in enumerate(_flatten_comment_objects(_json_values(raw))):
        if "source" in obj and "ref" in obj and "text" in obj:  # already normalized (collect_feedback.py)
            item = {k: obj.get(k) for k in ("id", "source", "kind", "ref", "text", "paths", "line", "author", "date",
                                            "url", "reverts", "reverted_by", "diff_ids", "diff_added", "diff_removed")
                    if obj.get(k) is not None or k in ("kind", "line", "author", "date", "url")}
            item["paths"] = item.get("paths") or []
            item["id"] = item.get("id") or f"{item['source']}:{n}"
            items.append(item)
            continue
        body = (obj.get("body") or obj.get("text") or "").strip()
        if not body:
            continue
        cid = obj.get("id") or obj.get("node_id") or n
        url = obj.get("html_url") or obj.get("url")
        path = obj.get("path")
        line = obj.get("line") or obj.get("original_line") or obj.get("position")
        date = (obj.get("created_at") or obj.get("createdAt") or obj.get("submitted_at") or "")[:10] or None
        items.append({"id": f"review:{cid}", "source": "review", "kind": None,
                      "ref": url or f"{origin}#{cid}", "text": body, "paths": [path] if path else [],
                      "line": line if isinstance(line, int) else None,
                      "author": _login(obj.get("user")) or _login(obj.get("author")), "date": date, "url": url})
    return items


def markdown_items(text: str, origin: str, source: str = "chat") -> list[dict]:
    """One item per bullet (with continuation lines) or paragraph. Headings are context, not items."""
    items: list[dict] = []
    block: list[str] = []
    start = 0
    in_fence = False

    def flush():
        nonlocal block
        body = " ".join(s.strip() for s in block).strip()
        if len(body.split()) >= 3:
            items.append({"id": f"{source}:{origin}:{start}", "source": source, "kind": None,
                          "ref": f"{origin}:{start}", "text": body, "paths": [], "line": start,
                          "author": None, "date": None, "url": None})
        block = []

    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            if block:
                block.append(stripped)
            continue
        if not stripped or stripped.startswith("#") or re.fullmatch(r"[-*_]{3,}", stripped):
            flush()
            continue
        stripped = re.sub(r"^>\s?", "", stripped)
        bullet = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
        if bullet:
            flush()
            start = n
            block = [bullet.group(2)]
        else:
            if not block:
                start = n
            block.append(stripped)
    flush()
    return items


def load_feedback(path: Path, source: str | None = None) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    origin = path.name
    head = raw.lstrip()[:1]
    if head in ("[", "{"):
        try:
            return review_items_from_json(raw, origin)
        except json.JSONDecodeError:
            pass
    src = source or ("review" if "review" in path.name.lower() else "chat")
    return markdown_items(raw, origin, src)


def agents_rules(repo: Path, files: list[Path] | None = None) -> list[dict]:
    """Rules from agent instruction files: table rows (with enforcement) and bullets."""
    cands = files if files is not None else [repo / n for n in ("AGENTS.md", "CLAUDE.md")]
    rules = []
    for f in cands:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        table_lines = set()
        res = rule_table.check(f)
        for r in res["rules"]:
            rules.append({"file": f.name, "line": r["line"], "text": r["rule"], "enforced_by": r["enforced_by"],
                          "status": r["status"]})
            table_lines.add(r["line"])
        for n, ln in enumerate(lines, 1):
            m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.{8,})$", ln)
            if m and n not in table_lines:
                rules.append({"file": f.name, "line": n, "text": m.group(1).strip(), "enforced_by": None, "status": "prose"})
    return rules


# ---------------------------------------------------------------- clustering

def _cos(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _normalize(v: dict) -> dict:
    n = math.sqrt(sum(x * x for x in v.values()))
    return {k: x / n for k, x in v.items()} if n else {}


def vectorize(items: list[dict]):
    """TF-IDF vectors. Keys seen only in a commit's diff (not its message) weigh less."""
    surfaces: dict = {}
    text_docs = [tokens(it.get("text") or "", it.get("paths") or [], surfaces) for it in items]
    diff_docs = []
    for it in items:
        keys: set[str] = set()

        def note(key: str, surface: str, keys=keys) -> None:
            keys.add(key)
            surfaces.setdefault(key, Counter())[surface] += 1

        code_keys(" ".join(it.get("diff_ids") or []), note)
        diff_docs.append(keys)
    docs = [t | d for t, d in zip(text_docs, diff_docs)]
    df = Counter(k for d in docs for k in d)
    n = len(items)
    idf = {k: math.log((1 + n) / (1 + c)) + 1.0 for k, c in df.items()}
    vecs = []
    for t, d in zip(text_docs, docs):
        v = {}
        for k in d:
            if df[k] < 2:
                continue  # unshared keys cannot make two items similar
            w = idf[k] * (1.5 if k.startswith(STRONG_PREFIXES) else 1.0)
            v[k] = w if k in t else 0.7 * w
        vecs.append(_normalize(v))
    return docs, vecs, df, idf, surfaces


def _occurrences(members: list[dict]) -> int:
    """Distinct occurrences: a revert and the commit it reverts are one mistake."""
    shas = {m["ref"] for m in members if m["source"] == "commit"}
    n = 0
    for m in members:
        if m["source"] == "commit" and m.get("kind") == "revert" and m.get("reverts"):
            if any(s.startswith(m["reverts"]) for s in shas):
                continue
        n += 1
    return n


def cluster_items(items: list[dict], threshold: float = 0.3, min_count: int = 2, rules: list[dict] | None = None):
    """Group items into classes. Returns (classes, unclassified)."""
    if not items:
        return [], []
    order = sorted(range(len(items)), key=lambda i: (SOURCE_ORDER.get(items[i]["source"], 9),
                                                     items[i].get("date") or "", items[i]["id"]))
    docs, vecs, df, idf, surfaces = vectorize(items)

    def best_group(v: dict, cents: list[dict], index: dict) -> tuple[int, float]:
        cand: set[int] = set()
        for k in v:
            cand |= index.get(k, set())
        best, best_sim = -1, 0.0
        for gi in sorted(cand):
            sim = _cos(v, cents[gi])
            if sim > best_sim + 1e-12:
                best, best_sim = gi, sim
        return best, best_sim

    def rebuild(groups: list[list[int]]):
        sums, cents, index = [], [], {}
        for gi, g in enumerate(groups):
            s: dict = {}
            for i in g:
                for k, x in vecs[i].items():
                    s[k] = s.get(k, 0.0) + x
                    index.setdefault(k, set()).add(gi)
            sums.append(s)
            cents.append(_normalize(s))
        return sums, cents, index

    # Leader pass: join the most similar class if similar enough, else start a new one.
    groups: list[list[int]] = []
    sums: list[dict] = []
    cents: list[dict] = []
    index: dict[str, set[int]] = {}
    for i in order:
        v = vecs[i]
        gi, sim = best_group(v, cents, index)
        if gi >= 0 and sim >= threshold:
            groups[gi].append(i)
            for k, x in v.items():
                sums[gi][k] = sums[gi].get(k, 0.0) + x
                index.setdefault(k, set()).add(gi)
            cents[gi] = _normalize(sums[gi])
        else:
            gi = len(groups)
            groups.append([i])
            sums.append(dict(v))
            cents.append(_normalize(v))
            for k in v:
                index.setdefault(k, set()).add(gi)

    # Refinement: move each item to its best centroid until assignments are stable.
    for _ in range(5):
        assign: dict[int, int | None] = {}
        for gi, g in enumerate(groups):
            for i in g:
                v = vecs[i]
                own = _cos(v, cents[gi]) if v else 0.0
                bj, bs = best_group(v, cents, index)
                if bj < 0 or bs <= own + 1e-9:
                    bj, bs = gi, own
                assign[i] = None if (bs < threshold * 0.8 and len(g) > 1) else bj
        buckets: dict[int, list[int]] = {}
        singles: list[list[int]] = []
        for i in order:
            if assign[i] is None:
                singles.append([i])
            else:
                buckets.setdefault(assign[i], []).append(i)
        new_groups = [buckets[k] for k in sorted(buckets)] + singles
        if sorted(map(tuple, new_groups)) == sorted(map(tuple, groups)):
            break
        groups = new_groups
        sums, cents, index = rebuild(groups)

    # Second chance: a multi-item group below min_count (typically a revert and the commit it
    # reverts, one occurrence with a terse message) joins the closest class if moderately similar.
    rank = {i: r for r, i in enumerate(order)}

    def occ(g: list[int]) -> int:
        return _occurrences([items[i] for i in g])

    while True:
        sums, cents, index = rebuild(groups)
        big = [gi for gi, g in enumerate(groups) if occ(g) >= min_count]
        moved = False
        for gi, g in enumerate(groups):
            if len(g) < 2 or occ(g) >= min_count or not cents[gi]:
                continue
            best, best_sim = -1, 0.0
            for gj in big:
                sim = _cos(cents[gi], cents[gj])
                if sim > best_sim + 1e-12:
                    best, best_sim = gj, sim
            if best >= 0 and best_sim >= threshold * 0.6:
                groups[best] = sorted(groups[best] + g, key=rank.get)
                groups[gi] = []
                moved = True
                break
        groups = [g for g in groups if g]
        if not moved:
            break

    classes, unclassified = [], []
    for g in groups:
        members = [items[i] for i in g]
        count = _occurrences(members)
        if count < min_count:
            unclassified.extend(members)
            continue
        key_counts = Counter(k for i in g for k in docs[i])
        shared = [k for k, c in key_counts.items() if c >= 2]
        shared.sort(key=lambda k: (-key_counts[k], -idf.get(k, 0) * (1.5 if k.startswith(STRONG_PREFIXES) else 1), k))
        centroid = _normalize({k: sum(vecs[i].get(k, 0.0) for i in g) for k in shared})
        cls = {
            "count": count,
            "items": len(members),
            "sources": dict(Counter(m["source"] for m in members)),
            "signature": [display_key(k, surfaces) for k in shared[:8]],
            "signature_keys": shared[:8],
            "evidence": sorted(members, key=lambda m: (m.get("date") or "", m["id"]), reverse=True),
            "rules": [],
            "repeat": False,
        }
        if rules:
            for r in rules:
                rk = tokens(r["text"])
                rv = _normalize({k: idf.get(k, 0.0) for k in rk if k in idf})
                strong_overlap = any(k.startswith(STRONG_PREFIXES) and k in rk for k in shared)
                if strong_overlap or _cos(rv, centroid) >= 0.25:
                    cls["rules"].append(r)
            cls["repeat"] = any(r.get("status") != "enforced" for r in cls["rules"])
        classes.append(cls)
    classes.sort(key=lambda c: (-c["count"], -len(c["sources"]), c["signature"]))
    for n, c in enumerate(classes, 1):
        c["id"] = f"C{n}"
    unclassified.sort(key=lambda m: m["id"])
    return classes, unclassified


# ---------------------------------------------------------------- reporting

def short(text: str, n: int = 100) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


def describe_item(m: dict) -> str:
    if m["source"] == "commit":
        files = ", ".join(m.get("paths", [])[:3]) + (" …" if len(m.get("paths", [])) > 3 else "")
        tag = {"fix": "fix", "revert": "revert", "reverted": "reverted"}.get(m.get("kind"), "commit")
        return f"{tag:<8} {m['ref'][:10]} {m.get('date') or ''} {short(m['text'].splitlines()[0], 80)}" + (f"  [{files}]" if files else "")
    where = m.get("paths")[0] if m.get("paths") else ""
    if where and m.get("line"):
        where += f":{m['line']}"
    if where == m["ref"]:
        where = ""
    who = f" @{m['author']}" if m.get("author") else ""
    return f"{m['source']:<8} {m['ref']}{who}{' ' + where if where else ''} \"{short(m['text'], 90)}\""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mine_mistakes.py",
        description="Group a repo's reverts, fix commits, review comments, and workaround comments into mistake "
                    "classes (keyword/path signatures). Keeps classes seen at least --min-count times and prints the "
                    "evidence: commit shas, comment ids, file:line refs.",
        epilog="examples:\n"
               "  mine_mistakes.py .\n"
               "  mine_mistakes.py . --since 2026-07-01 --feedback review-comments.json chat-feedback.md --json\n"
               "  gh api repos/OWNER/REPO/pulls/comments --paginate > review-comments.json   # read-only fetch\n\n"
               "Classes that match an AGENTS.md/CLAUDE.md rule not enforced by a check are marked `repeat`:\n"
               "the rule exists and the mistake still happened, so fix it higher up the pyramid.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("repo", type=Path, help="repository to mine")
    ap.add_argument("--since", help="only commits after this date (git --since syntax, e.g. 2026-07-01 or '90 days ago')")
    ap.add_argument("--feedback", nargs="+", type=Path, default=[], metavar="FILE",
                    help="review-comment JSON (gh api shape) or markdown notes")
    ap.add_argument("--agents-file", nargs="+", type=Path, default=None, metavar="FILE",
                    help="agent instruction files to match rules from (default: REPO/AGENTS.md REPO/CLAUDE.md)")
    ap.add_argument("--min-count", type=int, default=2, help="minimum occurrences for a class (default 2)")
    ap.add_argument("--threshold", type=float, default=0.3, help="cosine similarity to join a class (default 0.3)")
    ap.add_argument("--max-commits", type=int, default=2000, help="newest N commits to read (default 2000)")
    ap.add_argument("--no-git", action="store_true", help="skip git history (feedback files only)")
    ap.add_argument("--no-code-comments", action="store_true", help="skip HACK/WORKAROUND/FIXME/XXX comments")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"mine_mistakes: {repo} is not a directory", file=sys.stderr)
        return 2
    items: list[dict] = []
    git_ok = not args.no_git and is_git_repo(repo)
    if not args.no_git and not git_ok:
        print(f"mine_mistakes: {repo} is not a git repo; reading feedback files only (pass --no-git to silence)",
              file=sys.stderr)
    if git_ok:
        items += git_items(repo, args.since, args.max_commits)
        if not args.no_code_comments:
            items += code_comment_items(repo)
    for f in args.feedback:
        if not f.is_file():
            print(f"mine_mistakes: feedback file {f} not found", file=sys.stderr)
            return 2
        items += load_feedback(f)
    seen, unique = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    items = unique
    rules = agents_rules(repo, args.agents_file)
    classes, unclassified = cluster_items(items, args.threshold, args.min_count, rules)

    counts = dict(Counter(it["source"] for it in items))
    if args.json:
        print(json.dumps({"repo": str(repo), "since": args.since, "items": len(items), "counts": counts,
                          "min_count": args.min_count, "classes": classes,
                          "unclassified": unclassified, "rules_read": len(rules)}, indent=2, default=str))
        return 0

    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "nothing"
    print(f"mine_mistakes: {len(items)} items ({summary}) from {repo}" + (f" since {args.since}" if args.since else ""))
    if not classes:
        print(f"No class reached {args.min_count} occurrences. Nothing to fix yet; rerun after more history or pass --feedback.")
    for c in classes:
        srcs = ", ".join(f"{k}×{v}" for k, v in sorted(c["sources"].items()))
        print(f"\n{c['id']}  {c['count']} occurrences [{srcs}]  signature: {', '.join(c['signature'][:6])}")
        for r in c["rules"]:
            if r.get("status") == "enforced":
                state = "rule enforced, mistake still happened (check the check)"
            else:
                state = f"REPEAT ({r.get('status')}: nothing that runs enforces it)"
            print(f"  {state}: {r['file']}:{r['line']} \"{short(r['text'], 70)}\"" +
                  (f" <- {r['enforced_by']}" if r.get("enforced_by") else ""))
        for m in c["evidence"]:
            print("  - " + describe_item(m))
    if unclassified:
        print(f"\nSeen once ({len(unclassified)}), not a class yet:")
        for m in unclassified[:15]:
            print("  - " + describe_item(m))
        if len(unclassified) > 15:
            print(f"  … {len(unclassified) - 15} more (use --json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
