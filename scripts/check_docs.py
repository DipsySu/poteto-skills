#!/usr/bin/env python3
"""Check first-party README, mapping and skill docs against this collection's tree.

Only inventoried helpers run, with --help (cached, five-second timeout). Shell
examples are tokenized, never executed. Bash/sh fences and command-like inline
code support literal assignments, $S, $SKILL_DIR and sibling skill paths; dynamic
shell expressions, generated verify-* paths and vendored tools are out of scope.
Links support inline/image links (titles and angle destinations), reference
definitions, full/collapsed references and known shortcut references. Fenced
examples are not links. Heading anchors use Unicode GitHub-style slugs, including
duplicate suffixes. Remote URLs are not fetched.

Supported flags and recommended README usage are separate checks. A skill may
declare an explicit policy, e.g.:
  <!-- check-docs: readme-avoid $SKILL_DIR/scripts/mine_mistakes.py --since -->
Other valid README-only flags are allowed; absence from an example is no ban.
"""
import argparse
from functools import lru_cache
from html import unescape
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
FLAGS = re.compile(r'(?<![\w-])--[a-zA-Z][\w-]*')
SCRIPT = re.compile(r'[\w.$~/{<>}-]*scripts/[\w.<>-]+\.(?:py|sh)\b')
CITATION = re.compile(r'\bS\d+\b')
NUMBERS = dict(enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()))


def inventory(root):
    skills = sorted((root/'skills').glob('*/SKILL.md'))
    helpers = set((root/'scripts').glob('*.py')) | set((root/'scripts').glob('*.sh'))
    helpers |= {p for skill in skills for p in (skill.parent/'scripts').glob('*')
                if p.is_file() and p.suffix in ('.py', '.sh')}
    helpers.add(root/'install.sh')
    helpers |= set((root/'examples').glob('*.sh')) | set((root/'examples').glob('*.py'))
    notes = root/'research/NOTES.md'
    sources = set(re.findall(r'^\|\s*(S\d+)\s*\|', notes.read_text(), re.M))
    return skills, {p.resolve() for p in helpers if p.is_file()}, sources


