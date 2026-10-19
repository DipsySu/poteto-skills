#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --help ]]; then echo 'usage: lint.sh (reject direct persistence outside store.py)'; exit 0; fi
cd "$(dirname "$0")/.."
python3 - <<'PY'
import ast
from pathlib import Path
bad = []
for path in Path('todo').glob('*.py'):
    if path.name == 'store.py':
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('write_text', 'dump', 'replace'):
            bad.append(f'{path}:{node.lineno}: persist through Store.add/Store.transition in store.py')
print('\n'.join(bad) if bad else 'persistence boundary: clean')
raise SystemExit(bool(bad))
PY
