#!/usr/bin/env python3
"""Exercise all eight skills serially against a freshly built, isolated todo repo."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())


class Examples:
    def __init__(self, keep=False):
        self.keep = keep
        self.scratch = Path(tempfile.mkdtemp(prefix="poteto-examples-", dir=(os.environ.get("TMPDIR") or "/tmp")))
        self.repo = self.scratch / "todo-app"
        self.logs = ROOT / "logs/examples"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.summary = {name: {"status": "fail", "checks": []} for name in SKILLS}
        self.log = None
        self.name = None
        self.skill = self.repo / ".claude/skills/verify-todo"
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LC_ALL="C", TZ="UTC",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

    def ensure(self, condition, description):
        if not condition:
            raise AssertionError(description)
        self.summary[self.name]["checks"].append(description)
        self.log.write(f"ASSERT PASS: {description}\n")
        self.log.flush()

    def run(self, *argv, expected=0, cwd=None, env=None, timeout=90, json_stdout=False):
        args = [str(a) for a in argv]
        self.log.write("$ " + shlex.join(args) + "\n")
        self.log.flush()
        result = subprocess.run(args, cwd=cwd or self.repo, env=env or self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE if json_stdout else subprocess.STDOUT,
                                text=True, timeout=timeout)
        self.log.write(result.stdout + (result.stderr or '') + f"\n[exit {result.returncode}; expected {expected}]\n")
        self.log.flush()
        self.last_output = result.stdout
        self.last_returncode = result.returncode
        accepted = expected if isinstance(expected, tuple) else (expected,)
        if result.returncode not in accepted:
            raise AssertionError(f"{shlex.join(args)}: exit {result.returncode}, expected {expected}; see {self.name}.log")
        return result.stdout

    def json(self, *argv, **kwargs):
        return json.loads(self.run(*argv, json_stdout=True, **kwargs))

    def script(self, skill, file):
        return ROOT / "skills" / skill / "scripts" / file

    def save(self, name, value):
        path = self.art / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n")
        return path

    def copy(self, source, name=None):
        dest = self.art / (name or source.name)
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, dest)
        return dest

    @contextmanager
    def case(self, name):
        self.name = name
        self.art = self.logs / "artifacts" / name
        if self.art.exists():
            shutil.rmtree(self.art)
        self.art.mkdir(parents=True)
        with (self.logs / f"{name}.log").open("w") as self.log:
            self.log.write(f"scratch: {self.scratch}\n")
            try:
                yield
            except Exception as error:
                traceback.print_exc(file=self.log)
                (self.art / "failure.txt").write_text(traceback.format_exc() + "\nLast command output:\n" + getattr(self,"last_output",""))
                self.summary[name]["checks"].append(f"FAIL: {error}")
                print(f"FAIL {name}: {error}", flush=True)
            else:
                self.summary[name]["status"] = "pass"
                print(f"PASS {name}: {len(self.summary[name]['checks'])} assertions", flush=True)
            finally:
                (self.logs / "summary.json").write_text(json.dumps(self.summary, indent=2) + "\n")

    def create(self):
        with self.case("create-verification-skill"):
            built = self.json(ROOT / "examples/build-todo-app.sh", self.repo, cwd=ROOT)
            self.built = built
            self.save("history.json", built)
            rebuilt = self.json(ROOT / "examples/build-todo-app.sh", self.scratch / "repro", cwd=ROOT)
            self.ensure(built['head'] == rebuilt['head'], "two independent builds reproduce identical git HEAD")
            self.run(sys.executable, "-m", "unittest", "-q")
            self.ensure(len(self.run("git", "log", "--format=%H").splitlines()) == 10, "nested repo has ten dated commits")
            for sha in built["direct_write_commits"]:
                diff = self.run("git", "show", sha, "--format=", "--", "todo")
                self.ensure('+            store.path.write_text' in diff or '+                store.path.write_text' in diff,
                            f"real direct-write mistake retained at {sha[:12]}")
            inv = self.json(self.script(self.name, "scan_repo.py"), self.repo, "--json")
            self.save("scan.json", inv)
            routes = {(r['method'], r['path']) for r in inv['http_routes']}
            self.ensure(routes == {('GET','/health'),('GET','/todos'),('POST','/todos'),
                                   ('POST','/todos/<id>/done'),('DELETE','/todos/<id>')}, "scan detects all five HTTP routes")
            self.ensure({'add','list','done','rm','serve'} <= {c['name'] for c in inv['cli_commands']}, "scan detects CLI and serve subcommands")
            scaffold = self.json(self.script(self.name, "scaffold.py"), self.repo, "--app", "todo", "--json")
            self.ensure(scaffold['todo_markers'] > 0, "scaffold leaves unknown application facts explicit")
            self.run(self.script(self.name, "check_verify_skill.py"), self.skill, expected=1)
            self.run(ROOT / "examples/fill_verify.py", self.skill)
            self.run(self.script(self.name, "check_verify_skill.py"), self.skill)
            self.ensure(not any('TODO(verify)' in p.read_text() for p in self.skill.rglob('*.md')), "all scaffold questions replaced by real todo recipes")
            # Running a Python helper must not make bytecode count as an unexecutable script.
            helper = self.skill / 'scripts/check.py'
            helper.write_text('#!/usr/bin/env python3\nprint("check")\n')
            helper.chmod(0o755)
            skill_text = (self.skill / 'SKILL.md').read_text()
            (self.skill / 'SKILL.md').write_text(skill_text + '\nRun `scripts/check.py` for the example helper.\n')
            py_compile.compile(str(helper), doraise=True)
            self.run(self.script(self.name, 'check_verify_skill.py'), self.skill)
            self.ensure(True, 'generated bytecode does not invalidate the skill contract')
            helper.unlink()
            shutil.rmtree(self.skill / 'scripts/__pycache__')
            (self.skill / 'SKILL.md').write_text(skill_text)
            self.copy(self.skill, 'verify-todo')
            # Contract gate must reject concrete structural damage.
            feature = self.skill / 'features/create-todo.md'
            original = feature.read_text()
            try:
                feature.write_text(original.replace('## Gotchas', '## Wrong heading'))
                self.run(self.script(self.name, 'check_verify_skill.py'), self.skill, expected=1)
            finally:
                feature.write_text(original)
            self.ensure(True, 'contract rejects damaged feature heading order')
            self.run(self.script(self.name, 'check_verify_skill.py'), self.skill, '--require-proof',
                     self.repo / 'artifacts/verify', expected=1)
            self.ensure(True, 'contract with --require-proof fails before any proof run')
            self.drive_contract_regressions()
            self.live_pass('initial')
            self.run(self.script(self.name, 'check_verify_skill.py'), self.skill, '--require-proof', self.repo / 'artifacts/verify')
            self.ensure(True, 'contract with --require-proof passes once a proof run left drive records')
            self.drive_result_regressions()
            self.wrapped_cleanup()
            self.grok_trial_regressions()

    def drive_contract_regressions(self):
        gate = self.script(self.name, 'check_verify_skill.py')
        feature = self.skill / 'features/create-todo.md'
        original = feature.read_text()
        prefix, rest = original.split('## Driving it with control.sh\n', 1)
        _, suffix = rest.split('## Gotchas', 1)
        control = '.claude/skills/verify-todo/scripts/control.sh'
        def recipe(lines):
            feature.write_text(prefix + '## Driving it with control.sh\n\n' + '\n'.join(f'- `{control} drive {line}`' for line in lines)
                               + '\n\n## Gotchas' + suffix)
        try:
            recipe(['--feature create-todo http POST /todos \'{"title":"verify HTTP"}\' --expect 201',
                    '--feature create-todo cli -- python3 -m todo add \'verify CLI\' --data {DATA_DIR}',
                    '--feature create-todo http GET /todos --expect 200'])
            result = self.run(gate, self.skill, expected=1)
            self.ensure('add --expect-json' in result, 'old status-only create-todo recipe fails the assertion gate')
            write = '--feature create-todo --write added http POST /todos \'{"title":"verify HTTP"}\' --expect 201 --expect-json id=1'
            recipe([write])
            self.ensure('later asserted --read-back' in self.run(gate, self.skill, expected=1),
                        'an asserted POST still requires a second view')
            recipe([write, '--feature create-todo --read-back added http GET /health --expect-json ok=true'])
            self.ensure('must observe the written resource' in self.run(gate, self.skill, expected=1),
                        'an unrelated asserted health read cannot prove a todo write')
            recipe(['--feature create-todo cli --write added --expect-json id=1 -- python3 -m todo add x --data {DATA_DIR}'])
            self.ensure('later asserted --read-back' in self.run(gate, self.skill, expected=1),
                        'CLI writes require an asserted read-back too')
            recipe(['--feature create-todo cli --read-only --expect-text \'<title>todos</title>\' -- python3 -m todo list --data {DATA_DIR}'])
            self.run(gate, self.skill)
            self.ensure(True, 'HTML in literal text assertions is not an unfilled recipe placeholder')
            recipe(['--feature create-todo cli --read-only --expect-no-text missing -- python3 -m todo list --data <dir>'])
            self.ensure('<dir>' in self.run(gate, self.skill, expected=1), 'application argument placeholders still fail after CLI --')
        finally:
            feature.write_text(original)
        control_path = self.skill / 'scripts/control.sh'
        original_control = control_path.read_text()
        try:
            control_path.write_text(original_control.replace('--expect-json', '--old-json'))
            self.ensure('regenerate control.sh' in self.run(gate, self.skill, expected=1),
                        'legacy harness without assertion support gets a regeneration hint')
        finally:
            control_path.write_text(original_control)
        worked = ROOT / 'skills/create-verification-skill/references/feature-example.md'
        module = load_module(gate, 'drive_contract')
        drives = module.recipe_drives(worked.read_text())
        self.ensure(drives and not module.second_view_errors(drives) and not module.recipe_placeholders(worked.read_text()),
                    'worked feature example has assertions and paired HTTP/CLI read-backs')

    def drive_result_regressions(self):
        control = self.skill / 'scripts/control.sh'
        gate = self.script(self.name, 'check_verify_skill.py')
        root = self.scratch / 'assertion-evidence'
        env = dict(self.env, VERIFY_STATE_DIR=str(self.scratch / 'assertion-state'),
                   VERIFY_ARTIFACTS_DIR=str(root), VERIFY_RUN_ID='assertions')
        def record():
            return json.loads(sorted((root / 'assertions').glob('[0-9]*.json'))[-1].read_text())
        try:
            self.json(control, 'launch', env=env)
            self.run(control, 'drive', '--expect-json', '.=[]', 'http', 'GET', '/todos', env=env)
            good = record()
            self.ensure(good['ok'] and good['assertions'][0]['got'] == [], 'root JSON path asserts an empty stored list')
            self.run(control, 'drive', 'http', 'GET', '/todos', '--expect', '201', '--expect-json', '.=[]', env=env, expected=1)
            wrong_status = record()
            self.ensure(wrong_status['assertions'][0]['ok'] and not wrong_status['execution_ok'] and not wrong_status['ok'],
                        'matching response JSON cannot override a wrong HTTP status')
            self.run(control, 'drive', 'http', 'GET', '/health', '--expect-json', 'missing=null', env=env, expected=1)
            self.ensure(record()['assertions'][0]['got'] == {'missing': True}, 'missing JSON path is distinct from null')
            for expression in ('ok=1', 'ok="true"'):
                self.run(control, 'drive', 'http', 'GET', '/health', '--expect-json', expression, env=env, expected=1)
                self.ensure(not record()['ok'], f'JSON assertion distinguishes true from {expression.split("=", 1)[1]}')
            self.run(control, 'drive', '--expect-text', '<title>todos</title>', 'cli', '--read-only',
                     '--expect-no-text', 'Discard me', '--', sys.executable, '-c',
                     'print("<title>todos</title>")', '--expect-json', 'application-argument', env=env)
            self.ensure(record()['argv'][-2:] == ['--expect-json', 'application-argument'],
                        'CLI -- preserves application flags and arguments verbatim')
            self.run(control, 'drive', 'cli', '--read-only', '--expect-json', 'value=null', '--expect-json', 'items.0=true',
                     '--', sys.executable, '-c', 'print(\'{"value":null,"items":[true]}\')', env=env)
            self.ensure(record()['ok'], 'existing null and nested array paths assert successfully on CLI JSON')
            self.run(control, 'drive', 'cli', '--read-only', '--expect-text', 'missing', '--', sys.executable, '-c',
                     'print("present")', env=env, expected=1)
            self.ensure(not record()['ok'] and record()['execution_ok'], 'text mismatch fails even with CLI exit zero')
            self.run(control, 'drive', 'cli', '--read-only', '--expect-no-text', 'present', '--', sys.executable, '-c',
                     'print("present")', env=env, expected=1)
            self.ensure(not record()['ok'], 'absence assertion fails when stdout contains the forbidden text')
            self.run(control, 'drive', 'cli', '--read-only', '--expect-json', 'x=1', '--', sys.executable, '-c',
                     'print("not JSON")', env=env, expected=1)
            self.ensure(not record()['assertions'][0]['ok'], 'JSON assertions reject non-JSON stdout')
        finally:
            self.run(control, 'cleanup', env=env)
        # Isolated manifests ensure an older passing run cannot hide bad evidence.
        proof_root = self.scratch / 'proof-records'
        run_dir = proof_root / 'case'
        run_dir.mkdir(parents=True)
        path = run_dir / '001-adhoc-http.json'
        (run_dir / 'manifest.json').write_text(json.dumps({'files': [{'path': path.name}]}))
        self.run(gate, self.skill, '--require-proof', proof_root, expected=1)
        self.ensure(True, 'manifest pointing to a nonexistent record cannot count as proof')
        variants = [
            ('assertion-free evidence', dict(good, assertions=[])),
            ('failed assertion evidence', dict(good, assertions=[dict(good['assertions'][0], ok=False)])),
            ('failed overall evidence', dict(good, ok=False)),
            ('wrong status with passing assertions', wrong_status),
            ('wrong status with forged overall flags', dict(wrong_status, ok=True, execution_ok=True)),
        ]
        for label, bad in variants:
            path.write_text(json.dumps(bad))
            self.run(gate, self.skill, '--require-proof', proof_root, expected=1)
            self.ensure(True, f'proof gate rejects {label}')
        path.write_text('{invalid')
        self.run(gate, self.skill, '--require-proof', proof_root, expected=1)
        self.ensure(True, 'malformed proof record fails without crashing')
        path.write_text(json.dumps(good))
        self.run(gate, self.skill, '--require-proof', proof_root)
        self.ensure(True, 'proof gate accepts actual successful asserted read evidence')
        # Break the real persistence code; the success response still contains the new row.
        store = self.repo / 'todo/store.py'
        original = store.read_text()
        broken_root = self.scratch / 'lost-write-evidence'
        broken_env = dict(env, VERIFY_RUN_ID='lost-write', VERIFY_ARTIFACTS_DIR=str(broken_root))
        try:
            self.ensure('os.replace(name, self.path)' in original, 'scratch mutant targets the actual persistence boundary')
            store.write_text(original.replace('os.replace(name, self.path)', 'pass  # skip the durable write'))
            self.json(control, 'launch', env=broken_env)
            self.run(control, 'drive', '--feature', 'create-todo', '--write', 'lost', 'http', 'POST', '/todos',
                     '{"title":"verify HTTP"}', '--expect', '201', '--expect-json', 'title=verify HTTP', env=broken_env)
            self.ensure(True, 'skipped-replace mutant still returns 201 with the expected response title')
            self.run(control, 'drive', '--feature', 'create-todo', '--read-back', 'lost', 'http', 'GET', '/todos',
                     '--expect-json', '0.title=verify HTTP', env=broken_env, expected=1)
            self.ensure(True, 'asserted GET second view catches the skipped-replace persistence bug')
            self.run(control, 'evidence', '--copy-data', env=broken_env)
            self.run(gate, self.skill, '--require-proof', broken_root, expected=1)
            self.ensure(True, 'a successful write response and failed stored read-back cannot count as proof')
        finally:
            try:
                self.run(control, 'cleanup', env=broken_env)
            finally:
                store.write_text(original)
        prove_env = dict(env, VERIFY_RUN_ID='prove-read', VERIFY_ARTIFACTS_DIR=str(self.scratch / 'prove-evidence'))
        self.run(control, 'prove', '--feature', 'list-todos', 'http', 'GET', '/todos', '--expect-json', '.=[]', env=prove_env)
        self.run(gate, self.skill, '--require-proof', self.scratch / 'prove-evidence')
        self.ensure(True, 'read-only prove shortcut keeps passing asserted evidence through cleanup')
        self.run(control, 'prove', 'http', 'GET', '/todos', env=dict(prove_env, VERIFY_RUN_ID='prove-unasserted'), expected=1)
        self.ensure(True, 'prove rejects an unasserted read and still cleans up')
        self.run(control, 'prove', '--write', 'mutation', 'http', 'POST', '/todos', '{"title":"x"}',
                 '--expect', '201', '--expect-json', 'id=1', env=dict(prove_env, VERIFY_RUN_ID='prove-write'), expected=1)
        self.ensure(True, 'prove rejects mutation-only shortcuts and still cleans up')

    def grok_trial_regressions(self):
        """Regressions from the Grok CLI trial on a Rust repo (logs/grok-trial/)."""
        fmap = self.skill / 'FEATURE_MAP.md'
        self.ensure(fmap.is_file() and all((self.skill / link).is_file() for link in
                    re.findall(r'\]\(([^)]+\.md)\)', fmap.read_text())), 'scaffold writes FEATURE_MAP.md whose links resolve')
        helpers = [line for line in (self.skill / 'SKILL.md').read_text().splitlines() if 'check_verify_skill.py' in line]
        self.ensure(helpers and not any('~/.claude' in line for line in helpers), 'generated helper paths do not assume ~/.claude')
        rust = self.scratch / 'rust-cli'
        (rust / 'src').mkdir(parents=True)
        (rust / 'Cargo.toml').write_text('[package]\nname = "demo-cli"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo"\npath = "src/main.rs"\n')
        (rust / 'src/main.rs').write_text(
            'use clap::{Parser, Subcommand};\n#[derive(Parser)]\nstruct Cli { #[command(subcommand)] command: Command }\n'
            '#[derive(Subcommand)]\nenum Command {\n    /// list adapters\n    Adapters {\n        #[arg(long)]\n        json: bool,\n    },\n'
            '    ExportWeb,\n    #[command(name = "doctor-x")]\n    Doc,\n}\nfn main() {}\n')
        inv = self.json(self.script(self.name, 'scan_repo.py'), rust, '--json', cwd=rust)
        names = {c['name'] for c in inv['cli_commands']}
        self.ensure(names == {'adapters', 'export-web', 'doctor-x'}, 'scan detects clap Subcommand variants in a Rust CLI')
        self.ensure(all(c['prog'] == 'cargo run -q -p demo-cli --bin demo --' for c in inv['cli_commands']),
                    'Rust CLI is invoked through cargo run with its package and bin')
        self.ensure('Run `demo adapters`' in {f['title'] for f in inv['features']}, 'resource-less CLI features are named after the command')
        control = self.skill / 'scripts/control.sh'
        state = self.scratch / 'verify-state-symlink'
        env = dict(self.env, VERIFY_STATE_DIR=str(state), VERIFY_RUN_ID='symlink')
        try:
            launch = self.json(control, 'launch', env=env)
            data = Path(launch['data_dir'])
            (data / 'site').mkdir()
            (data / 'site/index.html').write_text('<h1>x</h1>')
            os.symlink('site', data / 'web')
            os.symlink('../outside-the-snapshot', data / 'dangling')
            self.run(control, 'evidence', '--copy-data', env=env)
            manifest = json.loads((self.repo / 'artifacts/verify/symlink/manifest.json').read_text())
            links = {f['path']: f.get('symlink') for f in manifest['files'] if 'symlink' in f}
            self.ensure(any(p.endswith('dangling') for p in links), 'evidence --copy-data records a dangling symlink instead of crashing')
        finally:
            self.run(control, 'cleanup', env=env)

    def wrapped_cleanup(self):
        # A wrapper launch command (sh -c, npm, make) forks the real server; cleanup must stop that child too.
        wrapped = self.repo / '.claude/skills/verify-wrapped'
        shutil.copytree(self.skill, wrapped)
        control = wrapped / 'scripts/control.sh'
        text, n = re.subn(r"^LAUNCH_CMD='(.+)'$", r"LAUNCH_CMD='\1; echo server exited'", control.read_text(), flags=re.M)
        self.ensure(n == 1, 'wrapper variant reuses the generated launch command')
        control.write_text(text)
        env = dict(self.env, VERIFY_STATE_DIR=str(self.scratch / 'verify-state-wrapped'),
                   VERIFY_ARTIFACTS_DIR=str(self.scratch / 'evidence-wrapped'))
        launch, closed = None, False
        try:
            launch = self.json(control, 'launch', env=env)
            self.ensure(self.run('ps', '-o', 'pid=', '--ppid', launch['pid']).split(), 'wrapper launch runs the server as a child of the recorded PID')
            self.ensure(self.json(control, 'cleanup', env=env)['killed'], 'cleanup stops the recorded wrapper')
            for _ in range(30):
                with socket.socket() as s:
                    s.settimeout(0.2)
                    closed = s.connect_ex(('127.0.0.1', launch['port'])) != 0
                if closed:
                    break
                time.sleep(0.1)
            self.ensure(closed, 'cleanup also stops the server child of a wrapper launch command')
        finally:
            if launch and not closed:  # leaked: stop only the process group this launch created
                try:
                    os.killpg(launch['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.run(control, 'cleanup', env=env)
            shutil.rmtree(wrapped)

    def live_pass(self, label):
        control = self.skill / 'scripts/control.sh'
        state = self.scratch / f'verify-state-{label}'
        env = dict(self.env, VERIFY_STATE_DIR=str(state), VERIFY_RUN_ID=label)
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], env=self.env)
        launch = None
        try:
            launch = self.json(control, 'launch', env=env)
            self.ensure(launch['pid'] > 1 and launch['pid'] != sentinel.pid, 'launch records a distinct owned server PID on a free port')
            self.ensure(not list(Path(launch['data_dir']).iterdir()), 'launch starts with an empty data directory')
            doctor = self.json(control, 'doctor', env=env)
            self.ensure(doctor['ok'] and all(c['ok'] for c in doctor['checks']), 'doctor checks owned process, health and read route')
            def drive(feature, *args):
                self.run(control, 'drive', '--feature', feature, *args, env=env)
            drive('health', '--expect-json', 'ok=true', 'http', 'GET', '/health', '--expect', '200')
            drive('create-todo', '--write', 'create-http', 'http', 'POST', '/todos', '{"title":"verify HTTP"}',
                  '--expect', '201', '--expect-json', 'id=1', '--expect-json', 'status=open')
            drive('create-todo', '--read-back', 'create-http', 'http', 'GET', '/todos',
                  '--expect-json', '0.title=verify HTTP', '--expect-json', '0.status=open')
            drive('create-todo', 'cli', '--write', 'create-cli', '--expect-json', 'id=2', '--expect-json', 'status=open',
                  '--', sys.executable, '-m', 'todo', 'add', 'verify CLI', '--data', '{DATA_DIR}')
            drive('create-todo', 'cli', '--read-back', 'create-cli', '--expect-json', '1.title=verify CLI',
                  '--expect-json', '1.status=open', '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('list-todos', 'http', 'GET', '/todos', '--expect', '200', '--expect-json', '0.id=1', '--expect-json', '1.id=2')
            drive('list-todos', 'cli', '--read-only', '--expect-json', '1.id=2',
                  '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('delete-todo', '--write', 'reject-http', 'http', 'DELETE', '/todos/1',
                  '--expect', '409', '--expect-json', 'error=cannot move open to archived')
            drive('delete-todo', '--read-back', 'reject-http', 'http', 'GET', '/todos', '--expect-json', '0.status=open')
            drive('delete-todo', 'cli', '--write', 'reject-cli', '--expect-exit', '1', '--expect-no-text', 'archived',
                  '--', sys.executable, '-m', 'todo', 'rm', '2', '--data', '{DATA_DIR}')
            drive('delete-todo', 'cli', '--read-back', 'reject-cli', '--expect-json', '1.status=open',
                  '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('done-todo', '--write', 'done-http', 'http', 'POST', '/todos/1/done',
                  '--expect', '200', '--expect-json', 'status=done')
            drive('done-todo', '--read-back', 'done-http', 'http', 'GET', '/todos', '--expect-json', '0.status=done')
            drive('done-todo', '--write', 'retry-http', 'http', 'POST', '/todos/1/done',
                  '--expect', '409', '--expect-text', 'cannot move done to done')
            drive('done-todo', '--read-back', 'retry-http', 'http', 'GET', '/todos', '--expect-json', '0.status=done')
            drive('done-todo', 'cli', '--write', 'done-cli', '--expect-json', 'status=done',
                  '--', sys.executable, '-m', 'todo', 'done', '2', '--data', '{DATA_DIR}')
            drive('done-todo', 'cli', '--read-back', 'done-cli', '--expect-json', '1.status=done',
                  '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('delete-todo', '--write', 'archive-http', 'http', 'DELETE', '/todos/1',
                  '--expect', '200', '--expect-json', 'status=archived')
            drive('delete-todo', '--read-back', 'archive-http', 'http', 'GET', '/todos', '--expect-json', '0.status=archived')
            drive('delete-todo', 'cli', '--write', 'archive-cli', '--expect-json', 'status=archived',
                  '--', sys.executable, '-m', 'todo', 'rm', '2', '--data', '{DATA_DIR}')
            drive('delete-todo', 'cli', '--read-back', 'archive-cli', '--expect-json', '1.status=archived',
                  '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('list-todos', 'http', 'GET', '/todos', '--expect', '200',
                  '--expect-json', '0.status=archived', '--expect-json', '1.status=archived')
            evidence = self.json(control, 'evidence', '--copy-data', env=env)
            evdir = Path(launch['evidence_dir'])
            records = [json.loads(p.read_text()) for p in sorted(evdir.glob('[0-9]*.json'))]
            self.ensure(all(r['ok'] and r['execution_ok'] and r['assertions'] and all(a['ok'] for a in r['assertions'])
                            for r in records), 'every live drive records execution success and passing state assertions')
            self.ensure(any(r.get('expect_exit') == 1 and r['exit_code'] == 1 and r['ok'] for r in records),
                        'expected CLI rejection (rm 2 on an open todo) exits 0 from the harness')
            http = [r for r in records if r['kind'] == 'http']
            created = next(r for r in http if r['method'] == 'POST' and r['url'].endswith('/todos'))
            self.ensure(created['request_body'] == {'title':'verify HTTP'} and created['response_body'] ==
                        {'id':1,'title':'verify HTTP','status':'open'}, 'POST evidence records the real request and created task')
            final_rows = http[-1]['response_body']
            self.ensure([r['status'] for r in final_rows] == ['archived','archived'], 'HTTP and CLI complete and archive both real records')
            snapshots = list(evdir.glob('*-data-snapshot/todos.json'))
            self.ensure(len(snapshots) == 1 and json.loads(snapshots[0].read_text()) == final_rows,
                        'stored JSON snapshot matches the API read-back')
            manifest = json.loads(Path(evidence['manifest']).read_text())
            self.ensure(all(hashlib.sha256((evdir / item['path']).read_bytes()).hexdigest() == item['sha256']
                            for item in manifest['files']), 'every evidence manifest digest matches its actual file')
            stopped = self.json(control, 'cleanup', env=env)
            self.ensure(stopped['killed'] and not state.joinpath('current').exists(), 'cleanup stops its recorded server and clears run state')
            # kill(0) also sees zombies; a stopped zombie cannot serve or execute.
            stat = Path(f"/proc/{launch['pid']}/stat")
            self.ensure(not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0] == 'Z', 'owned server no longer runs after cleanup')
            self.ensure(sentinel.poll() is None, 'cleanup leaves an unrelated process alive')
            self.ensure((evdir / 'manifest.json').exists(), 'cleanup preserves evidence files')
            self.copy(evdir, f'evidence-{label}')
        finally:
            try:
                self.run(control, 'cleanup', env=env)
            finally:
                sentinel.terminate()
                sentinel.wait(timeout=5)

    def maintain(self):
        with self.case('maintain-verification-skill'):
            command = [self.script(self.name, 'drift_check.py'), self.skill, '--repo', self.repo, '--json']
            clean = self.json(*command)
            self.save('clean.json', clean)
            self.ensure(not clean['drift'], 'complete good feature map has no drift')
            feature = self.skill / 'features/done-todo.md'
            original = feature.read_text()
            try:
                feature.write_text('\n'.join(line for line in original.splitlines()
                                             if '/todos/<id>/done' not in line and '/todos/1/done' not in line) + '\n')
                drift = self.json(*command, expected=1)
                self.ensure(any(r['path'] == '/todos/<id>/done' for r in drift['routes']['missing']), 'deleting a route from the map reports it missing')
                self.save('deleted-map-route.json', drift)
            finally:
                feature.write_text(original)
            api = self.repo / 'todo/api.py'
            original = api.read_text()
            try:
                api.write_text(original.replace('if path == "/health":', 'if path == "/metrics":\n            self.reply(200, {"count": 1})\n        elif path == "/health":'))
                drift = self.json(*command, expected=1)
                self.ensure(any(r['path'] == '/metrics' for r in drift['routes']['missing']), 'adding an application route reports it unmapped')
                self.save('new-app-route.json', drift)
                api.write_text('"""All HTTP routes removed."""\n')
                drift = self.json(*command, expected=1)
                self.ensure(bool(drift['routes']['dead']), 'removing the last source route still reports dead mapped routes')
            finally:
                api.write_text(original)
            cli = self.repo / 'todo/__main__.py'
            original = cli.read_text()
            try:
                cli.write_text('"""CLI removed."""\n')
                drift = self.json(*command, expected=1)
                self.ensure(any(r['name'] == 'add' for r in drift['commands']['dead']), 'removing the entire CLI still reports dead mapped commands')
            finally:
                cli.write_text(original)
            feature = self.skill / 'features/list-todos.md'
            original = feature.read_text()
            try:
                feature.write_text(original + '- Run the unit tests first: `python3 -m unittest discover -s tests`.\n')
                self.ensure(not self.json(*command)['drift'], 'an incidental python command in a recipe is not a dead app command')
            finally:
                feature.write_text(original)
            self.maintain_scan_regressions()
            self.maintain_path_comparisons()
            self.review_phantom_routes()
            self.review_path_rebindings()
            self.review_path_branches()
            self.review_go_receivers()
            self.maintain_zero_nargs()
            self.maintain_exit_contract()
            self.run(*command)
            self.live_pass('maintain')

    def maintain_scan_regressions(self):
        """Routes named like filesystem roots (/home) and store_true flags before the subcommand (curator 2026-11-09)."""
        command = [self.script(self.name, 'drift_check.py'), self.skill, '--repo', self.repo, '--json']
        api, listing = self.repo / 'todo/api.py', self.skill / 'features/list-todos.md'
        originals = {path: path.read_text() for path in (api, listing)}
        try:
            api.write_text(originals[api].replace('if path == "/health":', 'if path == "/home":\n'
                           '            self.server.store.export("/tmp/todo-export")\n'
                           '            self.reply(200, {"page": "home"})\n        elif path == "/health":'))
            drift = self.json(*command, expected=1)
            self.save('home-route.json', drift)
            missing = [(r['method'], r['path']) for r in drift['routes']['missing']]
            self.ensure(('GET', '/home') in missing, 'a GET /home branch is reported missing from the map, though /home is also a filesystem root')
            self.ensure(not any(path.startswith('/tmp') for _, path in missing), 'a /tmp path passed only to a generic call is still not a route')
            listing.write_text(originals[listing] + '- `GET /home` serves the home page.\n')
            mapped = self.json(*command, expected=0)
            self.ensure(not mapped['routes']['dead'] and not mapped['drift'], 'a feature file that maps GET /home is not reported dead')
        finally:
            for path, text in originals.items():
                path.write_text(text)
        cli, create = self.repo / 'todo/__main__.py', self.skill / 'features/create-todo.md'
        features = sorted((self.skill / 'features').glob('*.md'))
        originals = {path: path.read_text() for path in (cli, *features)}
        flag = 'parser.add_argument("--json", action="store_true")'
        try:
            cli.write_text(originals[cli].replace('    commands = parser.add_subparsers(', f'    {flag}\n    commands = parser.add_subparsers('))
            for path in features:
                path.write_text(originals[path].replace('python3 -m todo list', 'python3 -m todo --json list'))
            create.write_text(create.read_text() + '- `python3 -m todo --json add milk --data {DATA_DIR}` adds a todo titled milk.\n')
            texts = [path.read_text() for path in features]
            self.ensure(flag in cli.read_text() and not any('-m todo list' in t for t in texts) and any('-m todo --json list' in t for t in texts),
                        'scratch CLI declares a top-level store_true --json and every list recipe passes it before the subcommand')
            drift = self.json(*command, expected=0)
            self.save('store-true-flag.json', drift)
            self.ensure(not any(c['name'] == 'list' for c in drift['commands']['missing']),
                        'a store_true flag does not take the subcommand as its value: python3 -m todo --json list maps list')
            self.ensure(not any(c['name'] == 'milk' for c in drift['commands']['dead']),
                        'python3 -m todo --json add milk maps add instead of reporting milk as a dead command')
            self.ensure(not drift['drift'], 'the map stays clean when its recipes pass the store_true --json before the subcommand')
        finally:
            for path, text in originals.items():
                path.write_text(text)

    def maintain_path_comparisons(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'review_scan')
        source = '''import os, re
from http.server import BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path
        if data_dir == "/tmp/cache": pass
        if data_dir.startswith("/var/lib/"): pass
        if real.startswith("/var/www/"): pass
        if os.environ.get("HOME") == "/root": pass
        if re.match(r"^/Users/files$", real): pass
        match real:
            case "/etc/config": pass
        if self.path == "/Users": pass
        if "/home" == path: pass
        if request.path.startswith("/var/routes/"): pass
        if re.fullmatch(r"/root/routes", path): pass
        match self.path:
            case "/opt/routes": pass
'''
        routes = {(r['method'], r['path']) for r in scanner.analyze_python('server.py', source)['routes']}
        self.ensure(routes == {('GET', p) for p in ('/Users', '/home', '/var/routes/<*>', '/root/routes', '/opt/routes')},
                    'only request-path comparisons bypass filesystem filtering, including equality, prefix, regex and match/case')
        api = self.repo / 'todo/api.py'
        original = api.read_text()
        try:
            api.write_text(original.replace('        path = urlsplit(self.path).path',
                           '        data_dir = "/unused"\n'
                           '        if data_dir == "/tmp/cache": pass\n'
                           '        if data_dir.startswith("/var/www/"): pass\n'
                           '        path = urlsplit(self.path).path'))
            drift = self.json(self.script(self.name, 'drift_check.py'), self.skill, '--repo', self.repo, '--json')
            self.ensure(not drift['drift'], 'filesystem equality and startswith checks introduce no phantom routes or drift')
        finally:
            api.write_text(original)

    def maintain_zero_nargs(self):
        cli = self.repo / 'todo/__main__.py'
        features = sorted((self.skill / 'features').glob('*.md'))
        originals = {p: p.read_text() for p in (cli, *features)}
        try:
            action = ('import argparse\nclass Zero(argparse.Action):\n'
                      '    def __call__(self, parser, namespace, values, option_string=None):\n'
                      '        setattr(namespace, self.dest, True)\n')
            cli.write_text(action + originals[cli].replace('    commands = parser.add_subparsers(',
                           '    parser.register("action", "zero", Zero)\n'
                           '    parser.add_argument("--zero", action="zero", nargs=0, default=False)\n'
                           '    commands = parser.add_subparsers('))
            self.run(sys.executable, '-m', 'todo', '--zero', 'list', '--data', self.scratch / 'zero-data')
            scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'review_zero_scan')
            option = next(o for o in scanner.analyze_python('cli.py', cli.read_text())['parser_options']['parser']
                          if '--zero' in o['names'])
            self.ensure(option['takes_value'] is False, 'registered string argparse actions with explicit nargs=0 consume no value')
            for p in features:
                p.write_text(originals[p].replace('python3 -m todo list', 'python3 -m todo --zero list'))
            drift = self.json(self.script(self.name, 'drift_check.py'), self.skill, '--repo', self.repo, '--json')
            self.ensure(not drift['drift'], 'a registered nargs=0 action before list keeps its command mapped and exits 0')
        finally:
            for p, text in originals.items():
                p.write_text(text)

    def review_phantom_routes(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'recheck_scan')
        prefix = 'from http.server import BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n'
        negatives = [
            'path = self.translate_path(self.path)\nif path.startswith("/var/www/"): pass',
            'cmd = ["curl","-X","POST","-o","/tmp/out",url]',
            'if args[0] == "dev": pass',
            'if tokens[0] == "var": pass',
            'if folders == ["tmp", "cache"]: pass',
            'if path.startswith("/var/www/"): pass',
        ]
        unexpected = {}
        for source in negatives:
            body = prefix + '    def do_GET(self):\n' + ''.join('        ' + line + '\n' for line in source.splitlines())
            routes = scanner.analyze_python('server.py', body)['routes']
            if routes:
                unexpected[source] = routes
        # A request-path assignment in another function must not bless this function's bare name.
        body = (prefix + '    def do_POST(self):\n        path = self.path\n'
                '    def do_GET(self):\n        if path == "/var/files": pass\n')
        if routes := scanner.analyze_python('server.py', body)['routes']:
            unexpected['cross-function path'] = routes
        root = self.scratch / 'review-go-routes'
        root.mkdir()
        (root / 'main.go').write_text('package main\nfunc main() {\n'
                                      'kv.Put("/var/lib/data", v)\n'
                                      'r.Put("/var/routes", handler)\n'
                                      'router.GET("/Users", handler)\n}\n')
        go = self.json(self.script('create-verification-skill', 'scan_repo.py'), root, '--json')
        if any(r['path'] == '/var/lib/data' for r in go['http_routes']):
            unexpected['kv.Put("/var/lib/data", v)'] = go['http_routes']
        self.ensure(not unexpected, f'filesystem, curl, args, folders and non-router Go calls create no phantom routes: {unexpected}')
        self.ensure({(r['method'], r['path']) for r in go['http_routes']} == {('PUT', '/var/routes'), ('GET', '/Users')},
                    'known Go route receivers retain filesystem-like route names')
        body = (prefix + '    def do_GET(self):\n'
                '        path = urlsplit(self.path).path\n'
                '        if path.startswith("/var/routes/"): pass\n'
                '        parts = path.strip("/").split("/")\n'
                '        if parts == ["tmp", "cache"]: pass\n'
                '        if parts[0] == "dev": pass\n'
                '        path = self.translate_path(self.path)\n'
                '        if path.startswith("/var/www/"): pass\n')
        self.ensure({r['path'] for r in scanner.analyze_python('server.py', body)['routes']}
                    == {'/var/routes/<*>', '/tmp/cache', '/dev'},
                    'same-function request-path aliases and split segments keep real routes; reassignment revokes provenance')

    def review_path_rebindings(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'rebind_scan')
        prefix = 'from http.server import BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n'
        probe = 'if path.startswith("/var/www/"): pass'
        negatives = [
            'path, query = self.translate_path(path), ""\n' + probe,
            '[path, query] = [self.translate_path(path), ""]\n' + probe,
            '(query, [path, *rest]) = translated\n' + probe,
            'path, path = self.path, filesystem\n' + probe,
            'for path in files:\n    ' + probe,
            'for query, [path] in files:\n    ' + probe,
            'for path in files: pass\n' + probe,
            'async for path in files:\n    ' + probe,
            'with open_file() as path:\n    ' + probe,
            'with open_file() as (path, query):\n    ' + probe,
            'with open_file() as path: pass\n' + probe,
            'async with open_file() as path:\n    ' + probe,
            '(path := self.translate_path(path))\n' + probe,
            'if (path := self.translate_path(path)) and path.startswith("/var/www/"): pass',
            'path += suffix\n' + probe,
            'del path\n' + probe,
            'import filesystem as path\n' + probe,
            'from filesystem import root as path\n' + probe,
            'def path(): pass\n' + probe,
            'class path: pass\n' + probe,
            'try: read_file()\nexcept OSError as path:\n    ' + probe,
            'match filesystem:\n    case {"root": path}:\n        ' + probe,
            'checks = [path.startswith("/var/www/") for path in files]',
            'parts = path.strip("/").split("/")\nparts, query = folders, ""\n'
            'if parts == ["var", "www"]: pass',
        ]
        failures = []
        for source in negatives:
            body = prefix + '    async def do_GET(self):\n        path = self.path\n'
            body += ''.join('        ' + line + '\n' for line in source.splitlines())
            info = scanner.analyze_python('server.py', body)
            if info['routes'] or info['notes']:
                failures.append((source, info['routes'], info['notes']))
        positives = [
            'path = self.path',
            'path, query = self.path, ""',
            'path, path = filesystem, self.path',
            '[query, [path]] = ["", [urlsplit(self.path).path]]',
            '(path := self.path)',
            'path = self.path\npath: str',
            'path = self.path\nfor other in files: pass',
            'path = self.path\nwith open_file() as other: pass',
            'path = self.path\nchecks = [path for path in files]',
            'path = self.translate_path(self.path)\npath = self.path',
        ]
        for source in positives:
            body = prefix + '    def do_GET(self):\n'
            body += ''.join('        ' + line + '\n' for line in (source + '\n' + probe).splitlines())
            routes = {(r['method'], r['path']) for r in scanner.analyze_python('server.py', body)['routes']}
            if routes != {('GET', '/var/www/<*>')}:
                failures.append((source, sorted(routes)))
        self.ensure(not failures, f'rebindings revoke unknown path/segment sources and retain proven request aliases: {failures}')

    def review_baseline_retry(self):
        root = self.scratch / 'review-baseline-adoption'
        root.mkdir()
        self.run('git', 'init', '-q', cwd=root)
        rules, baseline = root / 'rules.json', root / 'baseline.json'
        rules.write_text(json.dumps({'rules': [
            {'id': 'probe', 'regex': 'BAD', 'include': ['*.py'], 'message': 'Use good() instead.'},
            {'id': 'clean', 'regex': 'OTHER', 'include': ['*.py'], 'message': 'Use good() instead.'}]}))
        (root / 'new.py').write_text('BAD\n')
        (root / 'tracked.py').write_text('BAD\n')
        self.run('git', 'add', 'tracked.py', cwd=root)
        command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules), '--root', str(root),
                   '--baseline', str(baseline), '--update-baseline', '--json', str(root)]
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
        self.log.write(result.stdout + result.stderr)
        counts = json.loads(baseline.read_text())['counts']
        self.ensure(result.returncode == 1 and 'probe' not in counts and counts.get('clean') == {}
                    and 'probe' in result.stderr and 'track' in result.stderr and 'rerun' in result.stderr,
                    'failed first adoption leaves no rule key, preserves other rules and explains how to retry')
        self.run('git', 'add', 'new.py', cwd=root)
        adopted = self.json(*command)
        self.ensure(adopted['ok'] and json.loads(baseline.read_text())['counts']['probe'] == {'new.py': 1, 'tracked.py': 1},
                    'tracking the hit after a failed first adoption lets the retry adopt all current counts')
        (root / 'new.py').write_text('BAD\nBAD\n')
        refused = self.json(*command, expected=1)
        self.ensure(refused['baseline']['refused'], 'successful retry still ratchets subsequent increases')

    def review_path_branches(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'branch_scan')
        prefix = 'class Handler:\n    def do_GET(self):\n'
        probe = 'if path.startswith("/var/www/"): pass'
        negatives = [
            'path = self.translate_path(self.path)\nif flag:\n    path = self.path\nelif path.startswith("/var/www/"): pass',
            'path = self.translate_path(self.path)\nif flag:\n    path = self.path\nelse:\n    ' + probe,
            'path = self.translate_path(self.path)\nif flag: pass\nelif other:\n    path = self.path\nelse:\n    ' + probe,
            'path = self.translate_path(self.path)\nif flag:\n    path = self.path\n' + probe,
            'path = self.path\nif flag:\n    path = filesystem\nelse:\n    path = self.path\n' + probe,
            'path = filesystem\nif flag:\n    if other:\n        path = self.path\n    ' + probe,
        ]
        positives = [
            'path = self.path\nif flag:\n    path = filesystem\nelif path.startswith("/var/www/"): pass',
            'path = self.path\nif flag:\n    path = filesystem\nelse:\n    ' + probe,
            'if flag:\n    path = self.path\n    ' + probe,
            'path = self.path\nif flag:\n    if other: pass\n    ' + probe,
            'if flag:\n    path = filesystem\npath = self.path\n' + probe,
        ]
        failures = []
        for expected, sources in ((set(), negatives), ({('GET', '/var/www/<*>')}, positives)):
            for source in sources:
                body = prefix + ''.join('        ' + line + '\n' for line in source.splitlines())
                info = scanner.analyze_python('server.py', body)
                routes = {(r['method'], r['path']) for r in info['routes']}
                if routes != expected or info['notes']:
                    failures.append((source, sorted(routes), info['notes']))
        self.ensure(not failures, f'exclusive branches cannot lend path provenance; uncertain joins revoke it: {failures}')

    def review_git_fail_closed(self):
        root = self.scratch / 'review-git-errors'
        root.mkdir()
        self.run('git', 'init', '-q', cwd=root)
        (root / 'tracked.py').write_text('BAD\n')
        (root / 'scratch.py').write_text('BAD\n')
        self.run('git', 'add', 'tracked.py', cwd=root)
        rules = self.save('rules.json', {'rules': [
            {'id': 'probe', 'regex': 'BAD', 'include': ['*.py'], 'message': 'Use good() instead.'}]})
        baseline = root / 'baseline.json'
        original = '{"counts": {}}\n'
        baseline.write_text(original)
        env = dict(self.env, GIT_DIR=str(root / 'missing.git'))
        failures = []
        for target in (root, root / 'tracked.py'):
            for explicit_root in ((), ('--root', str(root))):
                for mode in ((), ('--baseline', str(baseline), '--update-baseline')):
                    command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                               *explicit_root, *mode, str(target)]
                    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=10)
                    self.log.write(result.stdout + result.stderr)
                    if (result.returncode != 2 or 'git' not in result.stderr.lower()
                            or 'missing.git' not in result.stderr or 'Traceback' in result.stderr
                            or baseline.read_text() != original):
                        failures.append((command, result.returncode, result.stderr, baseline.read_text()))
                    baseline.write_text(original)
        self.ensure(not failures, f'git failure inside a worktree exits 2 without adopting any baseline hits: {failures}')
        outside = self.scratch / 'review-non-git'
        outside.mkdir()
        (outside / 'clean.py').write_text('GOOD\n')
        result = subprocess.run([str(self.script('correct', 'patlint.py')), '--rules', str(rules), str(outside)],
                                cwd=outside, env=self.env, capture_output=True, text=True, timeout=10)
        self.ensure(result.returncode == 0 and not result.stderr,
                    'ordinary non-git directories retain quiet filesystem fallback')

    def review_git_warning(self):
        import contextlib
        import io
        lint = load_module(self.script('correct', 'patlint.py'), 'recheck_patlint')
        root = self.scratch / 'review-git-fallback'
        root.mkdir()
        (root / 'hit.py').write_text('BAD\n')
        for error in (FileNotFoundError('git unavailable'),
                      subprocess.CompletedProcess(['git'], 128, b'', b'fatal: dubious ownership')):
            err = io.StringIO()
            kwargs = {'side_effect': error} if isinstance(error, Exception) else {'return_value': error}
            with patch.object(lint.subprocess, 'run', **kwargs), contextlib.redirect_stderr(err):
                try:
                    files = list(lint.walk([root], root))
                    top = lint.git_toplevel(root)
                except RuntimeError as failure:
                    self.ensure(isinstance(error, subprocess.CompletedProcess)
                                and 'dubious ownership' in str(failure) and 'refusing filesystem fallback' in str(failure),
                                'dubious ownership is a git error, never permission to traverse the disk')
                    continue
            self.ensure([f[0] for f in files] == ['hit.py'] and top is None
                        and 'WARNING' in err.getvalue() and 'git' in err.getvalue()
                        and 'fallback' in err.getvalue(),
                        f'git failure retains disk fallback but warns on stderr: {error}')

    def maintain_exit_contract(self):
        """Prove both clean-map regressions reject a checker that returns clean JSON with exit 1."""
        from unittest.mock import patch
        checker = str(self.script(self.name, 'drift_check.py'))
        run = subprocess.run
        for target in (0, 1):  # mapped /home; store_true recipes
            clean = 0
            def wrong_exit(*args, **kwargs):
                nonlocal clean
                result = run(*args, **kwargs)
                if args[0][0] == checker and not json.loads(result.stdout)['drift']:
                    if clean == target:
                        result.returncode = 1
                    clean += 1
                return result
            caught = None
            with patch.object(subprocess, 'run', wrong_exit):
                try:
                    self.maintain_scan_regressions()
                except AssertionError as error:
                    caught = str(error)
            self.ensure(caught is not None and 'exit 1, expected 0;' in caught,
                        f'clean drift regression {target + 1} rejects exit 1 even when its JSON reports no drift')

    def correct_grok_regressions(self):
        """mine_mistakes false positives seen on pixel-agent-garden (logs/grok-trial/)."""
        repo = self.scratch / 'mine-fixture'
        repo.mkdir()
        git = lambda *a: self.run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *a, cwd=repo)
        git('init', '-q')
        (repo / 'AGENTS.md').write_text('# Agents\n\n- Read CHANGELOG.md before you start work here.\n')
        steps = [('drawer.js', 'feat(web): add the data drawer', 'Also fix the toggle and the drawer focus.'),
                 ('drawer.js', 'feat(web): add the share drawer', 'Fixes drawer focus on open.'),
                 ('CHANGELOG.md', 'feat: layout pass, plus 3 layout-audit fixes', ''),
                 ('CHANGELOG.md', 'fix: layout overlap in the changelog renderer again', ''),
                 ('CHANGELOG.md', 'test: keep the layout from overlapping again', '')]
        for n, (path, subject, body) in enumerate(steps):
            (repo / path).write_text(f'v{n}\n')
            git('add', '-A')
            git('commit', '-qm', subject, *(['-m', body] if body else []))
        mined = self.json(self.script(self.name, 'mine_mistakes.py'), repo, '--json', cwd=repo)
        self.save('mine-fixture.json', mined)
        refs = {e.get('text', '').split('\n')[0] for c in mined['classes'] for e in c['evidence']}
        texts = ' '.join(json.dumps(c['evidence']) for c in mined['classes'])
        self.ensure('add the data drawer' not in texts and 'add the share drawer' not in texts,
                    'feat commits that mention "fix" only in the body are not counted as fixes')
        self.ensure('layout-audit fixes' in texts, 'a typed feat subject that names its fixes still counts')
        self.ensure('keep the layout from overlapping again' not in texts, 'a test: subject with a weak word ("again") is not a fix')
        self.ensure(not any(c['repeat'] for c in mined['classes']), 'an orientation bullet sharing a file name does not mark a class as repeat')

    def correct_git_file_set(self):
        """patlint lints what git would commit, so a leftover worktree or an ignored dir can't fail it or enter the baseline."""
        rules = ROOT / 'skills/correct/references/lint-rules.example.json'
        past = self.repo / '.claude/worktrees/past'
        gitignore, env, new = self.repo / '.gitignore', self.repo / 'env', self.repo / 'todo/new.py'
        ignored = gitignore.read_text()
        baseline, fresh = self.scratch / 'git-set-baseline.json', self.scratch / 'git-set-fresh.json'
        self.run('git', 'worktree', 'add', '--detach', past, self.built['direct_write_commits'][1])
        try:
            self.ensure(any(v['path'] == 'todo/api.py' for v in self.lint(rules, past / 'todo', root=past, expected=1)['violations']),
                        'a leftover worktree under .claude/worktrees/ holds the real past mistake, caught when linted as its own checkout')
            whole = self.lint(rules, self.repo)
            self.ensure(whole['ok'] and not any(v['path'].startswith('.claude/') for v in whole['violations']),
                        'linting the main checkout skips the leftover worktree (a nested checkout CI never sees) and passes')
            self.lint(rules, self.repo, extra=('--baseline', baseline, '--update-baseline'))
            self.ensure(not any(p.startswith('.claude/') for f in json.loads(baseline.read_text())['counts'].values() for p in f),
                        '--update-baseline writes no key for files inside a leftover worktree')
            env.mkdir()
            (env / 'x.py').write_text('store.path.write_text(payload)\n')
            gitignore.write_text(ignored + 'env/\n')
            self.ensure(self.lint(rules, self.repo)['ok'], 'a hit in a gitignored env/ directory stays quiet')
            output = self.run(self.script('correct', 'patlint.py'), '--rules', rules, '--root', self.repo,
                              '--json', env, expected=2)
            self.ensure('no files' in output and str(env) in output,
                        'an explicitly named ignored directory fails visibly instead of reporting a clean zero-file lint')
            new.write_text('store.path.write_text(payload)\n')
            hit = self.lint(rules, self.repo, expected=1)
            self.ensure([v['path'] for v in hit['failing']] == ['todo/new.py'] and 'todo/new.py' in hit['untracked'],
                        'a hit in an untracked, unignored todo/new.py still fails and is reported as untracked')
            adopted = self.lint(rules, self.repo, extra=('--baseline', fresh, '--update-baseline'), expected=1)
            self.ensure(not any('todo/new.py' in f for f in json.loads(fresh.read_text())['counts'].values())
                        and any(o['path'] == 'todo/new.py' for o in adopted['baseline']['over']),
                        'adopting a rule writes no count for an untracked file, whose hits keep failing')
        finally:
            new.unlink(missing_ok=True)
            shutil.rmtree(env, ignore_errors=True)
            gitignore.write_text(ignored)
            self.run('git', 'worktree', 'remove', '--force', past)
            if not any(past.parent.iterdir()):
                past.parent.rmdir()

    def lint(self, rules, path, *, root=None, expected=0, extra=()):
        return self.json(self.script('correct', 'patlint.py'), '--rules', rules, '--root', root or self.repo,
                         '--json', *extra, path, expected=expected)

    def correct_baseline_contract(self):
        rules = ROOT / 'skills/correct/references/lint-rules.example.json'
        root = self.scratch / 'ratchet-contract'
        (root / 'todo').mkdir(parents=True)
        self.run('git', 'init', '-q', cwd=root)
        file = root / 'todo/new.py'
        baseline, first = root / 'baseline.json', root / 'first.json'
        file.write_text('# clean\n')
        self.run('git', 'add', 'todo/new.py', cwd=root)
        self.lint(rules, root, root=root, extra=('--baseline', baseline, '--update-baseline'))
        file.write_text('store.path.write_text(payload)\n')
        existing = self.lint(rules, root, root=root, extra=('--baseline', baseline, '--update-baseline'), expected=1)
        self.ensure(existing['baseline']['refused'] and not existing['ok'],
                    'a tracked hit cannot raise an existing rule baseline, even after the file enters git')
        adopted = self.lint(rules, root, root=root, extra=('--baseline', first, '--update-baseline'))
        self.ensure(adopted['ok'] and json.loads(first.read_text())['counts']['no-direct-json-write']['todo/new.py'] == 1,
                    'tracked existing hits may enter a baseline only when the rule is first adopted')
        for path in (ROOT / 'skills/correct/SKILL.md', self.script('correct', 'patlint.py')):
            text = path.read_text()
            self.ensure('first adopted' in text and 'existing rule' in text,
                        f'{path.name} explains first adoption and the ratchet for existing rules')

    def correct(self):
        with self.case('correct'):
            mined = self.json(self.script(self.name, 'mine_mistakes.py'), self.repo, '--since', '2026-09-01', '--feedback',
                              self.repo / 'review-comments.json', self.repo / 'chat-feedback.md', '--json')
            self.save('mistakes.json', mined)
            bad_shas = set(self.built['direct_write_commits'])
            self.ensure(any(bad_shas <= {e['ref'] for e in c['evidence'] if e['source']=='commit'}
                            for c in mined['classes']), 'direct-JSON class contains both real mistake commit SHAs')
            self.ensure(any('status' in ' '.join(c['signature']).lower() and c['sources'].get('commit',0)>=2
                            for c in mined['classes']), 'status-transition class contains both fix commits')
            self.correct_grok_regressions()
            self.correct_baseline_contract()
            self.review_baseline_retry()
            self.review_git_warning()
            self.review_git_fail_closed()
            rules = self.save('rules.json', {'rules':[{'id':'no-direct-json-write','regex':r'\b\w+\.path\.write_text\s*\(',
                'include':['todo/*.py'], 'exclude':['todo/store.py'],
                'message':'Use Store.add or Store.transition in todo/store.py for atomic persistence; avoid json.dump.',
                'fix':[{'path':'todo/store.py','symbol':'Store.add'}]}]})
            patlint = self.script(self.name, 'patlint.py')
            recorded = self.json(patlint, '--rules', rules, '--root', self.repo, '--record', 'no-direct-json-write',
                                 '--commit', self.built['direct_write_fix'], '--json')
            saved = json.loads(rules.read_text())
            cases = saved['rules'][0]['tests']
            self.ensure(recorded['hits_at_mistake'] > 0 and recorded['hits_at_fixed'] == 0
                        and any(t['expect']=='no-direct-json-write' and 'write_text(json.dumps(rows))' in t['code']
                                and t['code'].startswith('                ') for t in cases),
                        '--record keeps the raw removed mistake after whole-file parent/fix proof')
            self.ensure(self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json')['ok'],
                        '--test passes the retained history cases and explicit Store.add fix')
            self.ensure(self.json(patlint, '--rules', ROOT/'skills/correct/references/lint-rules.example.json',
                                  '--root', self.repo, '--test', '--json')['ok'],
                        'both documented example rules replay successfully and name existing owner APIs')
            original_rules = rules.read_text()
            saved['rules'][0]['include'] = ['todo/__main__.py']
            rules.write_text(json.dumps(saved))
            missing = self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json', expected=1)
            self.ensure(any('missing match' in f for f in missing['failures']),
                        '--test detects missing matches when include no longer covers todo/api.py')
            rules.write_text(original_rules)
            store = self.repo / 'todo/store.py'
            store_source = store.read_text()
            try:
                store.write_text(store_source.replace('def add(', 'def create('))
                renamed = self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json', expected=1)
                self.ensure(any('Store.add: symbol missing' in f for f in renamed['failures']),
                            '--test rejects a renamed Store.add when the explicit fix reference stays unchanged')
            finally:
                store.write_text(store_source)
            saved = json.loads(original_rules)
            saved['rules'][0]['tests'].append({'expect':'ok', 'code':'# json.dump was the mistake', 'path':'todo/api.py'})
            rules.write_text(json.dumps(saved))
            self.ensure(self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json')['ok'],
                        'message and cases mentioning json.dump as the mistake pass with an explicit fix')
            del saved['rules'][0]['fix']
            rules.write_text(json.dumps(saved))
            self.ensure(self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json')['ok'],
                        'absent fix field never resolves names from the message')
            saved['rules'][0]['tests'] = [t for t in cases if t['expect']=='ok']
            rules.write_text(json.dumps(saved))
            empty = self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json', expected=1)
            self.ensure(any('missing ruleid test case' in f for f in empty['failures']),
                        '--test requires a ruleid case even when all ok cases pass')
            saved['rules'][0]['tests'] = cases + [{'expect':'ok', 'code':'store.path.write_text(payload)', 'path':'todo/api.py'}]
            rules.write_text(json.dumps(saved))
            noisy = self.json(patlint, '--rules', rules, '--root', self.repo, '--test', '--json', expected=1)
            self.ensure(any('noisy match' in f for f in noisy['failures']), '--test rejects a matching ok case')
            rules.write_text(original_rules)
            self.run(patlint, '--rules', rules, '--root', self.repo, '--record', 'no-direct-json-write',
                     '--commit', self.built['head'], expected=1)
            self.ensure(rules.read_text() == original_rules, '--record without a removed match fails without replacing tests')
            saved = json.loads(original_rules)
            saved['rules'][0]['regex'] += r'|\bimport json\b'
            rules.write_text(json.dumps(saved))
            before_record = rules.read_text()
            residual = self.json(patlint, '--rules', rules, '--root', self.repo, '--record', 'no-direct-json-write',
                                 '--commit', self.built['direct_write_fix'], '--json', expected=1)
            self.ensure(any('whole-file fixed replay' in f for f in residual['failures'])
                        and not any('noisy added line' in f for f in residual['failures'])
                        and rules.read_text() == before_record,
                        '--record rejects unchanged fixed-file matches even when added diff lines are quiet')
            saved['rules'][0]['regex'] = r'\b\w+\.path\.write_text\s*\(|self\.reply\(201'
            rules.write_text(json.dumps(saved))
            noisy_record = self.json(patlint, '--rules', rules, '--root', self.repo, '--record', 'no-direct-json-write',
                                    '--commit', self.built['direct_write_fix'], '--json', expected=1)
            self.ensure(any('noisy added line' in f for f in noisy_record['failures']),
                        '--record rejects added lines that still match the rule')
            rules.write_text(original_rules)
            old = self.scratch / 'past-direct-write'
            self.run('git', 'worktree', 'add', '--detach', old, self.built['direct_write_commits'][1])
            try:
                result = self.lint(rules, old / 'todo', root=old, expected=1)
                self.ensure(any(v['path']=='todo/api.py' for v in result['violations']), 'patlint fails on a real past mistake checked out in a worktree')
                self.save('past-violation.json', result)
            finally:
                self.run('git', 'worktree', 'remove', '--force', old)
            self.ensure(self.lint(rules, self.repo / 'todo')['ok'], 'same persistence lint passes on HEAD')
            self.correct_git_file_set()
            fixture = self.scratch / 'ratchet'
            (fixture / 'todo').mkdir(parents=True)
            file = fixture / 'todo/legacy.py'
            file.write_text('store.path.write_text(payload)\n')
            baseline = fixture / 'baseline.json'
            extra = ('--baseline', baseline)
            self.lint(rules, file, root=fixture, extra=(*extra,'--update-baseline'))
            self.ensure(self.lint(rules,file,root=fixture,extra=extra)['ok'], 'ratchet permits an adopted existing violation')
            file.write_text(file.read_text()*2)
            increased = self.lint(rules,file,root=fixture,extra=(*extra,'--update-baseline'),expected=1)
            self.ensure(bool(increased['baseline']['refused']), 'ratchet refuses increasing a known violation count')
            file.write_text('# clean\n')
            self.lint(rules,file,root=fixture,extra=(*extra,'--update-baseline'))
            self.ensure('no-direct-json-write' in json.loads(baseline.read_text())['counts'], 'zero-count adopted rules remain in the baseline')
            file.write_text('store.path.write_text(payload)\n')
            self.lint(rules,file,root=fixture,extra=(*extra,'--update-baseline'),expected=1)
            self.ensure(True, 'ratchet cannot re-adopt violations after reaching zero')
            file.write_text('# patlint: allow no-direct-json-write reason=legacy-import expires=2026-10-18 approved-by=reviewer\nstore.path.write_text(payload)\n')
            expired = self.lint(rules,file,root=fixture,extra=(*extra,'--today','2026-10-19'),expected=1)
            self.ensure(bool(expired['expired']), 'expired exception fails even in ratchet mode')
            file.write_text(file.read_text().replace('2026-10-18','2026-10-20'))
            valid = self.lint(rules,file,root=fixture,extra=('--today','2026-10-19'))
            self.ensure(len(valid['allowed'])==1, 'unexpired exception with reason and approver allows the intended violation')
            self.copy(baseline)
            table = self.json(self.script(self.name,'rule_table.py'),'check',self.repo / 'AGENTS.md','--json',expected=1)
            self.ensure(table['flagged']==1 and any(r['status']=='prose' and 'error messages' in r['rule'] for r in table['rules']),
                        'rule table flags exactly the prose-only example rule')
            prose = fixture / 'AGENTS.md'
            prose.write_text('| Rule | Enforced by |\n|---|---|\n| Lock writes | README documents the tests |\n')
            result = self.json(self.script(self.name,'rule_table.py'),'check',prose,'--json',expected=1)
            self.ensure(result['flagged']==1, 'mentioning tests in README prose does not qualify as enforcement')
            self.save('rule-table.json',table)

    def feedback(self):
        with self.case('feedback-to-architecture'):
            feedback = self.scratch / 'feedback.json'
            self.run(self.script(self.name,'collect_feedback.py'),'--repo',self.repo,'--pr-comments',self.repo/'review-comments.json',
                     '--chat',self.repo/'chat-feedback.md','--out',feedback)
            collected = json.loads(feedback.read_text())
            self.ensure(all(item.get('source') and item.get('ref') for item in collected['items']), 'every normalized feedback item has source provenance')
            self.ensure({i['source'] for i in collected['items']} == {'commit','review','chat'}, 'collection includes commit, review and chat evidence')
            plan = self.scratch / 'plan'
            result = self.json(self.script(self.name,'plan.py'),feedback,'--out-dir',plan)
            self.ensure(bool(result['proven']), 'generated lint is proven against history')
            self.ensure((plan/'REFACTOR_PLAN.md').is_file() and (plan/'lint-rules.json').is_file(), 'planner writes refactor plan and machine-readable lint rules')
            violations = self.lint(plan/'lint-rules.json', self.repo/'todo', expected=1,
                                   extra=('--rev',self.built['direct_write_commits'][1]))
            self.ensure(bool(violations['violations']), 'generated lint fires on the real direct-write mistake')
            self.ensure(self.lint(plan/'lint-rules.json',self.repo/'todo')['ok'], 'generated lint also passes the fixed HEAD')
            self.ensure(self.json(self.script('correct','patlint.py'), '--rules', plan/'lint-rules.json',
                                  '--root', self.repo, '--test', '--json')['ok'],
                        'planner retains executable bad and quiet cases from successful whole-file proofs')
            unproved = self.scratch / 'unproved-plan'
            result = self.json(self.script(self.name,'plan.py'), feedback, '--out-dir', unproved, '--no-prove')
            self.ensure(not result['proven'] and not any(r.get('tests') for r in json.loads((unproved/'lint-rules.json').read_text())['rules']),
                        '--no-prove never labels unverified cases as proven or records proof tests')
            # Exercise error path: failed fixed-side replay must never count as proof.
            module = load_module(self.script(self.name,'plan.py'),'example_plan')
            original = module.count_hits
            module.count_hits = lambda *args: (2 if str(args[-2]).endswith('^') else None, 'failed replay', [])
            try:
                proof = module.prove({'id':'test','include':['**/*.py']},[
                    {'source':'commit','kind':'fix','ref':'bad123','paths':['todo/api.py']}],plan/'lint-rules.json',self.repo)
                self.ensure(not proof['proven'], 'a failed fixed-revision replay never counts as proof')
            finally:
                module.count_hits = original
            for file in plan.iterdir():
                self.copy(file)
            self.copy(feedback)

    def architect(self):
        with self.case('architect'):
            good = self.copy(ROOT/'examples/fixtures/SKETCH.md')
            result = self.json(self.script(self.name,'sketch_check.py'),good,'--json')
            self.ensure(result['ok'] and len(result['candidates'])==2, 'two distinct ownership sketches pass the design gate')
            one = self.art / 'ONE-CANDIDATE.md'
            text = good.read_text()
            one.write_text(text[:text.index('## Candidate B')] + text[text.index('## Red-flags'):])
            result = self.json(self.script(self.name,'sketch_check.py'),one,'--json',expected=1)
            self.ensure(any(not c['ok'] and 'two candidates' in c['check'] for c in result['checks']), 'one-candidate sketch fails specifically for missing alternatives')

    def sharpen(self):
        with self.case('sharpen'):
            brief = self.art / 'BRIEF.md'
            self.run(self.script(self.name,'brief.py'),'Prevent lost todo writes and invalid status transitions', '--repo',self.repo,'--out',brief)
            text = brief.read_text()
            self.ensure(all(section in text for section in ['## In my own words','## How a top expert would approach it',
                                                            '## Specification check','## Outside check']), 'brief template includes restatement, expert, specification and outside check')
            self.run(self.script(self.name,'brief.py'),'--check',brief,expected=1)
            self.ensure(True,'unfilled brief fails its completion gate')
            filled = self.art/'FILLED-BRIEF.md'
            shutil.copyfile(ROOT / 'examples/fixtures/FILLED-BRIEF.md', filled)
            self.run(self.script(self.name,'brief.py'),'--check',filled)
            self.ensure(True,'filled brief with executable outside-check command passes')

    def formal(self):
        with self.case('formal-verification'):
            model = self.copy(ROOT/'examples/fixtures/todo_model.py')
            checker = self.script(self.name,'bounded_check.py')
            good = self.json(checker,model,'--mutants','--json')
            self.save('bounded-result.json',good)
            self.ensure(good['ok'] and good['exhaustive'] and good['states']>1, 'correct status and locked-writer model exhausts its finite reachable states without violation')
            self.ensure(len(good['mutants'])==3 and all(m['killed'] for m in good['mutants'].values()), 'checker kills unlocked writers, skipped done and repeated done mutants')
            for variant, prop in [('lost_update','no_lost_todo'),('skip_done','legal_status'),('repeat_done','legal_status')]:
                broken = self.art/f'{variant}.py'
                broken.write_text(f'from todo_model import *\nactions = {variant}\n')
                result = self.json(checker,broken,'--json',expected=1)
                self.ensure(result['violation']['name']==prop and len(result['violation']['trace'])>1, f'{variant} produces a real {prop} counterexample trace')
                self.save(f'{variant}-counterexample.json',result)
                self.json(checker,broken,'--expect-violation',prop,'--json')
            self.json(checker,model,'--depth','0','--json')
            self.run(checker,model,'--depth','-1',expected=2)
            empty = self.art/'empty.py'
            empty.write_text('def inits(): return []\ndef actions(s): return []\ninvariants={"ok":lambda s:True}\n')
            self.run(checker,empty,expected=2)
            self.ensure(True,'empty initial states and invalid bounds are rejected')
            dead = self.art/'dead.py'
            dead.write_text('def init(): return 0\ndef actions(s): return [("loop",s)]\ninvariants={"ok":lambda s:True}\nmutants={"dead":lambda s:[]}\n')
            result = self.json(checker,dead,'--mutants','--deadlock','--json',expected=1)
            self.ensure(not result['mutants']['dead']['killed'], 'deadlock alone does not prove an invariant mutant was killed')
            availability = self.json(self.script(self.name,'tla_available.sh'),'--json',expected=(0,1))
            self.ensure(self.last_returncode == (0 if availability['available'] else 1), 'TLA availability exit code matches its report and advertises the stdlib fallback')
            self.save('tla-availability.json', availability)
            self.generated_properties()
            self.formal_time_limits()
            self.formal_trace_timeout()
            self.formal_shrink_interrupt()
            self.formal_property_seeds()
            self.formal_import_timeout_json()
            self.formal_limit_support()
            self.review_deadline_order()
            self.review_synchronous_deadlines()
            self.review_import_budget()
            self.review_shrink_call_timeout()
            self.review_mutant_deadline()
            self.review_mutant_json()
            self.review_mutant_errors()
            self.review_sigint()
            self.review_fixture_limits()
            self.review_docs()
            self.review_property_handoff()
            self.review_call_timeout_exit()
            self.review_oneshot_timers()
            self.review_disabled_limits()

    def clock_check(self, checker, body, *, run=5, call=10, flags=()):
        """CLI semantics with a manually advanced clock: no scheduler or disk-speed assumptions."""
        driver = self.art / 'clock_driver.py'
        driver.write_text('''import contextlib, importlib.util, io, json, sys, types
checker, fixture, *argv = sys.argv[1:]
spec = importlib.util.spec_from_file_location("gen_check" if "gen_check" in checker else "bounded_check", checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
clock = types.ModuleType("review_clock")
clock.now, clock.events = 0.0, []
def advance(now, tick=True):
    clock.now = now
    if tick: clock.limits.tick(None, None)
clock.advance = advance
sys.modules[clock.__name__] = clock
mod.time = types.SimpleNamespace(monotonic=lambda: clock.now, time=lambda: clock.now)
class ManualLimits(mod.Limits):
    def __enter__(self):
        self.active = True
        clock.limits = self
        return self
    def __exit__(self, *exc):
        return False
mod.Limits = ManualLimits
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    code = mod.main([fixture, *argv])
print(json.dumps({"code": code, "stdout": out.getvalue(), "stderr": err.getvalue(), "events": clock.events}))
''')
        fixture = self.art / 'clock_fixture.py'
        fixture.write_text(body)
        args = ('--repo', self.repo, '--examples', '1') if checker == 'gen_check.py' else ()
        timeout_args = ('--timeout', run) if run is not None else ()
        return self.json(sys.executable, driver, self.script('formal-verification', checker), fixture,
                         *args, *timeout_args, '--call-timeout', call, *flags, timeout=10)

    def review_property_handoff(self):
        target = self.repo / 'handoff_target.py'
        target.write_text('def checked(x): return False\n')
        fixture = self.art / 'handoff_props.py'
        fixture.write_text('from gen_check import sampled_from\nfrom handoff_target import checked\n'
                           'TARGETS = ["handoff_target.py:checked"]\n'
                           'properties = {"broken": (sampled_from([42]), checked)}\n')
        driver = self.art / 'handoff_driver.py'
        driver.write_text('''import importlib.util, signal, sys
checker, *argv = sys.argv[1:]
spec = importlib.util.spec_from_file_location("gen_check", checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
original = mod.run_property
def interrupted_return(*args, **kwargs):
    result = original(*args, **kwargs)
    assert result[0]["result"] == "fail" and result[0]["counterexample"] == "42"
    limits = args[7]
    limits.deadline = limits.begun - 1
    signal.raise_signal(signal.SIGALRM)  # exactly after return, before main can write back
    raise AssertionError("injected SIGALRM was not delivered")
mod.run_property = interrupted_return
raise SystemExit(mod.main(argv))
''')
        try:
            for flags, code in (((), 1), (('--expect-fail', 'broken'), 3)):
                report = self.json(sys.executable, driver, self.script('formal-verification', 'gen_check.py'),
                                   fixture, '--repo', self.repo, '--examples', '1', '--json', *flags, expected=code)
                found = report['properties']['broken']
                self.ensure(not report['ok'] and found['result'] == 'fail' and found['counterexample'] == '42'
                            and found['detail'] == 'returned False' and found['accepted'] == 1
                            and report['called'] == ['handoff_target.py:checked'] and not report['missing_targets']
                            and 'in broken' in report['incomplete'] and 'import' not in report['incomplete'],
                            f'SIGALRM at property return retains the confirmed counterexample and coverage (exit {code})')
        finally:
            target.unlink()

    def review_call_timeout_exit(self):
        body = ('import review_clock as clock\nfrom gen_check import sampled_from\n'
                'TARGETS = ["todo/store.py:Store.add"]\n'
                'def check(x): clock.advance(2)\n'
                'properties = {"slow": (sampled_from([42]), check), "later": (sampled_from([0]), lambda x: True)}\n')
        failures = []
        for flags in ((), ('--expect-fail', 'slow')):
            result = self.clock_check('gen_check.py', body, run=20, call=1, flags=(*flags, '--json'))
            report = json.loads(result['stdout'])
            if (result['code'] != 3 or report['ok'] or report['properties']['slow']['result'] != 'timeout'
                    or report['properties']['slow']['input'] != '42'
                    or report['properties']['slow']['timeout_scope'] != 'call'
                    or report['properties']['later']['result'] != 'pass'):
                failures.append(result)
            text = self.clock_check('gen_check.py', body, run=20, call=1, flags=flags)
            if (text['code'] != 3 or 'ERROR' in text['stdout'] or 'INCONCLUSIVE' not in text['stdout']
                    or 'input: 42' not in text['stdout']):
                failures.append(text)
        self.ensure(not failures, f'property call timeouts are inconclusive exit 3 in JSON and text, never ERROR: {failures}')

    def review_oneshot_timers(self):
        from types import SimpleNamespace
        failures = []
        for checker in ('gen_check.py', 'bounded_check.py'):
            mod = load_module(self.script('formal-verification', checker), 'timer_' + checker[:-3])
            now, due, interval = 0.0, 40.0, 2.0
            previous = handler = lambda *args: None
            calls = []

            def set_handler(sig, value):
                nonlocal handler
                old, handler = handler, value
                return old

            def get_timer(which):
                return (max(0.0, due - now) if due else 0.0, interval)

            def set_timer(which, delay, repeat=0.0):
                nonlocal due, interval
                old = get_timer(which)
                due, interval = now + delay if delay else 0.0, repeat
                calls.append((now, delay, repeat))
                return old

            alarm = SimpleNamespace(SIGALRM=signal.SIGALRM, ITIMER_REAL=signal.ITIMER_REAL, SIG_DFL=signal.SIG_DFL,
                                    signal=set_handler, getsignal=lambda sig: handler,
                                    getitimer=get_timer, setitimer=set_timer)
            with patch.object(mod, 'signal', alarm), patch.object(mod, 'time', SimpleNamespace(monotonic=lambda: now)):
                with mod.Limits(20, 5) as limits:
                    if (due, interval) != (20, 0):
                        failures.append((checker, 'run timer', due, interval))
                    for start, finish, expected in ((3, 4, 8), (18, 19, 20)):
                        now = start

                        def check(*args):
                            nonlocal now
                            if (due, interval) != (expected, 0):
                                failures.append((checker, 'nearest call deadline', due, interval, expected))
                            now = finish
                            return True

                        if checker == 'bounded_check.py':
                            limits.call(check)
                        else:
                            mod.attempt(SimpleNamespace(), check, 0, self.repo, limits)
                        if (due, interval) != (20, 0):
                            failures.append((checker, 'run timer after call', due, interval))
                if handler is not previous or (due, interval) != (40, 2):
                    failures.append((checker, 'previous handler/timer not restored', due, interval))
                if any(repeat for _, _, repeat in calls[:-1]):
                    failures.append((checker, 'periodic checker timer', calls))
                # If the owner changed, cleanup must leave that owner's handler and timer alone.
                with mod.Limits(20, 5):
                    replacement = lambda *args: None
                    set_handler(signal.SIGALRM, replacement)
                    set_timer(signal.ITIMER_REAL, 7)
                if handler is not replacement or (due, interval) != (now + 7, 0):
                    failures.append((checker, 'cleared another owner timer', due, interval))
                old_due = due
                elapsed = mod.Limits(1, 5)
                now += 2
                try:
                    with elapsed:
                        failures.append((checker, 'entered after startup budget elapsed'))
                except mod.Timeout as error:
                    if error.scope != 'run':
                        failures.append((checker, 'startup expiry used the call limit'))
                if handler is not replacement or (due, interval) != (old_due, 0):
                    failures.append((checker, 'startup timeout lost the previous signal state', due, interval))
        self.ensure(not failures, f'checkers arm one-shot nearest deadlines and restore only their owned signal state: {failures}')

    def review_disabled_limits(self):
        target = self.repo / 'disabled_target.py'
        target.write_text('def checked(x): return True\n')
        failures = []
        try:
            for checker in ('gen_check.py', 'bounded_check.py'):
                for run, call, end, code in ((0, 0, 1000, 0), (0, 1, 2, 3 if checker == 'gen_check.py' else 2),
                                             (1, 0, 2, 3), (None, 0, 1000, 0)):
                    body = ('import review_clock as clock\nTIMEOUT = 0\n'
                            'def check(x):\n    clock.advance(' + str(end) + ')\n    return True\n')
                    if checker == 'gen_check.py':
                        body += ('from gen_check import sampled_from\nfrom disabled_target import checked\n'
                                 'TARGETS = ["disabled_target.py:checked"]\n'
                                 'def covered(x):\n    check(x)\n    return checked(x)\n'
                                 'properties = {"ok": (sampled_from([0]), covered)}\n')
                    else:
                        body += 'def init(): return 0\ndef actions(s): return []\ninvariants = {"ok": check}\n'
                    result = self.clock_check(checker, body, run=run, call=call, flags=('--json',))
                    if (result['code'] != code or not result['stdout']
                            or json.loads(result['stdout']).get('ok') != (code == 0)):
                        failures.append((checker, run, call, code, result))
                mod = load_module(self.script('formal-verification', checker), 'disabled_' + checker[:-3])
                # Fully disabled limits must not take over an embedding process's SIGALRM.
                with patch.object(mod.signal, 'signal') as handler, patch.object(mod.signal, 'setitimer') as timer:
                    with mod.Limits(0, 0):
                        pass
                    if handler.called or timer.called:
                        failures.append((checker, 'disabled limits changed signal state'))
                for value in (-1, float('nan'), float('inf'), True):
                    try:
                        mod.seconds(value, 'limit')
                    except (ValueError, mod.PropsError if checker == 'gen_check.py' else mod.ModelError):
                        pass
                    else:
                        failures.append((checker, 'invalid limit accepted', repr(value)))
            self.ensure(not failures, f'zero disables each deadline independently while invalid limits remain errors: {failures}')
        finally:
            target.unlink()
        text = (ROOT / 'skills/formal-verification/SKILL.md').read_text()
        step4 = text.split('## 4. Leave a project-local skill')[1].split('## 5.')[0]
        self.ensure(all(term in text and term in step4 for term in
                        ('--seed N --expect-fail NAME', '90 s', '10 s', '--timeout 0', '--call-timeout 0', 'migration')),
                    'skill and generated-skill instructions include replay and default-limit migration with opt-outs')

    def review_synchronous_deadlines(self):
        """Advance past deadlines without delivering SIGALRM, including the last covered call."""
        target = self.repo / 'review_sync_target.py'
        target.write_text('def checked(value): return True\n')
        failures = []

        def verify(label, result, code, events=None):
            report = json.loads(result['stdout'])
            if (result['code'] != code or report['ok'] != (code == 0)
                    or (events is not None and result['events'] != events)):
                failures.append((label, code, events, result))
            return report

        props = ('from gen_check import Generator, sampled_from, reject\n'
                 'from review_sync_target import checked\nTARGETS = ["review_sync_target.py:checked"]\n')
        model = 'def init(): return 0\ndef actions(s): return []\n'
        try:
            # Cover the last property, last invariant and last actions call. Exact equality expires too.
            for checker, phase in (('gen_check.py', 'check'), ('bounded_check.py', 'invariant'),
                                   ('bounded_check.py', 'actions')):
                for run, call, start, end, code in ((5, 6, 0, 4.99, 0), (5, 6, 0, 5, 3),
                                                   (5, 6, 0, 5.02, 3), (6, 5, 0, 5.02, 2),
                                                   (6, 5, 0, 5, 2), (10, 2, 4, 6, 2),
                                                   (5, 5, 0, 5.02, 3), (5, 2, 4, 5.02, 3),
                                                   (6, 5, 0, 20, 2)):
                    if checker == 'gen_check.py' and code == 2:
                        code = 3
                    body = ('import review_clock as clock\n' + (props if checker == 'gen_check.py' else model)
                            + f'clock.advance({start}, tick=False)\n'
                            'def check(x):\n    clock.events.append("checked")\n'
                            + ('    checked(x)\n' if checker == 'gen_check.py' else '')
                            + f'    clock.advance({end}, tick=False)\n'
                            + ('    return []\n' if phase == 'actions' else '    return True\n'))
                    body += ('properties = {"ok": (sampled_from([0]), check)}\n' if checker == 'gen_check.py' else
                             'actions = check\ninvariants = {"ok": lambda s: True}\n' if phase == 'actions' else
                             'invariants = {"ok": check}\nDEPTH = 0\n')
                    result = self.clock_check(checker, body, run=run, call=call, flags=('--json',))
                    report = verify((checker, phase, run, call, start, end), result, code, ['checked'])
                    if checker == 'gen_check.py' and code:
                        if report['called'] or report['properties']['ok']['accepted'] != 0:
                            failures.append(('timed-out input counted as accepted coverage', result))
            # Returning False, rejecting, or raising after a deadline cannot manufacture a counterexample.
            for ending in ('return False', 'reject()', 'raise ValueError("late")'):
                body = ('import review_clock as clock\n' + props + 'def check(x):\n    checked(x)\n'
                        '    clock.advance(5.02, tick=False)\n    ' + ending + '\n'
                        'properties = {"ok": (sampled_from([0]), check)}\n')
                for run, call, code in ((5, 6, 3), (6, 5, 3)):
                    verify(('late outcome', ending, code), self.clock_check('gen_check.py', body, run=run,
                           call=call, flags=('--json',)), code)
            for hook in ('setup', 'reset'):
                body = ('import review_clock as clock\n' + props + f'def {hook}():\n'
                        f'    clock.events.append("{hook}")\n    clock.advance(5.02, tick=False)\n'
                        'def check(x):\n    clock.events.append("check")\n    return checked(x)\n'
                        'properties = {"ok": (sampled_from([0]), check)}\n')
                for run, call, code in ((5, 6, 3), (6, 5, 3)):
                    verify((hook, code), self.clock_check('gen_check.py', body, run=run, call=call,
                           flags=('--json',)), code, [hook])
            # A call timeout may end one property, but it cannot start another after the total budget elapsed.
            body = ('import review_clock as clock\n' + props + 'def first(x):\n'
                    '    clock.events.append("first")\n    checked(x)\n    clock.advance(20, tick=False)\n'
                    'def later(x):\n    clock.events.append("later")\n    return checked(x)\n'
                    'properties = {"first": (sampled_from([0]), first), "later": (sampled_from([0]), later)}\n')
            for run, call, code in ((5, 6, 3), (6, 5, 3)):
                verify(('no later property', code), self.clock_check('gen_check.py', body, run=run, call=call,
                       flags=('--json',)), code, ['first'])
            body = ('import review_clock as clock\n' + props + 'def draw(rng):\n'
                    '    clock.events.append("draw")\n    clock.advance(5.02, tick=False)\n    return 0\n'
                    'def check(x):\n    clock.events.append("check")\n    return checked(x)\n'
                    'properties = {"ok": (Generator(draw, lambda x: []), check)}\n')
            verify('generation exhausts budget before input', self.clock_check('gen_check.py', body,
                   flags=('--json',)), 3, ['draw'])
            body = ('import review_clock as clock\n' + props + 'def check(x):\n    checked(x)\n'
                    '    clock.events.append(x)\n    if x == 1: clock.advance(5.02, tick=False)\n'
                    '    return False\nproperties = {"broken": (Generator(lambda rng: 2, lambda x: [1]), check)}\n')
            for run, call, code in ((5, 6, 3), (6, 5, 3)):
                report = verify(('shrink return', code), self.clock_check('gen_check.py', body, run=run, call=call,
                                flags=('--expect-fail', 'broken', '--json')), code, [2, 1])
                if report['properties']['broken']['counterexample'] != '2' or not report['incomplete']:
                    failures.append(('late shrink discarded confirmed counterexample or incomplete status', report))
            # A property iterator can consume the remaining budget between properties or at exhaustion.
            for tail in ('yield "later", (sampled_from([0]), check)', 'return'):
                body = ('import review_clock as clock\n' + props + 'def check(x):\n'
                        '    clock.events.append("check")\n    return checked(x)\n'
                        'class Properties(dict):\n    def items(self):\n'
                        '        yield "first", self["first"]\n'
                        '        if clock.events: clock.advance(5.02, tick=False)\n        ' + tail + '\n'
                        'properties = Properties(first=(sampled_from([0]), check))\n')
                verify(('property boundary', tail), self.clock_check('gen_check.py', body,
                       flags=('--json',)), 3, ['check'])
            # Initialization/terminal/description use the run budget, including returns between ticks.
            for phase in ('init', 'terminal', 'describe'):
                body = ('import review_clock as clock\n' + model
                        + 'invariants = {"ok": lambda s: s >= 0}\n'
                        'def broken(s): return [("bad", -1)]\n'
                        'def later(s):\n    clock.events.append("later")\n    return []\n'
                        'mutants = {"broken": broken, "later": later}\n'
                        + f'def {phase}(' + ('' if phase == 'init' else 's') + '):\n'
                        + f'    clock.events.append("{phase}")\n    clock.advance(5.02, tick=False)\n'
                        + ('    return 0\n' if phase == 'init' else '    return True\n'))
                flags = ('--mutants', '--json') if phase == 'describe' else ('--deadlock', '--json')
                verify(phase, self.clock_check('bounded_check.py', body, flags=flags), 3, [phase])
            body = ('import review_clock as clock\n' + model + 'invariants = {"ok": lambda s: s >= 0}\n'
                    'def broken(s): return [("bad", -1)]\n'
                    'class Mutants(dict):\n    def items(self):\n'
                    '        yield "broken", self["broken"]\n        clock.advance(5.02, tick=False)\n'
                    'mutants = Mutants(broken=broken)\n')
            verify('final mutant result boundary', self.clock_check('bounded_check.py', body,
                   flags=('--mutants', '--json')), 3)
            body = ('import review_clock as clock\n' + model + 'invariants = {"ok": lambda s: True}\n'
                    'def late(s):\n    clock.events.append("mutant")\n'
                    '    clock.advance(20, tick=False)\n    return []\n'
                    'def later(s):\n    clock.events.append("later")\n    return []\n'
                    'mutants = {"late": late, "later": later}\n')
            for run, call, code in ((5, 6, 3), (6, 5, 2)):
                report = verify(('mutant return', code), self.clock_check('bounded_check.py', body, run=run, call=call,
                                flags=('--mutants', '--json')), code, ['mutant'])
                if report['not_run'] != ['later']:
                    failures.append(('late mutant started pending search', report))
            self.ensure(not failures, f'synchronous deadline checks honor earliest expiry without timer delivery: {failures}')
        finally:
            target.unlink()

    def review_import_budget(self):
        target = self.repo / 'review_budget_target.py'
        target.write_text('def checked(value): return True\n')
        failures = []
        try:
            for checker in ('bounded_check.py', 'gen_check.py'):
                for elapsed, override, code in ((0.9, None, 0), (1, None, 3), (1.1, None, 3),
                                                (1.1, 2, 0), (2.1, 2, 3)):
                    body = ('import review_clock as clock\nTIMEOUT = 1\n'
                            f'clock.advance({elapsed}, tick=False)\n')
                    body += ('from review_budget_target import checked\n' if checker == 'gen_check.py' else
                             'def checked(value): return True\n')
                    body += ('def check(x):\n    clock.events.append("checked")\n    return checked(x)\n')
                    if checker == 'gen_check.py':
                        body += ('from gen_check import sampled_from\nTARGETS = ["review_budget_target.py:checked"]\n'
                                 'properties = {"ok": (sampled_from([0]), check)}\n')
                    else:
                        body += 'def init(): return 0\ndef actions(s): return []\ninvariants = {"ok": check}\n'
                    result = self.clock_check(checker, body, run=override, flags=('--json',))
                    report = json.loads(result['stdout'])
                    valid = result['code'] == code and report['ok'] == (code == 0)
                    if code == 3:
                        valid = valid and not result['events'] and bool(report['incomplete'])
                        if checker == 'gen_check.py':
                            valid = valid and report['properties'] == {} and report['timeout'] == (override or 1)
                    else:
                        valid = valid and result['events'] == ['checked']
                    if not valid:
                        failures.append((checker, elapsed, override, code, result))
            self.ensure(not failures,
                        f'import time counts toward TIMEOUT, including equality; explicit overrides retain their budget: {failures}')
        finally:
            target.unlink()

    def review_go_receivers(self):
        root = self.scratch / 'review-go-receivers'
        root.mkdir()
        cases = [
            ('mux alias', 'm := http.NewServeMux(); m.HandleFunc("GET /todos", h)', {('GET', '/todos')}),
            ('struct field', 's.mux.HandleFunc("GET /todos", h)', {('GET', '/todos')}),
            ('Handle alias', 'm.Handle("/todos/", h)', {('ANY', '/todos/<*>')}),
            ('router alias', 'apiRoutes.Get("/todos", h)', {('GET', '/todos')}),
            ('router field', 's.routes.POST("/todos", h)', {('POST', '/todos')}),
            ('returned mux', 'newMux().HandleFunc("GET /todos", h)', {('GET', '/todos')}),
            ('filesystem store', 'kv.Put("/var/lib/data", v)', set()),
            ('untrusted field', 's.router.Put("/var/lib/data", v)', set()),
            ('untrusted Handle', 'store.Handle("/var/lib/data", v)', set()),
            ('known receivers', 'http.HandleFunc("/home", h); router.GET("/Users", h); r.Put("/var/routes", h)',
             {('ANY', '/home'), ('GET', '/Users'), ('PUT', '/var/routes')}),
        ]
        failures = []
        for name, source, expected in cases:
            (root / 'main.go').write_text(f'package main\nfunc main() {{ {source} }}\n')
            result = self.json(self.script('create-verification-skill', 'scan_repo.py'), root, '--json')
            routes = {(r['method'], r['path']) for r in result['http_routes']}
            kinds = {c['kind'] for c in result['launch_candidates']}
            if routes != expected or kinds != ({'server'} if expected else {'cli'}):
                failures.append((name, sorted(routes), sorted(expected), kinds))
        self.ensure(not failures,
                    f'Go aliases, fields and returned muxes retain routes and server launches; FS bypass needs a known receiver: {failures}')

    def review_shrink_call_timeout(self):
        target = self.repo / 'review_shrink_target.py'
        target.write_text('def confirmed(value): return False\ndef timed_out(value): return True\n')
        body = ('import review_clock as clock\nfrom gen_check import Generator, sampled_from\n'
                'from review_shrink_target import confirmed, timed_out\n'
                'TARGETS = ["review_shrink_target.py:confirmed"]\n'
                'def check(x):\n    clock.events.append(f"check {x}")\n'
                '    if x == 0:\n        timed_out(x)\n        clock.advance(2)\n'
                '    return confirmed(x)\n'
                'def shrink(x):\n    if x == 100: yield 60\n'
                '    yield 0\n    yield 30\n'
                'def later(x):\n    clock.events.append("later")\n    return True\n'
                'properties = {"broken": (Generator(lambda rng: 100, shrink), check),\n'
                '              "later": (sampled_from([1]), later)}\n')
        try:
            for flags, code in ((('--expect-fail', 'broken'), 3), ((), 1)):
                result = self.clock_check('gen_check.py', body, run=20, call=1, flags=(*flags, '--json'))
                report = json.loads(result['stdout'])
                found = report['properties']['broken']
                self.ensure(result['code'] == code and not report['ok'] and report['incomplete']
                            and found['result'] == 'fail' and found['counterexample'] == '60'
                            and found['detail'] == 'returned False' and found['shrink_attempts'] == 2
                            and found.get('timeout_scope') == 'call' and found.get('timeout_phase') == 'shrink'
                            and found.get('incomplete') and found.get('input') == '0',
                            f'shrink call timeout preserves the best confirmed failure and reports incomplete reduction (exit {code})')
                self.ensure(result['events'] == ['check 100', 'check 60', 'check 0', 'later']
                            and report['properties']['later']['result'] == 'pass' and not report['missing_targets']
                            and report['called'] == ['review_shrink_target.py:confirmed'],
                            'a shrink call timeout ends only that property and keeps only accepted target coverage')
            result = self.clock_check('gen_check.py', body, run=20, call=1, flags=('--expect-fail', 'broken'))
            self.ensure(result['code'] == 3 and 'reduced counterexample: 60' in result['stdout']
                        and 'incomplete:' in result['stdout'] and 'shrink' in result['stdout']
                        and 'call timeout' in result['stdout'],
                        'text mode exposes the retained counterexample and incomplete shrink instead of PASS')
            result = self.clock_check('gen_check.py', body.replace('return True', 'return False'),
                                      run=20, call=1, flags=('--expect-fail', 'broken', '--json'))
            report = json.loads(result['stdout'])
            self.ensure(result['code'] == 1 and report['incomplete'] and report['properties']['later']['result'] == 'fail',
                        'an independent unexpected failure retains exit 1 alongside the expected failure and shrink timeout')
            # Exercise the actual SIGALRM path too, with a second-scale call budget and an external deadline.
            props = self.art / 'shrink_hang.py'
            props.write_text('from gen_check import Generator\nfrom review_shrink_target import confirmed\n'
                             'TARGETS = ["review_shrink_target.py:confirmed"]\n'
                             'def check(x):\n    while x == 0: pass\n    return confirmed(x)\n'
                             'properties = {"broken": (Generator(lambda rng: 100, lambda x: iter([0])), check)}\n')
            report = self.json(self.script('formal-verification', 'gen_check.py'), props, '--repo', self.repo,
                               '--examples', '1', '--timeout', '20', '--call-timeout', '2',
                               '--expect-fail', 'broken', '--json', expected=3, timeout=30)
            self.ensure(not report['ok'] and report['incomplete'] and not report['missing_targets']
                        and report['properties']['broken']['counterexample'] == '100'
                        and report['properties']['broken'].get('timeout_scope') == 'call'
                        and report['properties']['broken'].get('timeout_phase') == 'shrink',
                        'a genuinely hung shrink candidate is interrupted and cannot satisfy --expect-fail')
        finally:
            target.unlink()

    def review_deadline_order(self):
        for checker in ('bounded_check.py', 'gen_check.py'):
            for run, call, code in ((5, 6, 3), (6, 5, 2), (5, 5, 3)):
                if checker == 'gen_check.py' and code == 2:
                    code = 3
                body = ('import review_clock as clock\n'
                        'def check(s):\n    clock.advance(20)\n    return True\n')
                if checker == 'bounded_check.py':
                    body += 'def init(): return 0\ndef actions(s): return []\ninvariants = {"ok": check}\n'
                else:
                    body += ('from gen_check import sampled_from\nTARGETS = ["todo/store.py:Store.add"]\n'
                             'properties = {"ok": (sampled_from([0]), check)}\n')
                result = self.clock_check(checker, body, run=run, call=call, flags=('--json',))
                report = json.loads(result['stdout'])
                self.ensure(result['code'] == code and not report['ok'],
                            f'{checker}: simultaneous expiry uses the first absolute deadline ({run=}, {call=}, exit {code})')
            # A later-started call has a later absolute deadline even when its duration is shorter.
            result = self.clock_check(checker, body.replace('def check(s):', 'clock.advance(4, tick=False)\ndef check(s):'),
                                      run=5, call=2, flags=('--json',))
            self.ensure(result['code'] == 3, f'{checker}: deadline order includes the call start time')

    def review_mutant_deadline(self):
        body = ('import review_clock as clock\ndef init(): return 0\ndef actions(s): return []\n'
                'invariants = {"nonnegative": lambda s: s >= 0}\n'
                'def broken(s): return [("bad", -1)]\n'
                'def later(s):\n    clock.events.append("later search started")\n    return []\n'
                'def describe(s):\n    clock.events.append("describe")\n    clock.advance(20)\n'
                'mutants = {"broken": broken, "later": later, "last": later}\n')
        for flags in (('--json',), ()):
            result = self.clock_check('bounded_check.py', body, flags=('--mutants', *flags))
            self.ensure(result['events'] == ['describe'] and result['code'] == 3,
                        'a timed-out mutant description retains its kill and starts no later search')
            if flags:
                report = json.loads(result['stdout'])
                self.ensure(report['mutants']['broken']['killed'] and report['incomplete']
                            and report['not_run'] == ['later', 'last']
                            and [s['state'] for s in report['mutants']['broken']['trace']] == [0, -1],
                            'confirmed mutant kill and raw trace survive the total deadline; pending names stay visible')
            else:
                self.ensure('SURVIVED' not in result['stdout'] and 'INCOMPLETE' in result['stdout']
                            and 'later' in result['stdout'] and 'last' in result['stdout'],
                            'unrun mutants print INCOMPLETE instead of SURVIVED')
        # Deadline passes without a signal between searches: a preflight must prevent new work.
        result = self.clock_check('bounded_check.py', body.replace('def actions(s): return []',
                                  'def actions(s):\n    clock.advance(20, tick=False)\n    return []'),
                                  flags=('--mutants', '--json'))
        report = json.loads(result['stdout'])
        self.ensure(not result['events'] and result['code'] == 3 and report['not_run'] == ['broken', 'later', 'last'],
                    'an expired deadline between searches cannot start the first mutant')

    def review_mutant_json(self):
        body = ('import review_clock as clock\ndef init(): return 0\ndef actions(s): return []\n'
                'invariants = {"ok": lambda s: s >= 0}\n'
                'def killed(s): return [("bad", -1)]\n'
                'def slow(s):\n    clock.advance(20)\n    return []\n'
                'mutants = {"killed": killed, "slow": slow, "pending": actions}\n')
        for run, call, code, status in ((5, 10, 3, 'timeout'), (30, 5, 2, 'error')):
            result = self.clock_check('bounded_check.py', body, run=run, call=call, flags=('--mutants', '--json'))
            report = json.loads(result['stdout'])
            slow = report['mutants']['slow']
            self.ensure(result['code'] == code and slow.get('result') == status and slow.get('stopped')
                        and slow.get('detail') and slow.get('states') == 1 and report.get('incomplete'),
                        f'mutant JSON preserves {status}, stop reason, detail, states and top-level incompleteness')
            self.ensure(report['mutants']['killed']['killed']
                        and all({'result', 'stopped', 'detail'} <= m.keys() for m in report['mutants'].values())
                        and report['not_run'] == (['pending'] if status == 'timeout' else []),
                        'every mutant has an explicit status and unrun names are separate from survivors')
        result = self.clock_check('bounded_check.py', body.replace('def actions(s): return []',
                                  'def actions(s):\n    clock.advance(20)\n    return []'),
                                  flags=('--mutants', '--json'))
        report = json.loads(result['stdout'])
        self.ensure(result['code'] == 3 and report['not_run'] == ['killed', 'slow', 'pending']
                    and all(m['result'] == 'timeout' and m['states'] == 0 for m in report['mutants'].values()),
                    'a main-search timeout lists every unrun mutant as incomplete with zero explored states')

    def review_mutant_errors(self):
        body = ('import review_clock as clock\ndef init(): return 0\ndef actions(s): return []\n'
                'invariants = {"ok": lambda s: True}\n'
                'def crash(s): raise ValueError("broken mutant")\n'
                'def slow(s):\n    clock.advance(20)\n    return []\n'
                'mutants = {"crash": crash, "slow": slow}\n')
        for suffix in ('', 'mutants = {"crash": crash}\n'):
            result = self.clock_check('bounded_check.py', body + suffix, flags=('--mutants',))
            self.ensure(result['code'] == 2 and 'ERROR' in result['stdout'] and 'broken mutant' in result['stdout']
                        and 'SURVIVED' not in result['stdout'],
                        'a crashing mutant reports model error exit 2, including before a later run timeout')

    def review_sigint(self):
        self.copy(ROOT / 'examples/fixtures/todo_props.py')
        # A child interpreter inherits SIG_IGN in non-interactive/background execution.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            self.formal_shrink_interrupt()
        finally:
            signal.signal(signal.SIGINT, previous)

    def review_docs(self):
        text = (ROOT / 'CHANGELOG.md').read_text()
        self.ensure('same `--seed`' in text and '90 s' in text and '10 s' in text
                    and 'TIMEOUT' in text and 'revalidate' in text and 'pinned' in text,
                    'changelog documents changed seed sequences, default limits and replay/timeout migration')
        readme = (ROOT / 'logs/review-fixes/README.md').read_text()
        self.ensure('recheck-before' in readme and 'recheck-after' in readme
                    and 'No commit or push was made in the working branch.' not in readme,
                    'review evidence distinguishes this follow-up from the already committed first review fixes')

    def review_fixture_limits(self):
        import ast
        tree = ast.parse(Path(__file__).read_text())
        names = {'formal_trace_timeout', 'formal_property_seeds', 'formal_import_timeout_json'}
        values = []
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in names:
                for node in ast.walk(fn):
                    items = node.args if isinstance(node, ast.Call) else node.elts if isinstance(node, ast.List) else []
                    for flag, value in zip(items, items[1:]):
                        if isinstance(flag, ast.Constant) and flag.value in ('--timeout', '--call-timeout'):
                            values.append(float(ast.literal_eval(value)))
        self.ensure(len(values) == 4 and all(v >= 1 for v in values),
                    'disk-using timeout fixtures use second-scale limits instead of subsecond scheduling assumptions')

    def formal_trace_timeout(self):
        checker = self.script('formal-verification', 'bounded_check.py')
        model = self.art / 'slow_trace_model.py'
        model.write_text('import time\ndef init(): return 0\n'
                         'def actions(s): return [("inc", s + 1)]\n'
                         'invariants = {"below_two": lambda s: s < 2}\n'
                         'def describe(s):\n    if s == 1: time.sleep(10)\n    return f"state {s}"\n')
        for flags, code in (((), 1), (('--expect-violation', 'below_two'), 0)):
            result = self.json(checker, model, '--timeout', '2', '--json', *flags, expected=code)
            violation = result['violation']
            self.ensure(result['result'] == 'violation' and violation['name'] == 'below_two'
                        and [s['state'] for s in violation['trace']] == [0, 1, 2]
                        and violation['trace'][0]['describe'] == 'state 0' and violation.get('trace_incomplete'),
                        f'a describe timeout retains the confirmed invariant violation and raw trace (exit {code})')
        output = self.run(checker, model, '--timeout', '2', expected=1)
        self.ensure('invariant "below_two" violated' in output and 'trace' in output,
                    'text output retains the failure and explains incomplete trace descriptions')

    def formal_shrink_interrupt(self):
        checker = self.script('formal-verification', 'gen_check.py')
        props = self.art / 'props_shrink_interrupt.py'
        props.write_text('from gen_check import Generator\nimport signal\n'
                         'from todo_props import reset, round_trip\n'
                         'TARGETS = ["todo/store.py:Store.add"]\n'
                         'def check(x):\n    round_trip("valid")\n    return x < 10\n'
                         'def shrink(x):\n    if x == 100: yield 60\n'
                         '    signal.signal(signal.SIGINT, signal.default_int_handler)\n    signal.raise_signal(signal.SIGINT)\n'
                         'properties = {"broken": (Generator(lambda rng: 100, shrink), check)}\n')
        result = self.json(checker, props, '--repo', self.repo, '--json', expected=1)
        found = result['properties']['broken']
        self.ensure(found['result'] == 'fail' and found['counterexample'] == '60' and found['shrink_attempts'] == 1
                    and found['detail'] == 'returned False' and result['incomplete'] == 'interrupted'
                    and not result['missing_targets'] and 'todo/store.py:Store.add' in result['called'],
                    'SIGINT during shrinking preserves the best counterexample, accepted target calls and failure exit 1')
        incomplete = self.json(checker, props, '--repo', self.repo, '--expect-fail', 'broken', '--json', expected=3)
        self.ensure(not incomplete['ok'] and incomplete['properties']['broken']['counterexample'] == '60',
                    'an interrupted shrink keeps its failure but cannot complete an --expect-fail proof')

    def formal_property_seeds(self):
        checker = self.script('formal-verification', 'gen_check.py')
        props = self.art / 'props_independent_seeds.py'
        body = ('from gen_check import Generator\nimport time\n'
                'from todo_props import reset, round_trip\nTARGETS = ["todo/store.py:Store.add"]\n'
                'calls = 0\nSTOP_AFTER = {stop}\n'
                'def lead(x):\n    global calls\n    calls += 1\n'
                '    if calls == STOP_AFTER: time.sleep(10)\n'
                'def later(x):\n    round_trip("valid")\n    return False\n'
                'gen = Generator(lambda rng: rng.randrange(2**60), lambda x: [])\n'
                'properties = {{"lead": (gen, lead), "later": (gen, later)}}\n')
        reports = []
        for stop, seed in ((1, 42), (3, 42), (1, 43)):
            props.write_text(body.format(stop=stop))
            result = self.json(checker, props, '--repo', self.repo, '--seed', seed, '--examples', '5',
                               '--call-timeout', '2', '--json', expected=1)
            self.ensure(result['properties']['lead']['result'] == 'timeout'
                        and result['properties']['lead']['generated'] == stop and not result['missing_targets'],
                        f'leading property times out after {stop} draws with seed {seed}')
            reports.append(result['properties']['later'])
        self.ensure(reports[0] == reports[1] and reports[0]['counterexample'] != reports[2]['counterexample'],
                    'each property has its own seeded stream: an earlier timeout cannot change later inputs or replay')

    def formal_import_timeout_json(self):
        checker = self.script('formal-verification', 'bounded_check.py')
        model = self.art / 'slow_import_model.py'
        model.write_text('import time\ntime.sleep(10)\n'
                         'def init(): return 0\ndef actions(s): return []\ninvariants = {"ok": lambda s: True}\n')
        argv = [str(checker), str(model), '--timeout', '2', '--json']
        self.log.write('$ ' + shlex.join(argv) + '\n')
        result = subprocess.run(argv, cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=10)
        self.log.write(result.stdout + result.stderr + f'\n[exit {result.returncode}; expected 3]\n')
        self.ensure(result.returncode == 3, 'an import-time run limit exits 3')
        report = json.loads(result.stdout)
        self.ensure(report['result'] == 'timeout' and not report['ok'] and report['violation'] is None
                    and report['model'] == str(model) and report['timeout'] == 2 and report['stopped'],
                    '--json remains parseable on an import-time timeout and identifies an inconclusive run')

    def formal_limit_support(self):
        """Simulate missing interval timers and execution outside the main thread without hanging the runner."""
        driver = self.art / 'unsupported_limits.py'
        driver.write_text('''import contextlib, io, json, runpy, signal, sys, threading
checker, scenario, *argv = sys.argv[1:]
if scenario == "no_timer": del signal.setitimer
sys.argv = [checker, *argv]
out, err, codes = io.StringIO(), io.StringIO(), []
def run():
    try: runpy.run_path(checker, run_name="__main__")
    except SystemExit as e: codes.append(e.code)
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    if scenario == "worker":
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
    else: run()
print(json.dumps({"code": codes[0], "report": json.loads(out.getvalue()), "stderr": err.getvalue()}))
''')
        model, props = self.art / 'unsupported_model.py', self.art / 'props_unsupported.py'
        model.write_text('import time\ndef init():\n    time.sleep(0.1)\n    return 0\n'
                         'def actions(s): return []\ninvariants = {"ok": lambda s: True}\n')
        props.write_text('from gen_check import sampled_from\nimport time\n'
                         'from todo_props import reset, round_trip\nTARGETS = ["todo/store.py:Store.add"]\n'
                         'def check(x):\n    time.sleep(0.1)\n    round_trip("valid")\n'
                         'properties = {"ok": (sampled_from([0]), check)}\n')
        for file, fixture, flags in (('bounded_check.py', model, ()),
                                     ('gen_check.py', props, ('--repo', self.repo, '--examples', '1'))):
            for scenario in ('no_timer', 'worker'):
                result = self.json(sys.executable, driver, self.script('formal-verification', file), scenario,
                                   fixture, *flags, '--timeout', '0.02', '--call-timeout', '0.02', '--json')
                warning = result['stderr']
                self.ensure(result['code'] == 0 and result['report']['ok'] and 'WARNING' in warning
                            and '--timeout' in warning and '--call-timeout' in warning and 'not enforced' in warning,
                            f'{file} warns that both limits are not enforced for {scenario}, while stdout stays JSON')
        text = (ROOT / 'skills/formal-verification/SKILL.md').read_text()
        self.ensure(all(term in text for term in ('SIGALRM', 'main thread', 'native code', 'external')),
                    'formal-verification documents timer/thread support, native-code limits and an external deadline')

    def generated_properties(self):
        checker = self.script(self.name, 'gen_check.py')
        props = self.copy(ROOT/'examples/fixtures/todo_props.py')
        command = [checker, props, '--repo', self.repo]
        good = self.json(*command, '--json')
        self.ensure(good['ok'] and good['properties']['round_trip']['generated'] == 100,
                    'real Store round trip checks title.strip() and independent persistence after pinned examples')
        self.save('generated-result.json', good)
        source = self.repo/'todo/store.py'
        original = source.read_text()
        assignment = '"title": title.strip()'
        self.ensure(original.count(assignment) == 1, 'scratch break identifies only the persisted title assignment')
        try:
            source.write_text(original.replace(assignment, '"title": title'))
            output = self.run(*command, '--expect-fail', 'round_trip')
            self.ensure('reduced counterexample:' in output and 'seed 0' in output,
                        'scratch break prints a reduced counterexample and replay seed')
            broken = self.json(*command, '--expect-fail', 'round_trip', '--json')
            import ast
            title = ast.literal_eval(broken['properties']['round_trip']['counterexample'])
            self.ensure(title.strip() and title != title.strip() and len(title) < len('  pinned title  '),
                        'removing assignment strip shrinks to a nonblank title with surrounding whitespace')
            replay = self.json(*command, '--expect-fail', 'round_trip', '--json')
            self.ensure(replay == broken, 'same seed replays the same reduced counterexample and call report')
            self.save('generated-counterexample.json', broken)
        finally:
            source.write_text(original)
        self.json(*command, '--json')
        self.run(*command, '--expect-fail', 'round_trip', expected=1)

        def case(name, body, expected=1, *flags):
            path = self.art/f'props_{name}.py'
            path.write_text(body)
            return self.json(checker, path, '--repo', self.repo, '--json', *flags, expected=expected)

        header = 'from gen_check import *\nTARGETS = ["todo/store.py:Store.add"]\n'
        pasted = '''
def add(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a nonempty string")
    rows = []
    row = {"id": max((r["id"] for r in rows), default=0) + 1,
           "title": title.strip(), "status": "open"}
    rows.append(row)
    return row
def check(title):
    assert add(title)["title"] == title.strip()
properties = {"copy": (sampled_from([" title "]), check)}
'''
        for name, extra in [('copy', ''), ('imported_copy', 'import todo.store\nfrom pathlib import Path\nimport tempfile\n_temp = tempfile.TemporaryDirectory()\nstore = todo.store.Store(_temp.name)\n')]:
            result = case(name, header + extra + pasted)
            self.ensure(result['missing_targets'] == ['todo/store.py:Store.add'],
                        f'{name}: pasted add cannot satisfy declared Store.add even with imports/construction')
            if not extra:
                self.ensure(not result['called'], 'property-module functions are outside the repo call report')

        active = ('from todo_props import reset, round_trip\n'
                  'def check(x):\n    round_trip("valid")\n')
        result = case('filter', header + active.replace('    round_trip("valid")',
                      '    round_trip("valid")\n    if x: reject()') +
                      'properties = {"filtered": (sampled_from([True]), check)}\n'
                      'examples = {"filtered": [False]}\n')
        self.ensure(result['properties']['filtered']['result'] == 'filter_too_much' and not result['missing_targets'],
                    'over-half generated rejection fails even when pinned accepted input covers targets')
        case('filter_expected', (self.art/'props_filter.py').read_text(), 1, '--expect-fail', 'filtered')

        result = case('never', header + active + 'TARGETS += ["todo/store.py:Store.transition"]\n'
                      'properties = {"ok": (ints(), check)}\n')
        self.ensure(result['missing_targets'] == ['todo/store.py:Store.transition'],
                    'one uncalled declared target fails despite other repo functions running')
        result = case('import_call', header + 'from todo_props import reset, round_trip\n'
                      'reset()\nround_trip("imported")\n'
                      'properties = {"ok": (ints(), lambda x: None)}\n')
        self.ensure(not result['called'], 'target calls made during imports do not count')
        result = case('hook_call', header + 'from todo_props import reset, round_trip\n'
                      'def setup():\n    reset()\n    round_trip("setup")\n'
                      'properties = {"ok": (ints(), lambda x: None)}\n')
        self.ensure(not result['called'], 'target calls made during setup/reset do not count')
        result = case('rejected_call', header + active.replace('    round_trip("valid")',
                      '    round_trip("valid")\n    reject()') + 'properties = {"ok": (ints(), check)}\n')
        self.ensure(not result['called'], 'target calls made only in rejected checks do not count')

        # An exception in the check is a violation; exceptions in the runner's
        # import, hook, generation or shrinking paths are infrastructure errors.
        for name, extra, gen in [
            ('import_error', 'raise RuntimeError("import")\n', 'ints()'),
            ('setup_error', 'def setup(): raise RuntimeError("setup")\n', 'ints()'),
            ('reset_error', 'def reset(): raise RuntimeError("reset")\n', 'ints()'),
            ('hook_exit', 'def setup(): raise SystemExit(0)\n', 'ints()'),
            ('generation_error', 'def draw(rng): raise RuntimeError("draw")\n', 'Generator(draw, lambda x: [])'),
            ('shrink_error', 'def shrink(x): raise RuntimeError("shrink")\n', 'Generator(lambda rng: 1, shrink)'),
        ]:
            body = header + active.replace('    round_trip("valid")',
                    '    round_trip("valid")\n    raise AssertionError("violation")')
            body += extra + f'properties = {{"broken": ({gen}, check)}}\n'
            result = case(name, body, 2, '--expect-fail', 'broken')
            self.ensure(result['result'] == 'error' and not result['ok'],
                        f'{name}: infrastructure error never satisfies --expect-fail')

        # Exercise nested generators and shrinkers against a real repo call.
        module = load_module(checker, 'example_gen_check')
        generators = [module.ints(1, 20), module.text(' ab', 1, 8),
                      module.lists(module.ints(), 1, 4), module.tuples(module.ints(), module.text()),
                      module.dicts(module.ints(), module.text(), 1, 4),
                      module.one_of(module.ints(), module.text()), module.sampled_from([1, 2, 3])]
        import random
        for gen in generators:
            for _ in range(10):
                value = gen.draw(random.Random(_))
                list(gen.shrink(value))
        self.ensure(True, 'all seven combinators generate and shrink bounded nested inputs')

    def formal_time_limits(self):
        """Hung calls and slow runs end in a report, not a killed shell; filter_too_much never hides a result."""
        bounded = self.script(self.name, 'bounded_check.py')
        hang = self.art/'hang_model.py'
        hang.write_text('def init(): return 0\n'
                        'def actions(s):\n    while s == 1:\n        pass\n    return [("step", s + 1)] if s < 3 else []\n'
                        'invariants = {"ok": lambda s: True}\n')
        result = self.json(bounded, hang, '--call-timeout', '1', '--json', expected=2, timeout=30)
        v = result['violation']
        self.ensure(v['kind'] == 'error' and v['name'] == 'actions' and v['detail'] == 'no return within 1 s'
                    and [step['state'] for step in v['trace']] == [0, 1],
                    'an actions() call that never returns on the second state is a model error with the two-state trace to it')
        counter = self.art/'counter_model.py'
        # A 1 ms step keeps the 2 s search to about 2000 states instead of a million held in memory.
        counter.write_text('import time\ndef init(): return 0\n'
                           'def actions(s):\n    time.sleep(0.001)\n    return [("inc", s + 1)]\n'
                           'invariants = {"nonnegative": lambda s: s >= 0}\nDEPTH = MAX_STATES = 10**9\n')
        output = self.run(bounded, counter, '--timeout', '2', expected=3, timeout=30)
        self.ensure(re.search(r'INCOMPLETE: stopped at the 2 s limit after [1-9]\d* states', output)
                    and output.index('2 s run limit') < output.index('INCOMPLETE'),
                    'a search over a billion-state bound stops at the 2 s run limit as inconclusive, after printing its bounds')

        checker = self.script(self.name, 'gen_check.py')
        source = self.repo/'todo/text.py'
        self.ensure(not source.exists(), 'scratch looping module does not shadow a real todo module')
        props = self.art/'props_strip_leading.py'
        props.write_text('from gen_check import text\nfrom todo.text import strip_leading\n'
                         'TARGETS = ["todo/text.py:strip_leading"]\n'
                         'properties = {"strip_leading": (text(" ab", max_size=8), lambda t: strip_leading(t) == t.lstrip(" ")),\n'
                         '              "no_leading_space": (text("ab", max_size=8), lambda t: strip_leading(t) == t)}\n')
        try:
            # The scratch break: the loop never advances i, so a leading space never returns.
            source.write_text('def strip_leading(t):\n    i = 0\n    while i < len(t) and t[i] == " ":\n'
                              '        pass\n    return t[i:]\n')
            command = [checker, props, '--repo', self.repo, '--call-timeout', '1']
            result = self.json(*command, '--json', expected=3, timeout=30)
            hung = result['properties']['strip_leading']
            import ast
            self.ensure(hung['result'] == 'timeout' and ast.literal_eval(hung['input']).startswith(' ')
                        and hung['detail'] == 'no return within 1 s' and result['seed'] == 0 and not result['ok'],
                        'a check that never returns ends its property as timeout with the leading-space input and seed 0')
            self.ensure(result['properties']['no_leading_space']['result'] == 'pass' and not result['missing_targets'],
                        'the other property in the same file still runs and passes after the timeout')
            result = self.json(*command, '--expect-fail', 'strip_leading', '--json', expected=3, timeout=30)
            self.ensure(result['properties']['strip_leading']['result'] == 'timeout' and not result['ok'],
                        '--expect-fail never accepts a timeout as the expected counterexample')
            output = self.run(*command, expected=3, timeout=30)
            self.ensure(output.startswith('gen_check: seed 0;') and f"input: {hung['input']}" in output
                        and 'INCOMPLETE' in output,
                        'text mode prints the seed before checking and the timed-out input in its report')
        finally:
            source.unlink(missing_ok=True)

        # Two rejections, then a failing input: 2 x 2 > 3 must not turn the failure into filter_too_much.
        rare = self.art/'props_filter_then_fail.py'
        rare.write_text('from gen_check import *\nTARGETS = ["todo/store.py:Store.add"]\n'
                        'from todo_props import reset, round_trip\n'
                        '_values = [-1, -2, 60]\n'
                        'def draw(rng):\n    return _values.pop(0) if _values else 0\n'
                        'def check(x):\n    if x < 0:\n        reject()\n    round_trip("valid")\n    assert x <= 50, "above 50"\n'
                        'properties = {"rare": (Generator(draw, lambda x: []), check)}\n')
        result = self.json(checker, rare, '--repo', self.repo, '--json', expected=1, timeout=30)
        found = result['properties']['rare']
        self.ensure(found['result'] == 'fail' and found['counterexample'] == '60'
                    and (found['rejected'], found['generated']) == (2, 3) and not result['missing_targets'],
                    'a counterexample found after two rejected inputs reports fail with 60, not filter_too_much')
        self.json(checker, rare, '--repo', self.repo, '--expect-fail', 'rare', '--json', timeout=30)
        output = self.run(checker, rare, '--repo', self.repo, expected=1, timeout=30)
        self.ensure('rare: fail (2 of 3 generated inputs rejected before the counterexample)' in output,
                    '--expect-fail accepts that counterexample, and the report counts the rejections before it')

    def docs(self):
        """Mutate a disposable collection; keep doc checks under curator's key."""
        root = self.scratch/'docs'
        for directory in ('skills', 'scripts'):
            shutil.copytree(ROOT/directory, root/directory, ignore=shutil.ignore_patterns('__pycache__'))
        (root/'research').mkdir()
        shutil.copyfile(ROOT/'research/NOTES.md', root/'research/NOTES.md')
        shutil.copytree(ROOT/'research/upstream', root/'research/upstream')
        for file in ('README.md', 'MAPPING.md', 'install.sh'):
            shutil.copyfile(ROOT/file, root/file)
        # README also links its builder; helper inventory includes examples/.
        shutil.copytree(ROOT/'examples', root/'examples',
                        ignore=shutil.ignore_patterns('todo-app', '__pycache__'))
        module = load_module(ROOT/'scripts/check_docs.py', 'example_docs')
        checker = ROOT/'scripts/check_docs.py'
        self.run(checker, '--repo', root)
        self.ensure(True, 'clean copied docs pass the standalone documentation checker')

        def changed(file, transform, expected, description):
            path = root/file
            original = path.read_text()
            try:
                path.write_text(transform(original))
                issues = module.check(root)
                self.ensure(not issues if expected is None else any(expected in issue for issue in issues), description)
                if expected is not None:
                    self.ensure(all(re.match(r'[^:]+:\d+: ', issue) for issue in issues),
                                description + ': diagnostics include file and line')
            finally:
                path.write_text(original)

        mine = 'python3 $S/correct/scripts/mine_mistakes.py .'
        changed('README.md', lambda t: t.replace(mine, mine+' --since "90 days ago"'), 'README policy avoids --since',
                'reintroducing README\'s retired --since recommendation fails its explicit policy')
        changed('README.md', lambda t: t.replace('--mutants', '--renamed-mutants'), 'unsupported flag --renamed-mutants',
                'a renamed helper flag fails against its real --help')
        changed('README.md', lambda t: t.replace('(#credits)', '(#missing-heading)'), 'unknown heading anchor',
                'a dead heading link fails')
        changed('README.md', lambda t: t+'\n[dead](README.md#absent "title")\n', 'unknown heading anchor',
                'inline links with titles still validate their destination')
        changed('README.md', lambda t: t+'\n[dead](<missing-file.md> "title")\n', 'broken relative link',
                'angle destinations with titles still validate their destination')
        changed('README.md', lambda t: t.replace('all eight skills', 'all nine skills'), 'skill count claims 9',
                'a stale whole-set skill count fails against the tree')
        changed('README.md', lambda t: t+'\nThe sources S1-S22 cover the collection.\n', 'source range differs',
                'an explicit source range must match the entire NOTES inventory')
        changed('skills/sharpen/SKILL.md', lambda t: t+'\nS7 and S999\n', 'unknown source citation: S999',
                'a valid source alongside an unknown source does not hide the unknown citation')
        changed('MAPPING.md', lambda t: t+'\nS99\n', 'unknown source citation: S99',
                'mapping citations are validated too')
        changed('skills/sharpen/SKILL.md', lambda t: re.sub(r'\bS\d+\b', 'source', t), 'missing source ID',
                'every skill still needs at least one source citation')
        changed('README.md', lambda t: t.replace(mine, mine+' --json'), None,
                'a supported README-only flag absent from skill examples remains allowed')
        changed('skills/correct/SKILL.md', lambda t: t.replace('mine_mistakes.py --since -->', 'mine_mistakes.py --sinc -->'),
                'README policy names unsupported flag', 'a typo in the explicit recommendation policy fails too')
        changed('README.md', lambda t: t.replace(mine, mine+' -- --application-flag'), None,
                'flags after -- are outside the helper\'s option contract')
        changed('README.md', lambda t: t.replace(mine, mine+' && other-tool --unrelated-flag'), None,
                'shell separators keep flags associated with their own command')
        changed('README.md', lambda t: t.replace(mine, mine+' --feedback "--literal-payload"'), None,
                'an option\'s literal value is not mistaken for a helper flag')
        changed('skills/correct/SKILL.md', lambda t: t.replace('$P --test', '$P --renamed-test'), 'unsupported flag --renamed-test',
                'literal command variables are expanded without executing examples')
        changed('skills/correct/SKILL.md', lambda t: t+'\n```bash\npython3 $SKILL_DIR/scripts/rule_table.py check AGENTS.md --rule text\n```\n',
                'unsupported flag --rule', 'subcommand flags are checked against check help, not add help')
        changed('skills/correct/SKILL.md', lambda t: t+'\n`mine_mistakes.py . --nonexistent`\n',
                'unsupported flag --nonexistent', 'bare inline helper commands resolve within their owning skill')
        changed('skills/correct/SKILL.md', lambda t: t+'\n```bash\nD=$SKILL_DIR\npython3 $D/scripts/missing.py --help\n```\n',
                'missing script', 'helper paths expanded from literal directory variables are validated')
        changed('skills/correct/SKILL.md', lambda t: t.replace('mine_mistakes.py', 'missing-helper.py'), 'missing script',
                '$SKILL_DIR paths resolve to the owning skill')
        changed('README.md', lambda t: t.replace('$S/correct/scripts/mine_mistakes.py', '$S/correct/scripts/missing.py'),
                'missing script', '$S/skill paths resolve to the named skill')
        changed('skills/feedback-to-architecture/SKILL.md', lambda t: t+'\n`$SKILL_DIR/../correct/scripts/missing.py`\n',
                'missing script', 'sibling skill paths resolve from the current skill')
        changed('README.md', lambda t: t+'\n`correct/scripts/missing.py`\n', 'missing script',
                'explicit skill/scripts paths resolve to that skill')
        changed('README.md', lambda t: t+'\n`absent-skill/scripts/missing.py`\n', 'missing script',
                'a helper path naming a nonexistent skill fails')
        changed('skills/curator/SKILL.md', lambda t: t+'\n` scripts/missing.py `\n', 'missing script',
                'bare scripts paths resolve at the collection root')
        changed('skills/feedback-to-architecture/SKILL.md', lambda t: t+'\n```bash\npython3 $SKILL_DIR/../correct/scripts/patlint.py --help\n```\n',
                None, 'valid sibling helpers are recognized')
        changed('README.md', lambda t: t+'\n```bash\nverify-demo/scripts/control.sh --generated-flag\npython3 tools/patlint.py --vendored-flag\nverify-<app>/scripts/control.sh --placeholder\n```\n',
                None, 'generated harnesses, placeholders and vendored commands are outside collection contracts')
        changed('README.md', lambda t: t+'\n## Café 文!\n## Café 文!\n## Heading `code`\n\n[one](#café-文) [two](#café-文-1) [code](#heading-code)\n[title](README.md "title") [angle](<README.md>)\n[ref][docs] [docs][] [docs]\n\n[docs]: <README.md#credits> "title"\n',
                None, 'Unicode and duplicate heading anchors, code headings, titled/angle links and references resolve')
        changed('README.md', lambda t: t+'\n[bad][undefined]\n', 'undefined link reference',
                'undefined explicit reference links fail')
        changed('README.md', lambda t: t+'\n[remote helper](https://example.invalid/scripts/helper.py)\n', None,
                'remote URLs containing scripts paths are not local file references')
        changed('README.md', lambda t: t+'\n```bash\npython3 scripts/check_docs.py --repo "$(touch SHOULD_NOT_EXIST)"\n```\n', None,
                'documentation command substitutions are never executed')
        self.ensure(not (root/'SHOULD_NOT_EXIST').exists(), 'shell snippets have no side effects')

        notes = root/'research/NOTES.md'
        original = notes.read_text()
        try:
            notes.write_text(original+'\n| S24 | Local fixture source | checked |\n')
            changed('skills/sharpen/SKILL.md', lambda t: re.sub(r'\bS\d+\b', 'S24', t), None,
                    'adding S24 to NOTES allows a skill citing only S24 without editing checker code')
            changed('README.md', lambda t: t+'\nSources S1-S23\n', 'source range differs',
                    'adding a source also invalidates any stale explicit whole-set range')
        finally:
            notes.write_text(original)
        self.ensure(not module.check(root), 'all scratch mutations restore to clean documentation')

    def curator(self):
        with self.case('curator'):
            self.docs()
            # Populate a fake cached upstream directly: git init only, no remotes or fetches.
            cache = self.scratch/'cache'
            upstream = cache/'cursor-plugins'
            (upstream/'pstack/skills').mkdir(parents=True)
            for src in (ROOT/'research/upstream').rglob('*'):
                if not src.is_file():
                    continue
                rel = src.relative_to(ROOT/'research/upstream')
                if src.name=='PSTACK_LICENSE':
                    dest = upstream/'pstack/LICENSE'
                elif len(rel.parts)==1 and rel.name[:2].isdigit():
                    dest = upstream/'pstack/docs/guide'/rel
                else:
                    dest = upstream/'pstack/skills'/rel
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(src,dest)
            target = upstream/'pstack/skills/correct/SKILL.md'
            target.write_text(target.read_text()+'\nOffline fixture: prefer checks that fail on real mistakes.\n')
            new = upstream/'pstack/skills/local-new/SKILL.md'
            new.parent.mkdir()
            new.write_text('---\nname: local-new\ndescription: Use for local evidence.\n---\nA local-only upstream fixture.\n')
            self.run('git','init','-q','--initial-branch=main','--template=',upstream)
            self.run('git','-C',upstream,'add','-A')
            self.run('git','-C',upstream,'-c','user.name=example-dev','-c','user.email=dev@example.local',
                     '-c','commit.gpgsign=false','commit','-qm','offline upstream fixture')
            env = dict(self.env,POTETO_CACHE=str(cache),POTETO_UPSTREAM_URL=str(upstream),
                       GIT_ALLOW_PROTOCOL='file')
            diff = self.json(self.script(self.name,'upstream_diff.sh'),'--repo',ROOT,'--offline','--json',env=env)
            self.ensure(any(c['file']=='correct/SKILL.md' for c in diff['changed']), 'offline diff detects a changed upstream skill against the snapshot')
            self.ensure(any(c['name']=='local-new' for c in diff['new_skills']), 'first offline diff reports a new skill absent from the snapshot')
            self.ensure(not diff['missing_upstream'], 'fake upstream maps every snapshotted source path correctly')
            self.save('upstream-diff.json',diff)
            # A skill reported by the first run is not new again; one added between runs is.
            added = upstream/'pstack/skills/local-added/SKILL.md'
            added.parent.mkdir()
            added.write_text('---\nname: local-added\ndescription: Use for a skill added between runs.\n---\nAdded after the first diff.\n')
            self.run('git','-C',upstream,'add','-A')
            self.run('git','-C',upstream,'-c','user.name=example-dev','-c','user.email=dev@example.local',
                     '-c','commit.gpgsign=false','commit','-qm','add a skill between curator runs')
            # Redirect curator outputs to a disposable collection, including its log.
            collection = self.scratch/'collection'
            shutil.copytree(ROOT/'research',collection/'research')
            shutil.copytree(ROOT/'skills',collection/'skills')
            self.run(self.script(self.name,'curate.sh'),'--propose','--offline','--repo',collection,'--date','2026-10-19',env=env)
            proposal = collection/'proposals/2026-10-19.md'
            log = collection/'logs/curator/2026-10-19.log'
            self.ensure(proposal.is_file() and log.is_file(), 'offline curate writes a proposal and execution log')
            self.ensure('correct/SKILL.md' in proposal.read_text() and 'local-added' in proposal.read_text(), 'proposal includes changed and new upstream evidence with source URLs')
            rerun = json.loads((collection/'logs/curator/2026-10-19.upstream.json').read_text())
            self.ensure([n['name'] for n in rerun['new_skills']] == ['local-added'] and 'local-new' in rerun['untracked_skills']
                        and 'New upstream skill: local-new' not in proposal.read_text(),
                        'a later run proposes only skills new since the previous run, not every untracked skill again')
            self.json(self.script(self.name,'check_proposals.py'),proposal,'--skills-dir',collection/'skills','--json')
            self.copy(proposal)
            self.copy(log,'curator.log')
            self.copy(collection/'logs/curator/2026-10-19.upstream.json')
            empty_env = dict(env,POTETO_CACHE=str(self.scratch/'empty-cache'))
            self.run(self.script(self.name,'curate.sh'),'--propose','--offline','--repo',collection,'--date','2026-10-20',env=empty_env,expected=1)
            self.ensure(not (collection/'proposals/2026-10-20.md').exists(), 'failed upstream diff cannot produce a false clean proposal')
            malformed = self.art / 'malformed-proposal.md'
            malformed.write_text('# Curator proposals\nUnstructured text with no sources.\n')
            self.run(self.script(self.name,'check_proposals.py'),malformed,expected=1)
            self.ensure(True, 'unstructured proposal text cannot pass as an empty proposal list')
            module = load_module(self.script(self.name,'check_proposals.py'),'example_proposals')
            for status in (401,403,429):
                module.fetch_status = lambda url, status=status: status
                result = module.check(proposal,collection/'skills',True)
                self.ensure(not result['ok'], f'HTTP {status} fails the fetched-source acceptance gate (stubbed transport, no network)')
            self.ensure(not self.run('git','-C',upstream,'remote').strip(), 'local upstream fixture has no git remotes')

    def execute(self):
        print(f'Fresh scratch: {self.scratch}',flush=True)
        try:
            for check in [self.create,self.maintain,self.correct,self.feedback,self.architect,self.sharpen,self.formal,self.curator]:
                check()
        finally:
            (self.logs/'summary.json').write_text(json.dumps(self.summary,indent=2)+'\n')
            if not self.keep:
                shutil.rmtree(self.scratch)
        return 0 if all(s['status']=='pass' for s in self.summary.values()) else 1

    def review_regressions(self, output):
        """Run follow-up regressions independently so every pre-fix failure is recorded."""
        output.mkdir(parents=True, exist_ok=True)
        methods = [self.review_deadline_order, self.review_mutant_deadline, self.review_mutant_json,
                   self.review_mutant_errors, self.review_sigint, self.review_fixture_limits, self.review_phantom_routes,
                   self.review_baseline_retry, self.review_git_warning, self.review_docs,
                   self.review_import_budget, self.review_go_receivers, self.review_shrink_call_timeout]
        methods += [self.review_synchronous_deadlines, self.review_path_rebindings]
        methods += [self.review_property_handoff, self.review_path_branches, self.review_git_fail_closed,
                    self.review_call_timeout_exit, self.review_oneshot_timers, self.review_disabled_limits]
        results = {}
        try:
            with (output / 'run.log').open('w') as self.log:
                self.run(ROOT / 'examples/build-todo-app.sh', self.repo, cwd=ROOT)
                for method in methods:
                    self.name = method.__name__
                    self.summary[self.name] = {'checks': []}
                    self.art = self.scratch / 'review-artifacts' / self.name
                    self.art.mkdir(parents=True)
                    try:
                        method()
                    except Exception as error:
                        results[self.name] = {'status': 'fail', 'error': f'{type(error).__name__}: {error}'}
                        traceback.print_exc(file=self.log)
                    else:
                        results[self.name] = {'status': 'pass', 'assertions': len(self.summary[self.name]['checks'])}
                    print(f"{results[self.name]['status'].upper()} {self.name}", flush=True)
                (output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
        finally:
            if not self.keep:
                shutil.rmtree(self.scratch)
        return int(any(r['status'] != 'pass' for r in results.values()))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--keep-scratch',action='store_true',help='keep the isolated scratch directory for diagnosis')
    ap.add_argument('--review-regressions', type=Path, metavar='OUT', help='run follow-up review regressions and save independent results in OUT')
    args = ap.parse_args()
    examples = Examples(args.keep_scratch)
    return (examples.review_regressions(args.review_regressions.resolve()) if args.review_regressions
            else examples.execute())


if __name__=='__main__':
    raise SystemExit(main())