def markdown_lines(text):
    """Yield (line, text, fence language); fence delimiters have no text."""
    fence = None
    language = ''
    for n, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})([^\n]*)$', line)
        if match and fence is None:
            fence = match[1]
            language = match[2].strip().lower()
            yield n, '', language
        elif fence and re.fullmatch(r'\s{0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
            fence = None
            language = ''
            yield n, '', language
        else:
            yield n, line, language if fence else None


def headings(text):
    used = set()
    previous = ''
    for _, line, fence in markdown_lines(text):
        if fence is not None:
            continue
        match = re.match(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+\s*)?$', line)
        title = match[1] if match else previous if re.fullmatch(r' {0,3}(?:=+|-+)\s*', line) else None
        previous = line.strip()
        if title is None:
            continue
        title = re.sub(r'!?\[([^]]+)\]\([^)]*\)', r'\1', title)
        title = unescape(re.sub(r'<[^>]*>', '', title)).lower()
        slug = ''.join(c for c in title if c in '_-' or not unicodedata.category(c).startswith(('P', 'S', 'C')))
        slug = re.sub(r'\s', '-', slug)
        candidate, count = slug, 0
        while candidate in used:
            count += 1
            candidate = f'{slug}-{count}'
        used.add(candidate)
    return used


# One nested pair of parentheses covers ordinary Markdown paths and URL titles.
DEST = r'(?:<([^>\n]*)>|((?:\\.|[^\s()\\]|\([^()\n]*\))+))'
INLINE_LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*' + DEST + r'(?:\s+(?:"[^"\n]*"|\x27[^\x27\n]*\x27|\([^()\n]*\)))?\s*\)')
DEFINITION = re.compile(r'^ {0,3}\[([^]]+)\]:\s*' + DEST)


def check_links(file, text, root, report):
    lines = [(n, re.sub(r'`+[^`]*`+', '', line)) for n, line, fence in markdown_lines(text) if fence is None]
    definitions = {}
    destinations = []
    key = lambda label: ' '.join(label.lower().split())
    for n, line in lines:
        match = DEFINITION.match(line)
        if match:
            definitions[key(match[1])] = match[2] if match[2] is not None else match[3]
            destinations.append((n, definitions[key(match[1])]))
    for n, line in lines:
        if DEFINITION.match(line):
            continue
        destinations.extend((n, m[1] if m[1] is not None else m[2]) for m in INLINE_LINK.finditer(line))
        remainder = INLINE_LINK.sub('', line)
        for m in re.finditer(r'!?\[([^]]+)\]\[([^]]*)\]', remainder):
            label = key(m[2] or m[1])
            if label not in definitions:
                report(file, n, f'undefined link reference: {label}')
        remainder = re.sub(r'!?\[[^]]+\]\[[^]]*\]', '', remainder)
        for m in re.finditer(r'!?\[([^]]+)\]', remainder):
            if key(m[1]) in definitions:
                destinations.append((n, definitions[key(m[1])]))
    for n, destination in destinations:
        target = urlsplit(unescape(destination))
        if target.scheme or target.netloc:
            continue
        path = (file.parent/unquote(target.path)).resolve() if target.path else file
        if not path.is_relative_to(root) or not path.exists():
            report(file, n, f'broken relative link: {destination}')
        elif target.fragment:
            try:
                anchors = headings(path.read_text()) if path.is_file() else set()
            except (OSError, UnicodeError):
                anchors = set()
            if unquote(target.fragment) not in anchors:
                report(file, n, f'unknown heading anchor: {destination}')


def check_citations(file, text, sources, required, report):
    found = False
    for n, line in enumerate(text.splitlines(), 1):
        for source in CITATION.findall(line):
            found = True
            if source not in sources:
                report(file, n, f'unknown source citation: {source}')
    if required and not found:
        report(file, 1, 'missing source ID; every skill needs attribution from research/NOTES.md')


def check_claims(file, text, skill_count, sources, report):
    counts = {word: n for n, word in NUMBERS.items()}
    for n, line in enumerate(text.splitlines(), 1):
        for match in re.finditer(r'\ball (\d+|' + '|'.join(counts) + r') skills\b', line, re.I):
            count = int(match[1]) if match[1].isdigit() else counts[match[1].lower()]
            if count != skill_count:
                report(file, n, f'skill count claims {count}; tree has {skill_count}')
        for match in re.finditer(r'\bsources\s+S(\d+)\s*(?:[-–—]|\.\.)\s*S(\d+)\b', line, re.I):
            first, last = map(int, match.groups())
            actual = sorted(int(s[1:]) for s in sources)
            if not actual or first != actual[0] or last != actual[-1] or len(actual) != last-first+1:
                report(file, n, 'source range differs from research/NOTES.md; update the claim too')


def expand(value, variables):
    for _ in range(8):
        updated = re.sub(r'\$(?:\{(\w+)\}|(\w+))', lambda m: variables.get(m[1] or m[2], m[0]), value)
        if updated == value:
            break
        value = updated
    return value


def resolve_script(value, file, root):
    """Return a collection path, or None for a generated/external target."""
    if any(c in value for c in '<>$`*') or any(p.startswith('verify-') for p in Path(value).parts):
        return None
    value = value.removeprefix('./')
    if value.startswith('/path/to/') or value.startswith('tools/'):
        return None
    if value.startswith('scripts/') or value in ('install.sh',) or value.startswith(('skills/', 'examples/')):
        path = root/value
    elif re.match(r'^[\w-]+/scripts/', value):
        path = root/'skills'/value
    elif value.startswith('../'):
        path = file.parent/value
    elif value.startswith('/'):
        path = Path(value)
    else:
        return None
    return path.resolve()


def variables_for(file, root):
    variables = {'S': str(root/'skills')}
    if file.name == 'SKILL.md':
        variables['SKILL_DIR'] = str(file.parent)
    return variables


def check_paths(file, text, root, report):
    variables = variables_for(file, root)
    for n, line in enumerate(text.splitlines(), 1):
        remote = [m.span() for m in re.finditer(r'\b\w+://[^\s<>`]+', line)]
        for match in SCRIPT.finditer(line):
            if any(start <= match.start() < end for start, end in remote):
                continue
            path = resolve_script(expand(match[0], variables), file, root)
            if path is not None and (not path.is_relative_to(root) or not path.is_file()):
                report(file, n, f'missing script: {match[0]}')


@lru_cache(maxsize=None)
def help_contract(script, subcommand=()):
    """Read options only from help's option declarations, not its examples."""
    argv = [sys.executable, str(script)] if script.suffix == '.py' else ['bash', str(script)]
    result = subprocess.run([*argv, *subcommand, '--help'], cwd=script.parent,
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ValueError(f'--help exited {result.returncode}')
    options = {}
    commands = set()
    for line in result.stdout.splitlines():
        match = re.match(r'^\s+\{([\w,-]+)\}\s*$', line)
        if match:
            commands.update(match[1].split(','))
        if not re.match(r'^\s+-', line):
            continue
        declaration = re.split(r'\s{2,}', line.strip())[0]
        flags = FLAGS.findall(declaration)
        takes_value = bool(re.search(r'--[\w-]+(?:[= ]+)[\[A-Z]', declaration))
        options.update((flag, takes_value) for flag in flags)
    # Small shell helpers list --help only in their case arm, not their usage.
    options['--help'] = False
    return options, commands


def command_tokens(command, variables, root):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
    lexer.whitespace_split = True
    tokens = list(lexer)
    while tokens and re.match(r'^\w+=', tokens[0]):
        name, value = tokens.pop(0).split('=', 1)
        value = value.replace('~/.claude/skills', str(root/'skills')).replace('~/.cursor/skills', str(root/'skills'))
        variables[name] = expand(value, variables)
    expanded = []
    for token in tokens:
        value = expand(token, variables)
        # A whole command variable ($P) has shell word-splitting semantics.
        expanded.extend(shlex.split(value) if re.fullmatch(r'\$\w+|\$\{\w+\}', token) and value != token else [value])
    return expanded


def commands(file, text, root, report):
    variables = variables_for(file, root)
    pending, start = '', 0
    for n, line, language in markdown_lines(text):
        if language in ('bash', 'sh', 'shell'):
            if not pending:
                start = n
            pending += line.rstrip().removesuffix('\\') + ' '
            if line.rstrip().endswith('\\'):
                continue
            candidates = [(start, pending)]
            pending = ''
        elif language is None:
            candidates = [(n, m[1]) for m in re.finditer(r'`([^`\n]+)`', line)]
        else:
            continue
        for n, command in candidates:
            try:
                tokens = command_tokens(command, variables, root)
            except ValueError as error:
                if language in ('bash', 'sh', 'shell'):
                    report(file, n, f'cannot tokenize shell example: {error}')
                continue
            segment = []
            for token in [*tokens, ';']:
                if token in (';', '&&', '||', '|', '&'):
                    if segment:
                        yield n, segment
                    segment = []
                else:
                    segment.append(token)


def check_commands(file, text, root, helpers, policies, report):
    for n, tokens in commands(file, text, root, report):
        if tokens[0] in ('python', 'python3', 'bash', 'sh'):
            tokens = tokens[1:]
        if not tokens or Path(tokens[0]).suffix not in ('.py', '.sh'):
            continue
        script = resolve_script(tokens[0], file, root)
        if script is None and '/' not in tokens[0] and file.name == 'SKILL.md':
            own = file.parent/'scripts'/tokens[0]
            if own in helpers:
                script = own
        # MAPPING's table uses bare helper names with a skill column.
        if script is None and '/' not in tokens[0] and file.name == 'MAPPING.md':
            row = text.splitlines()[n-1].split('|')
            owner = row[1].strip(' `') if len(row) > 2 else ''
            candidates = [root/'skills'/owner/'scripts'/tokens[0], root/'scripts'/tokens[0]]
            script = next((p for p in candidates if p in helpers), None)
        if script is not None and (not script.is_relative_to(root) or not script.is_file()):
            report(file, n, f'missing script: {tokens[0]}')
        if script not in helpers:
            continue
        try:
            options, subcommands = help_contract(script)
            args = tokens[1:]
            skip = False
            for arg in args:
                if skip:
                    skip = False
                    continue
                if arg == '--' or arg in ('>', '>>', '<'):
                    break
                arg = arg.strip('[]')
                if arg in subcommands:
                    suboptions, _ = help_contract(script, (arg,))
                    options = dict(options, **suboptions)
                    subcommands = set()
                    continue
                if not arg.startswith('--'):
                    continue
                # Table synopsis alternatives, e.g. [--propose\|--apply].
                flags = arg.split('=', 1)[0].split('|')
                for flag in flags:
                    if flag not in options:
                        report(file, n, f'{script.name}: unsupported flag {flag} in this command/subcommand')
                    elif file == root/'README.md' and (script, flag) in policies:
                        report(file, n, f'{script.name}: README policy avoids {flag}; follow the skill default')
                if '=' not in arg:
                    skip = options.get(flags[-1], False)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            report(file, n, f'{script.name}: cannot inspect bounded --help: {error}')


def check(root):
    root = Path(root).resolve()
    issues = []
    def report(file, line, message):
        issues.append(f'{file.relative_to(root)}:{line}: {message}')
    skills, helpers, sources = inventory(root)
    if not sources:
        report(root/'research/NOTES.md', 1, 'no source rows found')
    files = [root/'README.md', root/'MAPPING.md', *skills]
    policies = set()
    for file in skills:
        for n, line in enumerate(file.read_text().splitlines(), 1):
            match = re.fullmatch(r'\s*<!-- check-docs: readme-avoid (\S+) (--[\w-]+) -->\s*', line)
            if line.strip().startswith('<!-- check-docs:') and not match:
                report(file, n, 'invalid check-docs policy declaration')
            if match:
                path = resolve_script(expand(match[1], variables_for(file, root)), file, root)
                if path not in helpers:
                    report(file, n, 'README policy must name an inventoried helper')
                else:
                    try:
                        options, _ = help_contract(path)
                        if match[2] not in options:
                            report(file, n, f'README policy names unsupported flag: {match[2]}')
                        else:
                            policies.add((path, match[2]))
                    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                        report(file, n, f'README policy cannot inspect bounded --help: {error}')
    for file in files:
        if not file.is_file():
            report(file, 1, 'missing documentation file')
            continue
        text = file.read_text()
        check_paths(file, text, root, report)
        check_commands(file, text, root, helpers, policies, report)
        check_links(file, text, root, report)
        check_citations(file, text, sources, file in skills, report)
        check_claims(file, text, len(skills), sources, report)
    return sorted(set(issues))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', type=Path, default=ROOT, help='collection checkout (default: this script\'s repo)')
    args = parser.parse_args()
    try:
        issues = check(args.repo)
    except (OSError, ValueError) as error:
        print(f'check_docs: {error}', file=sys.stderr)
        return 1
    for issue in issues:
        print(issue)
    if not issues:
        print('PASS docs: helper paths, commands, links, counts and source citations')
    return int(bool(issues))


if __name__ == '__main__':
    raise SystemExit(main())
