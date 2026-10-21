#!/usr/bin/env python3
"""Check packaging, executable contracts, syntax, install isolation and real example results."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {'create-verification-skill','maintain-verification-skill','correct','feedback-to-architecture',
            'architect','sharpen','formal-verification','curator'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-examples',action='store_true',help='regenerate logs/examples/summary.json with real skill executions')
    args = ap.parse_args()
    rows = []
    failures = []

    def check(name, fn):
        try:
            detail = fn()
        except Exception as error:
            rows.append((name,'FAIL',str(error)))
            failures.append(name)
        else:
            rows.append((name,'PASS',detail or ''))

    def require(ok, message):
        if not ok:
            raise AssertionError(message)

    def run(*argv, env=None, cwd=ROOT):
        result = subprocess.run([str(a) for a in argv],cwd=cwd,env=env,capture_output=True,text=True,timeout=120)
        require(result.returncode==0, f"{' '.join(map(str,argv))}: exit {result.returncode}\n{result.stdout}{result.stderr}")
        return result.stdout

    if args.run_examples:
        check('regenerate examples',lambda: run(ROOT/'scripts/run-examples.sh'))

    with tempfile.TemporaryDirectory(prefix='poteto-verify-',dir=(os.environ.get('TMPDIR') or '/tmp')) as scratch:
        temp = Path(scratch)
        skill_dirs = sorted(p for p in (ROOT/'skills').iterdir() if p.is_dir())

        def frontmatter():
            spec = importlib.util.spec_from_file_location('verify_contract',ROOT/'skills/create-verification-skill/scripts/check_verify_skill.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            require({p.name for p in skill_dirs} == EXPECTED, 'expected exactly the eight specified skills')
            for path in skill_dirs:
                require((path/'SKILL.md').is_file(),f'{path}: missing SKILL.md')
                text = (path/'SKILL.md').read_text()
                fm = module.frontmatter(text)
                require(fm is not None,f'{path}: invalid frontmatter')
                require(fm.get('name')==path.name,f'{path}: name does not match directory')
                desc = fm.get('description','')
                require(desc and 'Use' in desc and f'/{path.name}' in desc,f'{path}: description needs Use and slash invocation')
            return '8 valid skill frontmatters with invocation; source IDs checked by docs'
        check('skill frontmatter',frontmatter)

        def scripts():
            files = [ROOT/'install.sh']
            files += [p for d in skill_dirs for p in (d/'scripts').iterdir() if p.is_file() and p.suffix in ('.py','.sh')]
            files += list((ROOT/'scripts').glob('*.sh')) + list((ROOT/'scripts').glob('*.py'))
            files += [ROOT/'examples/build-todo-app.sh',ROOT/'examples/build_todo_app.py',ROOT/'examples/fill_verify.py',
                      ROOT/'examples/todo-app-src/scripts/lint.sh']
            for path in sorted(set(files)):
                require(os.access(path,os.X_OK),f'{path}: not executable')
                require(path.read_bytes().startswith(b'#!'),f'{path}: no shebang')
                run(path,'--help')
            return f'{len(set(files))} executable scripts; every --help exits 0'
        check('script contracts',scripts)

        sources = sorted(p for p in ROOT.rglob('*') if p.is_file() and p.suffix in ('.py','.sh')
                         and '.git' not in p.parts and '__pycache__' not in p.parts)
        def python_syntax():
            files = [p for p in sources if p.suffix=='.py']
            for n,path in enumerate(files):
                py_compile.compile(str(path),cfile=str(temp/f'compile-{n}.pyc'),doraise=True)
            return f'{len(files)} Python files compiled with py_compile'
        check('Python syntax',python_syntax)
        def bash_syntax():
            files = [p for p in sources if p.suffix=='.sh']
            for path in files:
                run('bash','-n',path)
            return f'{len(files)} shell files checked with bash -n'
        check('bash syntax',bash_syntax)

        def docs():
            for file in ('README.md','MAPPING.md','CHANGELOG.md','install.sh'):
                require((ROOT/file).is_file(),f'missing {file}')
            mapping = (ROOT/'MAPPING.md').read_text()
            require(all(f'`{name}`' in mapping for name in EXPECTED),'MAPPING.md omits a skill')
            output = run(sys.executable,ROOT/'scripts/check_docs.py','--repo',ROOT)
            return 'required files present; mapping lists every skill; ' + output.strip()
        check('docs and mapping',docs)

        def install():
            home = temp/'install-home'
            home.mkdir()
            env = dict(os.environ,HOME=str(home))
            output = run(ROOT/'install.sh','--dry-run',env=env)
            require('dry run, nothing changed' in output,'dry-run did not report its mode')
            require(not list(home.iterdir()),'dry-run mutated temporary HOME')
            # Real install/uninstall stays scoped to a project under this temporary root.
            project = temp/'project'
            sentinel = project/'.claude/skills/synced/keep'
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text('unrelated')
            run(ROOT/'install.sh','--project',project)
            require(all((project/'.claude/skills'/name).is_symlink() for name in EXPECTED),'symlink install incomplete')
            run(ROOT/'install.sh','--project',project,'--uninstall')
            require(sentinel.read_text()=='unrelated','uninstall touched unrelated skill')
            run(ROOT/'install.sh','--project',project,'--copy','--target','cursor')
            require(all((project/'.cursor/skills'/name/'SKILL.md').is_file() for name in EXPECTED),'copy install incomplete')
            run(ROOT/'install.sh','--project',project,'--uninstall','--target','cursor')
            require(sentinel.read_text()=='unrelated','copy uninstall touched unrelated skill')
            return 'temp HOME dry-run; project link/copy/uninstall preserve unrelated entries'
        check('installer isolation',install)

        def example():
            dest = temp/'todo-app'
            run(ROOT/'examples/build-todo-app.sh',dest)
            output = run(sys.executable,'-m','unittest','-q',cwd=dest)
            run(dest/'scripts/lint.sh',cwd=dest)
            require((dest/'.git').is_dir(),'example is not its own repository')
            require(not run('git','remote',cwd=dest).strip(),'example has a remote')
            return 'fresh nested repo: 7 unit tests and persistence boundary lint pass'
        check('example app',example)

        def examples():
            file = ROOT/'logs/examples/summary.json'
            require(file.is_file(),'missing summary; run scripts/run-examples.sh')
            summary = json.loads(file.read_text())
            require(set(summary)==EXPECTED,'summary must include exactly the eight skills')
            bad = []
            for name in sorted(EXPECTED):
                entry = summary[name]
                if entry.get('status')!='pass' or not entry.get('checks') or any(str(c).startswith('FAIL:') for c in entry.get('checks',[])):
                    bad.append(name)
                require((ROOT/f'logs/examples/{name}.log').is_file(),f'missing {name} execution log')
            require(not bad,'real example checks did not pass: '+', '.join(bad))
            return 'all 8 skills have passing, nonempty assertion records and logs'
        check('example skill results',examples)

    print('| Check | Result | Detail |')
    print('|---|---|---|')
    for name,status,detail in rows:
        print(f'| {name} | {status} | {str(detail).replace(chr(10), " ").replace("|", "/")} |')
    print(f'\n{len(rows)-len(failures)} PASS; {len(failures)} FAIL')
    return int(bool(failures))


if __name__=='__main__':
    raise SystemExit(main())
