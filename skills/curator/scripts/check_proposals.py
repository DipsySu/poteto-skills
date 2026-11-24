#!/usr/bin/env python3
"""Check a curator proposals file against the acceptance bar.

  check_proposals.py FILE [--fragment] [--fetch] [--skills-dir DIR] [--json]

Each proposal is an H2 section (`## 1. Title`) or an H3 section (`### Title`)
with its own Source bullet, either under an H2 or in a bare --fragment:
  - Source: <http(s) URL the curator actually fetched>   (one or more URLs)
  - Level: architecture | types | lint | test | verification | ci | prose | review
  - Skill: <existing skill name> | new:<name> | collection
  - Change: <the concrete change, preferably a script, lint, test, or type>
  - Why: <one sentence>                                   (optional)
A section with other proposal fields but no Source is still checked and fails.
A file with no proposals is valid only with an explicit `No proposals` line.
--fragment accepts a seat's standalone output; it does not relax any checks.

--fetch retains up to 5 MiB per source within curl's --max-time 20, following redirects.
GitHub blob URLs also fetch their raw file; a final 2xx from either representation passes.
Double-quoted spans of at least four words in Change/Why must match an own fetched source
or a collection file (tracked, proposals/, logs/curator/), excluding this proposal itself.
HTML/entities, case, curly punctuation, dashes, Markdown emphasis and whitespace are
normalized; ... or … parts must occur in order in one source/file. Proposed messages,
commands and test inputs belong in backticks and are skipped. No --fetch skips quotes.
Exit 0 valid, 1 invalid, 2 usage error.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

LEVELS = ["architecture", "types", "lint", "test", "verification", "ci", "prose", "review"]
ALIASES = {"static-analysis": "lint", "static analysis": "lint", "docs": "prose", "doc": "prose",
           "codebase": "architecture", "type": "types", "tests": "test", "formal": "verification"}
DETERMINISTIC = {"architecture", "types", "lint", "test", "verification", "ci"}
URL_RE = re.compile(r"https?://[^\s<>)\]`'\"]+")
FIELD_RE = re.compile(r"^\s*[-*]\s*\**([A-Za-z][\w ]*?)\**\s*:\s*(.*)$", re.M)
PROPOSAL_FIELDS = {"source", "level", "skill", "change"}
MAX_SOURCE_BYTES = 5 * 1024 * 1024
FETCH_SECONDS = 20


def normalize(text: str) -> str:
    text = html.unescape(text)
    # Block elements separate words; inline markup can split a single word.
    text = re.sub(r"</?(?:p|div|br|li|h[1-6]|tr|td|section|article|pre|blockquote)\b[^>]*>", " ",
                  text, flags=re.I)
    text = re.sub(r"<[^>]*>", "", text)
    text = text.casefold().translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'",
                                                  '–': '-', '—': '-', '*': '', '_': '', '`': ''}))
    return " ".join(text.split())


def quotations(body: str) -> list[str]:
    # Mask code without moving offsets: outer quotations can still contain code,
    # but a quoted proposed message inside a code span/fence is not a citation.
    masked = re.sub(r"(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)",
                    lambda m: re.sub(r"[^\n]", " ", m[0]), body)
    fields = list(re.finditer(r"^[ \t]*[-*][ \t]+\**([A-Za-z][\w ]*?)\**[ \t]*:[ \t]*(.*)$", masked, re.M))
    if not fields:
        return []
    indentation = min(len(m[0]) - len(m[0].lstrip(" \t")) for m in fields)
    fields = [m for m in fields if len(m[0]) - len(m[0].lstrip(" \t")) == indentation]
    quotes = []
    for i, field in enumerate(fields):
        if field[1].strip().lower() not in {"change", "why"}:
            continue
        start, end = field.start(2), fields[i + 1].start() if i + 1 < len(fields) else len(body)
        for match in re.finditer(r'"([^"]*)"|“([^”]*)”', masked[start:end]):
            group = 1 if match[1] is not None else 2
            quote = body[start + match.start(group):start + match.end(group)]
            if len(re.findall(r"\b\w+(?:['’]\w+)*\b", normalize(quote))) >= 4:
                quotes.append(quote)
    return quotes


def contains_quote(text: str, quote: str) -> bool:
    position = 0
    parts = [normalize(part) for part in re.split(r"\.\.\.|…", quote) if normalize(part)]
    for part in parts:
        found = text.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return bool(parts)


def collection_texts(root: Path, proposal: Path):
    """Tracked files plus curator history; the input cannot be its own evidence."""
    root, proposal = root.resolve(), proposal.resolve()
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                            capture_output=True, env=env)
    if result.returncode and (root / '.git').exists():
        raise OSError('cannot list tracked collection files; fix git ls-files in the collection and rerun')
    files = {root / os.fsdecode(name) for name in result.stdout.split(b'\0') if name} if result.returncode == 0 else set()
    for directory in ('proposals', 'logs/curator'):
        files.update((root / directory).rglob('*'))
    for file in sorted(files):
        resolved = file.resolve()
        if resolved == proposal or not resolved.is_relative_to(root) or not file.is_file():
            continue
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if b'\0' not in data:
            yield normalize(data.decode('utf-8', errors='replace'))


def section(title: str, body: str) -> dict:
    fields: dict[str, str] = {}
    for match in FIELD_RE.finditer(body):
        fields.setdefault(match.group(1).strip().lower(), match.group(2).strip())
    return {"title": title.strip(), "fields": fields, "urls": URL_RE.findall(fields.get("source", ""))}


def parse(text: str, *, with_quotes: bool = False) -> list[dict]:
    def read_section(title, body):
        item = section(title, body)
        if with_quotes:
            item['quotes'] = quotations(body)
        return item

    items = []
    parts = re.split(r"^##\s+", text, flags=re.M)
    for index, chunk in enumerate(parts):
        title, _, body = chunk.partition("\n") if index else ("", "", chunk)
        children = re.split(r"^###\s+", body, flags=re.M)
        if index and not any("source" in read_section("", child.partition("\n")[2])["fields"]
                             for child in children[1:]):
            # Legacy H2 proposals may organize their fields beneath descriptive
            # H3 headings. Only an H3 with its own Source starts the new format.
            items.append(read_section(title, body))
            continue
        parent_body = children[0]
        parent_is_proposal = index and (PROPOSAL_FIELDS.intersection(read_section(title, parent_body)["fields"])
                                        or re.match(r"\d+\.", title))
        proposals = []
        for child in children[1:]:
            child_title, _, child_body = child.partition("\n")
            item = read_section(child_title, child_body)
            if parent_is_proposal and not proposals and "source" not in item["fields"]:
                # Descriptive H3 fields still belong to a preceding H2 proposal,
                # up until the first H3 that starts its own sourced proposal.
                parent_body += "\n### " + child
            elif PROPOSAL_FIELDS.intersection(item["fields"]):
                proposals.append(item)
            elif proposals:
                # A descriptive subheading belongs to the preceding proposal.
                previous = proposals[-1]
                for key, value in item["fields"].items():
                    previous["fields"].setdefault(key, value)
                if with_quotes:
                    # A plain descriptive H3 continues the preceding Change.
                    previous['quotes'].extend(quotations('- Change: ' + child_body))
            else:
                parent_body += "\n### " + child
        if index:
            parent = read_section(title, parent_body)
            # Only an unnumbered, field-free H2 wrapping H3 proposals is a group.
            # Numbered H2 proposals and malformed field-bearing parents retain
            # every existing acceptance check, including missing Source.
            if not proposals or PROPOSAL_FIELDS.intersection(parent["fields"]) or re.match(r"\d+\.", title):
                items.append(parent)
        items.extend(proposals)
    return items


def raw_source(url: str) -> str | None:
    """Return the file-text companion of a GitHub blob URL, if any."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    match = re.fullmatch(r"/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", parsed.path)
    if parsed.hostname == "github.com" and match:
        return "https://raw.githubusercontent.com/" + "/".join(match.groups())
    return None


