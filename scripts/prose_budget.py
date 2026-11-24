#!/usr/bin/env python3
"""Ratchet SKILL.md word counts and keep collection-branch history out of skills."""

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
BUDGET_FILE = Path('scripts/prose-budget.json')
BRANCH_HISTORY = re.compile(
    r"\b(?:relative\s+to\s+main|than\s+main|main\s+took|main\s+had|main['’]s)\b",
    re.IGNORECASE,
)
HEADING = re.compile(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def word_count(text):
    """Count whitespace-separated words outside frontmatter and the Sources H2."""
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((n for n in range(1, len(lines)) if lines[n].strip() == '---'), None)
        if end is None:
            raise ValueError('unclosed frontmatter; add its closing --- delimiter')
        lines = lines[end + 1:]
    body = []
    sources = False
    fence = None
    for line in lines:
        marker = FENCE.match(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
        elif marker:
            fence = marker[1]
        else:
            heading = HEADING.match(line)
            if heading and len(heading[1]) <= 2:
                sources = heading[1] == '##' and heading[2] == 'Sources'
        if not sources:
            body.append(line)
    return len('\n'.join(body).split())


def read_budgets(path, initialize):
    if not path.exists() and initialize:
        return {}
    if not path.exists():
        raise ValueError(f'{BUDGET_FILE}: missing budgets; run prose_budget.py --update')
    budgets = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(budgets, dict):
        raise ValueError(f'{BUDGET_FILE}: use an object mapping skill names to {{"budget": WORDS}}')
    for name, entry in budgets.items():
        if not isinstance(entry, dict) or type(entry.get('budget')) is not int or entry['budget'] < 0:
            raise ValueError(f'{BUDGET_FILE}: {name}: set budget to a nonnegative integer')
        if 'reason' in entry or 'date' in entry:
            if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
                raise ValueError(f'{BUDGET_FILE}: {name}: record a nonempty reason with --raise {name} --reason TEXT')
            try:
                date.fromisoformat(entry['date'])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f'{BUDGET_FILE}: {name}: set the raise date to YYYY-MM-DD') from None
    return budgets


def check(repo, update=False, raise_skill=None, reason=None):
    report = {'ok': False, 'skills': [], 'errors': [], 'notes': [], 'updated': []}
    errors, notes = report['errors'], report['notes']
    files = sorted((repo / 'skills').glob('*/SKILL.md'))
    if not files:
        errors.append('skills/: no SKILL.md files found; set --repo to the collection checkout')
        return report
    counts = {}
    for path in files:
        relative = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding='utf-8')
            counts[path.parent.name] = word_count(text)
        except (OSError, ValueError) as error:
            errors.append(f'{relative}: {error}; fix the SKILL.md file and rerun')
            continue
        for number, line in enumerate(text.splitlines(), 1):
            match = BRANCH_HISTORY.search(line)
            if match:
                errors.append(f'{relative}:{number}: {match[0]!r} measures the skill against our branch; move this history to CHANGELOG.md')
    budget_path = repo / BUDGET_FILE
    try:
        budgets = read_budgets(budget_path, update)
    except (OSError, ValueError) as error:
        errors.append(f'{error}; fix {BUDGET_FILE} and rerun')
        return report
    for name in sorted(set(budgets) - {p.parent.name for p in files}):
        errors.append(f'{BUDGET_FILE}: {name}: entry has no skill; remove the stale entry or restore skills/{name}/SKILL.md')
    if raise_skill is not None and raise_skill not in counts:
        errors.append(f'skills/{raise_skill}/SKILL.md: unknown skill; pass an existing skill name to --raise')

    # Invalid files/history must be fixed before recording any new budgets.
    if not errors:
        for name, words in counts.items():
            entry = budgets.get(name)
            if name == raise_skill:
                raised = {'budget': words, 'reason': reason.strip(), 'date': date.today().isoformat()}
                if entry != raised:
                    budgets[name] = raised
                    report['updated'].append(name)
            elif update and (entry is None or words < entry['budget']):
                budgets[name] = dict(entry or {}, budget=words)
                report['updated'].append(name)
                if entry is not None:
                    notes.append(f'skills/{name}/SKILL.md: {words} words, budget {entry["budget"]}; lowered budget to {words}')
        if report['updated']:
            try:
                budget_path.parent.mkdir(parents=True, exist_ok=True)
                budget_path.write_text(json.dumps(budgets, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            except OSError as error:
                errors.append(f'{BUDGET_FILE}: {error}; make the budget file writable and rerun')

    for name, words in counts.items():
        path = f'skills/{name}/SKILL.md'
        entry = budgets.get(name)
        budget = entry['budget'] if entry else None
        delta = words - budget if budget is not None else None
        status = 'missing' if budget is None else 'over' if delta > 0 else 'under' if delta < 0 else 'at-budget'
        report['skills'].append(dict(skill=name, path=path, words=words, budget=budget, delta=delta, status=status))
        if budget is None:
            errors.append(f'{path}: no budget entry; run prose_budget.py --update to add it to {BUDGET_FILE}')
        elif delta > 0:
            errors.append(
                f"{path}: {words} words, budget {budget} (+{delta}); agents read SKILL.md on every run: "
                "move script behavior into the script's --help or error text and history into CHANGELOG.md, "
                f'or run prose_budget.py --raise {name} --reason TEXT'
            )
        elif delta < 0:
            notes.append(f'{path}: {words} words, budget {budget} ({delta}); run prose_budget.py --update to lower the budget')
    report['ok'] = not errors
    return report


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Counts str.split() words, including code blocks, except YAML frontmatter and
the ## Sources section (through the next H1/H2). Branch-history phrases are
rejected on every line, including Sources: move them to CHANGELOG.md.

--update initializes missing budgets and lowers existing ones, never raises.
An over-budget skill still exits 1 during --update. --raise sets one budget to
its current count and records the reason and local date for review; other
skills still have to pass. Lowering a budget preserves its last raise record.
An orphaned budget fails: remove it or restore the corresponding skill.

Examples:
  prose_budget.py --repo .
  prose_budget.py --update
  prose_budget.py --raise formal-verification --reason "adds --exhaustive"

Exit 0: all skills pass. Exit 1: budget, history, or file error (see the fix).
Exit 2: invalid CLI arguments. --json emits one report object.''',
    )
    parser.add_argument('--repo', type=Path, default=ROOT, metavar='DIR', help='collection checkout (default: this script\'s checkout)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--update', action='store_true', help='initialize or lower budgets to current counts')
    mode.add_argument('--raise', dest='raise_skill', metavar='SKILL', help='set this skill budget to its current count; requires --reason')
    parser.add_argument('--reason', metavar='TEXT', help='nonempty explanation recorded with --raise')
    parser.add_argument('--json', action='store_true', help='print counts, budgets, errors, notes, and updated skills as JSON')
    args = parser.parse_args()
    if args.raise_skill is not None and (args.reason is None or not args.reason.strip()):
        parser.error('--raise requires a nonempty --reason TEXT')
    if args.reason is not None and args.raise_skill is None:
        parser.error('--reason requires --raise SKILL')
    report = check(args.repo.resolve(), args.update, args.raise_skill, args.reason)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for message in report['notes']:
            print(f'note: {message}')
        for message in report['errors']:
            print(message, file=sys.stderr)
        if report['updated']:
            print(f'Updated {BUDGET_FILE}: {", ".join(report["updated"])}')
        if report['ok']:
            print(f'prose_budget: {len(report["skills"])} skills within budget; no branch history')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