def fetch_source(url: str) -> tuple[int, str]:
    """Fetch a bounded body; curl writes headers to -D and the body to stdout."""
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="curator-source-") as temporary:
            headers = Path(temporary) / "headers"
            deadline = time.monotonic() + FETCH_SECONDS
            process = subprocess.Popen(
                ["curl", "-sS", "-L", "-D", str(headers), "--max-time", str(FETCH_SECONDS),
                 "-A", "poteto-skills-curator/0.1", url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            body = bytearray()
            capped = False
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        return 0, ""
                    chunk = os.read(process.stdout.fileno(), min(65536, MAX_SOURCE_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_SOURCE_BYTES:
                        # A range header is insufficient: servers can ignore it.
                        # Killing a live transfer here is the only accepted curl error.
                        if process.poll() is None:
                            capped = True
                            process.kill()
                        del body[MAX_SOURCE_BYTES:]
                        break
            returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if returncode and not capped:
                return 0, ""
            statuses = re.findall(rb"^HTTP/\S+\s+(\d{3})\b", headers.read_bytes(), re.M)
            status = int(statuses[-1]) if statuses else 0
            text = body.decode("utf-8", errors="replace")
            if urlsplit(url).hostname == "api.fxtwitter.com":
                # JSON syntax and keys are not quotations from the post itself.
                try:
                    pending = [json.loads(text)]
                except (ValueError, RecursionError):
                    return status, ""
                strings = []
                while pending:
                    value = pending.pop()
                    if isinstance(value, str):
                        strings.append(value)
                    elif isinstance(value, dict):
                        pending.extend(reversed(list(value.values())))
                    elif isinstance(value, list):
                        pending.extend(reversed(value))
                text = "\n".join(strings)
            return status, text
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0, ""
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def check(path: Path, skills_dir: Path | None, do_fetch: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    items = parse(text, with_quotes=do_fetch)
    skills = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()} if skills_dir and skills_dir.is_dir() else None
    problems, warnings = [], []
    fetched: dict[str, tuple[int, str]] = {}
    unmatched = []
    if not items and not re.search(r"^No proposals(?:[.:]|$)", text, re.M):
        problems.append("no proposal sections and no explicit No proposals outcome; add a ## or ### proposal "
                        "with Source, Level, Skill, and Change bullets, or write No proposals.")
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
            sources = []
            for u in it["urls"]:
                urls = [u]
                raw = raw_source(u)
                if raw:
                    urls.append(raw)
                reachable = False
                for url in urls:
                    if url not in fetched:
                        fetched[url] = fetch_source(url)
                    code, body = fetched[url]
                    it["fetch"][url] = code
                    if 200 <= code < 300:
                        reachable = True
                        sources.append(normalize(body))
                if not reachable:
                    codes = ', '.join(str(it['fetch'][url] or 'error') for url in urls)
                    problems.append(f"{where}: {u} is unreachable (HTTP {codes}); "
                                    "fetch an accessible page and update Source")
            for quote in it['quotes']:
                if not any(contains_quote(source, quote) for source in sources):
                    unmatched.append((where, len(it['urls']), quote))
    if unmatched:
        root = skills_dir.resolve().parent if skills_dir else Path(__file__).resolve().parents[3]
        try:
            for source in collection_texts(root, path):
                unmatched = [entry for entry in unmatched if not contains_quote(source, entry[2])]
                if not unmatched:
                    break
        except OSError as error:
            problems.append(f"collection quote lookup failed: {error}; make the collection readable "
                            "and git ls-files succeed, then rerun")
        for where, count, quote in unmatched:
            problems.append(f'{where}: quote "{quote}" is on none of its {count} sources and in no collection file; '
                            'quote the fetched page exactly, add the page you quoted to Source, '
                            'or put proposed text in backticks')
    det = sum(1 for it in items if it.get("level") in DETERMINISTIC)
    return {"file": str(path), "items": len(items), "deterministic": det, "prose_or_review": len(items) - det,
            "problems": problems, "warnings": warnings, "ok": not problems, "quotes_checked": do_fetch,
            "proposals": [{"title": it["title"], "level": it.get("level"), "skill": it["fields"].get("skill"),
                           "urls": it["urls"], "fetch": it.get("fetch")} for it in items]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_proposals.py",
        description="Validate a curator proposals file: every proposal has a source URL, a pyramid level, a target "
                    "skill, and a concrete change. Reads ## proposals and ### proposals with their own Source "
                    "bullets, under an H2 or standalone. Other proposal fields without Source still fail. "
                    "--fetch checks double-quoted phrases (4+ words) in Change/Why against that proposal's "
                    "fetched sources or collection files. Put proposed messages, commands and test inputs in backticks.",
        epilog="Fetch: at most 5 MiB per body, curl --max-time 20; GitHub blob URLs also fetch raw file text.\n"
               "api.fxtwitter.com JSON contributes string values. Matching normalizes HTML/entities, case,\n"
               "curly quotes/apostrophes, en/em dashes, Markdown emphasis and whitespace. Ellipsis (...) or\n"
               "… parts must occur in order within one page/file. Local evidence: tracked files plus\n"
               "proposals/ and logs/curator/, excluding the input itself. Collection root is the parent\n"
               "of --skills-dir, or this script's checkout. Without --fetch, quotes are not checked.\n\n"
               "examples:\n  check_proposals.py proposals/2026-10-19.md\n"
               "  check_proposals.py proposals/2026-10-19.md --fetch --skills-dir skills",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("proposals", type=Path, help="proposals/YYYY-MM-DD.md")
    ap.add_argument("--fragment", action="store_true",
                    help="accept standalone panel output (### proposals or No proposals.); all checks still apply")
    ap.add_argument("--fetch", action="store_true", help="fetch source bodies with curl and verify quotations")
    ap.add_argument("--skills-dir", type=Path, help="validate Skill: names here; parent is the collection for local quotes")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if not args.proposals.is_file():
        print(f"check_proposals: {args.proposals} not found", file=sys.stderr)
        return 2
    res = check(args.proposals, args.skills_dir, args.fetch)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if not res['quotes_checked']:
            print('quotes not checked (no --fetch)')
        for p in res["problems"]:
            print(f"FAIL  {p}")
        for w in res["warnings"]:
            print(f"warn  {w}")
        print(f"check_proposals: {'PASS' if res['ok'] else 'FAIL'}: {res['items']} proposal(s), "
              f"{res['deterministic']} above prose, {res['prose_or_review']} prose/review ({args.proposals})")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
