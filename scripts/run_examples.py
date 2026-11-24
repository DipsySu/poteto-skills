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
from unittest.mock import call, patch

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

    def child_pids(self, parent):
        # Separate -o options are strictly POSIX; `--ppid` is GNU-only.
        rows = (row.split() for row in self.run('ps', '-A', '-o', 'pid=', '-o', 'ppid=', json_stdout=True).splitlines())
        return [r[0] for r in rows if len(r) == 2 and r[1] == str(parent)]

    def wrapped_cleanup(self):
        with patch.object(self, 'run', return_value='101 42\n102 420\n103 142\n42 1\n104 42\n') as ps:
            self.ensure(self.child_pids(42) == ['101', '104'],
                        'child_pids selects only exact PPID matches')
            self.ensure(ps.call_args_list == [call('ps', '-A', '-o', 'pid=', '-o', 'ppid=', json_stdout=True)],
                        'child_pids requests PID and PPID with separate POSIX -o options')
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
            leaf = subprocess.Popen(['sleep', '30'])
            try:
                self.ensure(not self.child_pids(leaf.pid), 'child_pids finds no children of a leaf process')
            finally:
                leaf.kill()
                leaf.wait()
            self.ensure(self.child_pids(launch['pid']), 'wrapper launch runs the server as a child of the recorded PID')
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
            self.review_match_case_paths()
            self.review_expression_paths()
            self.review_exception_path_provenance()
            self.review_with_item_provenance()
            self.review_registered_boolean_actions()
            self.review_parser_action_bindings()
            self.review_parser_helper_defaults()
            self.review_parser_constructor_arguments()
            self.review_query_path_aliases()
            self.review_go_receiver_workload()
            self.review_route_workload()
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

    def review_match_case_paths(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'case_path_scan')
        prefix = 'class Handler:\n    def do_GET(self):\n'
        probe = 'if path.startswith("/var/www/"): pass'
        negatives = [
            'path = filesystem\nmatch mode:\n    case "url":\n        path = self.path\n'
            '    case "disk":\n        ' + probe,
            'path = filesystem\nmatch mode:\n    case "url" if allowed:\n        path = self.path\n'
            '    case _:\n        ' + probe,
            'path = filesystem\nmatch mode:\n    case "url":\n        path = self.path\n' + probe,
            'path = self.path\nmatch mode:\n    case "disk":\n        path = filesystem\n' + probe,
            'path = filesystem\nmatch mode:\n    case "url":\n        if allowed:\n'
            '            path = self.path\n        ' + probe,
            'path = filesystem\nmatch mode:\n    case "url":\n        path = self.path\n'
            '    case "disk":\n        match detail:\n            case _:\n                ' + probe,
            'path = self.path\nmatch filesystem:\n    case path if False: pass\n'
            '    case _:\n        ' + probe,
            'path = self.path\nmatch mode:\n    case "disk" if (path := filesystem) and False: pass\n'
            '    case _:\n        ' + probe,
            'path = filesystem\nmatch mode:\n    case "url" if (path := self.path) and False: pass\n'
            '    case _:\n        ' + probe,
        ]
        positives = [
            'match mode:\n    case "url":\n        path = self.path\n        ' + probe,
            'path = self.path\nmatch mode:\n    case "disk":\n        path = filesystem\n'
            '    case "url":\n        ' + probe,
            'path = self.path\nmatch mode:\n    case {"disk": path}: pass\n'
            '    case "url":\n        ' + probe,
            'match mode:\n    case "disk":\n        path = filesystem\npath = self.path\n' + probe,
            'match mode:\n    case "url" if (path := self.path):\n        ' + probe,
            'path = self.path\nmatch mode:\n    case "disk" if allowed:\n        path = filesystem\n'
            '    case _:\n        ' + probe,
        ]
        failures = []
        for expected, sources in ((set(), negatives), ({('GET', '/var/www/<*>')}, positives)):
            for source in sources:
                body = prefix + ''.join('        ' + line + '\n' for line in source.splitlines())
                info = scanner.analyze_python('server.py', body)
                routes = {(r['method'], r['path']) for r in info['routes']}
                if routes != expected or info['notes']:
                    failures.append((source, sorted(routes), info['notes']))
        self.ensure(not failures, f'match cases keep exclusive bindings separate and revoke uncertain joins: {failures}')

    def review_route_workload(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'route_work_scan')
        original_children, original_compares = scanner.ast.iter_child_nodes, scanner._compares_path
        samples = []
        failures = []
        for path_prefix in ('/r', '/var/r'):
            for count in (20, 60, 120):
                metrics = {'prefix': path_prefix, 'n': count, 'ast_edges': 0, 'compares_path': 0}

                def children(node):
                    for child in original_children(node):
                        metrics['ast_edges'] += 1
                        yield child

                def compares(node):
                    metrics['compares_path'] += 1
                    return original_compares(node)

                source = 'class Handler:\n    def do_GET(self):\n        path = self.path\n'
                source += ''.join(f'        if path == "{path_prefix}{i}": pass\n' for i in range(count))
                started = time.monotonic()
                with patch.object(scanner.ast, 'iter_child_nodes', children), patch.object(scanner, '_compares_path', compares):
                    info = scanner.analyze_python('server.py', source)
                metrics['seconds'] = round(time.monotonic() - started, 6)
                samples.append(metrics)
                expected = {('GET', f'{path_prefix}{i}') for i in range(count)}
                if {(r['method'], r['path']) for r in info['routes']} != expected or info['notes']:
                    failures.append(('routes', metrics))
        self.log.write('ROUTE WORKLOAD ' + json.dumps(samples) + '\n')
        self.log.flush()
        for first, last in ((samples[0], samples[2]), (samples[3], samples[5])):
            if last['ast_edges'] > 8 * first['ast_edges']:
                failures.append(('sixfold route growth exceeds near-linear AST work', first, last))
        self.ensure(not failures, f'ordinary and filesystem-like routes retain exact inventory with near-linear AST work: {failures}')

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
        # Inherited GIT_DIR no longer selects the checkout. Break the actual
        # checkout marker so this still exercises a genuine git lookup failure.
        (root / '.git').rename(root / 'saved.git')
        (root / '.git').write_text('gitdir: missing.git\n')
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

    def review_git_case_aliases(self):
        """Filesystem identity, not spelling, selects a checkout and its index."""
        root = self.scratch / 'review-Git-Case'
        root.mkdir()
        alias = root.with_name(root.name.swapcase())
        if not alias.exists() or not alias.samefile(root):
            self.log.write('SKIP case-alias probe: scratch filesystem is case-sensitive\n')
            self.ensure(True, 'case-alias regression skips on a case-sensitive filesystem')
            return
        clean_env = {k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')}
        self.run('git', 'init', '-q', cwd=root, env=clean_env)
        (root / 'src').mkdir()
        (root / 'src/tracked.py').write_text('BAD\n')
        self.run('git', 'add', 'src/tracked.py', cwd=root, env=clean_env)
        rules = self.save('case-rules.json', {'rules': [
            {'id': 'probe', 'regex': 'BAD', 'include': ['*.py'], 'message': 'Use good() instead.'}]})
        failures = []
        for target in (alias, alias / 'src', alias / 'src/tracked.py',
                       root / 'SRC', alias / 'SRC', alias / 'SRC/tracked.py'):
            for explicit_root in ((), ('--root', str(alias))):
                baseline = self.art / 'case-baseline.json'
                baseline.unlink(missing_ok=True)
                command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules), '--json',
                           *explicit_root, '--baseline', str(baseline), '--update-baseline', str(target)]
                result = subprocess.run(command, cwd=root, env=clean_env, capture_output=True,
                                        text=True, timeout=10)
                self.log.write(f'case alias target={target} explicit_root={bool(explicit_root)} '
                               f'exit={result.returncode}\n' + result.stdout + result.stderr)
                report = json.loads(result.stdout) if result.stdout.strip().startswith('{') else {}
                counts = json.loads(baseline.read_text())['counts'] if baseline.exists() else None
                if (result.returncode != 0 or result.stderr or report.get('files_scanned') != 1
                        or report.get('untracked') != [] or counts != {'probe': {'src/tracked.py': 1}}
                        or [v['path'] for v in report.get('violations', [])] != ['src/tracked.py']):
                    failures.append((str(target), bool(explicit_root), result.returncode,
                                     result.stderr, report, counts))
        self.ensure(not failures,
                    f'case aliases of a checkout scan directory/file arguments and adopt tracked baseline keys consistently: {failures}')

    def review_git_inherited_environment(self):
        root = self.scratch / 'review-git-inherited'
        root.mkdir()
        clean_env = {k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')}
        self.run('git', 'init', '-q', cwd=root, env=clean_env)
        (root / 'src').mkdir()
        (root / 'src/tracked.py').write_text('BAD\n')
        (root / '.gitignore').write_text('ignored.py\n')
        self.run('git', 'add', '.', cwd=root, env=clean_env)
        self.run('git', '-c', 'user.name=Regression', '-c', 'user.email=regression@example.invalid',
                 'commit', '-qm', 'fixture', cwd=root, env=clean_env)
        linked = self.scratch / 'review-git-linked'
        self.run('git', 'worktree', 'add', '--detach', str(linked), 'HEAD', cwd=root, env=clean_env)
        rules = self.save('inherited-rules.json', {'rules': [
            {'id': 'probe', 'regex': 'BAD', 'include': ['*.py'], 'message': 'Use good() instead.'}]})
        failures = []
        for checkout in (root, linked):
            (checkout / 'src/ignored.py').write_text('BAD\n')
            (checkout / 'src/new.py').write_text('GOOD\n')
            nested = checkout / 'src/nested'
            nested.mkdir()
            self.run('git', 'init', '-q', cwd=nested, env=clean_env)
            (nested / 'nested.py').write_text('BAD\n')
            self.run('git', 'add', 'nested.py', cwd=nested, env=clean_env)
            git_dir = self.run('git', 'rev-parse', '--absolute-git-dir', cwd=checkout, env=clean_env).strip()
            variants = [dict(GIT_DIR=git_dir), dict(GIT_DIR=os.path.relpath(git_dir, checkout.resolve())),
                        dict(GIT_DIR=git_dir, GIT_WORK_TREE=str(nested))]
            for inherited in variants:
                env = dict(clean_env, **inherited)
                for target in ('src/tracked.py', 'src'):
                    for explicit_root in ((), ('--root', str(checkout)), ('--root', '.')):
                        baseline = self.art / 'inherited-baseline.json'
                        baseline.unlink(missing_ok=True)
                        command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                                   '--json', *explicit_root, '--baseline', str(baseline), '--update-baseline', target]
                        result = subprocess.run(command, cwd=checkout, env=env, capture_output=True,
                                                text=True, timeout=10)
                        self.log.write(f'checkout={checkout.name} inherited={inherited} target={target} '
                                       f'explicit_root={bool(explicit_root)} exit={result.returncode}\n'
                                       + result.stdout + result.stderr)
                        report = json.loads(result.stdout) if result.stdout.strip().startswith('{') else {}
                        counts = json.loads(baseline.read_text())['counts'] if baseline.exists() else None
                        expected_untracked = ['src/new.py'] if target == 'src' else []
                        if (result.returncode != 0 or result.stderr or report.get('untracked') != expected_untracked
                                or report.get('files_scanned') != (2 if target == 'src' else 1)
                                or counts != {'probe': {'src/tracked.py': 1}}
                                or report.get('root') != str(checkout.resolve())):
                            failures.append((checkout.name, inherited, target, bool(explicit_root),
                                             result.returncode, result.stderr, report, counts))
            # An explicit nested file is linted as given, but its own index must
            # not make it eligible for this checkout's ratchet baseline.
            baseline = self.art / 'nested-baseline.json'
            baseline.unlink(missing_ok=True)
            result = subprocess.run([str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                                     '--root', str(checkout), '--baseline', str(baseline), '--update-baseline',
                                     '--json', str(nested / 'nested.py')], cwd=checkout,
                                    env=dict(clean_env, GIT_DIR=git_dir), capture_output=True, text=True, timeout=10)
            self.log.write(result.stdout + result.stderr)
            report = json.loads(result.stdout) if result.stdout.strip().startswith('{') else {}
            if (result.returncode != 1 or report.get('untracked') != ['src/nested/nested.py']
                    or not baseline.exists() or json.loads(baseline.read_text())['counts'] != {}):
                failures.append(('nested index adopted', checkout.name, result.returncode, report))
        outside = self.scratch / 'review-inherited-non-git'
        outside.mkdir()
        (outside / 'clean.py').write_text('GOOD\n')
        for cwd in (root, outside):
            for target in (outside, outside / 'clean.py'):
                result = subprocess.run([str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                                         '--json', str(target)], cwd=cwd,
                                        env=dict(clean_env, GIT_DIR=str(root / '.git'), GIT_WORK_TREE=str(root)),
                                        capture_output=True, text=True, timeout=10)
                self.log.write(result.stdout + result.stderr)
                report = json.loads(result.stdout) if result.stdout.strip().startswith('{') else {}
                if (result.returncode != 0 or result.stderr or report.get('files_scanned') != 1
                        or report.get('untracked') != []):
                    failures.append(('non-git fallback', cwd.name, target.name, result.returncode, result.stderr, report))
        self.ensure(not failures,
                    f'inherited absolute/relative GIT_DIR and GIT_WORK_TREE cannot change root, tracking, ignores or baseline eligibility in ordinary/linked checkouts: {failures}')

    def review_git_locale(self):
        root = self.scratch / 'review-git-locale'
        root.mkdir()
        (root / 'clean.py').write_text('GOOD\n')
        rules = self.save('locale-rules.json', {'rules': [
            {'id': 'probe', 'regex': 'BAD', 'include': ['*.py'], 'message': 'Use good() instead.'}]})
        fake_bin = self.scratch / 'review-translated-git'
        fake_bin.mkdir()
        fake_git = fake_bin / 'git'
        fake_git.write_text('#!/bin/sh\nprintf "%s\\n" "致命错误：不是 Git 仓库（或者任何父目录）：.git" >&2\nexit 128\n')
        fake_git.chmod(0o755)
        env = {k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')}
        env['LC_ALL'] = 'zh_CN.UTF-8'
        translated_env = dict(env, PATH=str(fake_bin) + os.pathsep + env['PATH'])
        failures = []
        # Real git reproduces localized installations; the stub keeps the test
        # effective on machines whose git lacks a Chinese message catalog.
        for provider, command_env in (('real', env), ('translated', translated_env)):
            for target in (root, root / 'clean.py'):
                for explicit_root in ((), ('--root', str(root))):
                    command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                               '--json', *explicit_root, str(target)]
                    result = subprocess.run(command, cwd=root, env=command_env,
                                            capture_output=True, text=True, timeout=10)
                    self.log.write(f'locale=zh_CN.UTF-8 provider={provider} target={target.name} '
                                   f'explicit_root={bool(explicit_root)} exit={result.returncode}\n'
                                   + result.stdout + result.stderr)
                    report = json.loads(result.stdout) if result.returncode == 0 else {}
                    if result.returncode != 0 or result.stderr or report.get('files_scanned') != 1:
                        failures.append((provider, target.name, bool(explicit_root),
                                         result.returncode, result.stderr, report))
        # The same translated failure in a checkout must never authorize a
        # filesystem scan or a baseline update, including linked-worktree markers.
        checkout = self.scratch / 'review-locale-worktree'
        checkout.mkdir()
        (checkout / '.git').write_text('gitdir: missing.git\n')
        (checkout / 'hit.py').write_text('BAD\n')
        baseline = checkout / 'baseline.json'
        original = '{"counts": {}}\n'
        baseline.write_text(original)
        for target in (checkout, checkout / 'hit.py'):
            command = [str(self.script('correct', 'patlint.py')), '--rules', str(rules),
                       '--root', str(checkout), '--baseline', str(baseline), '--update-baseline', str(target)]
            result = subprocess.run(command, cwd=checkout, env=translated_env,
                                    capture_output=True, text=True, timeout=10)
            self.log.write(f'locale=zh_CN.UTF-8 checkout exit={result.returncode}\n' + result.stdout + result.stderr)
            if (result.returncode != 2 or 'refusing filesystem fallback' not in result.stderr
                    or baseline.read_text() != original):
                failures.append(('checkout', target.name, result.returncode, result.stderr, baseline.read_text()))
        self.ensure(not failures,
                    f'non-English git errors fall back quietly only outside checkouts, preserving baseline failures: {failures}')

    def review_git_warning(self):
        import contextlib
        import io
        lint = load_module(self.script('correct', 'patlint.py'), 'recheck_patlint')
        root = self.scratch / 'review-git-fallback'
        root.mkdir()
        (root / 'hit.py').write_text('BAD\n')
        for error in (FileNotFoundError('git unavailable'),
                      subprocess.CompletedProcess(['git'], 128, b'', b'fatal: dubious ownership')):
            if isinstance(error, subprocess.CompletedProcess):
                # Dubious ownership describes a checkout; outside one all git
                # failures now fall back without depending on translated text.
                (root / '.git').mkdir()
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
            excuse_repo = self.scratch / 'excuse-comments'
            self.run('git', 'clone', '--quiet', '--no-hardlinks', self.repo, excuse_repo)
            excuse = "# for now, write rows directly; Store.add's lock is too slow"
            rewrites = [
                ('todo/api.py', 'self.reply(201, self.server.store.add(body.get("title")))',
                 'store = self.server.store\n                rows = store.list_all()\n'
                 '                row = {"id": len(rows) + 1, "title": body.get("title"), "status": "open"}\n'
                 '                rows.append(row)\n                ' + excuse + '\n'
                 '                store.path.write_text(json.dumps(rows))\n                self.reply(201, row)'),
                ('todo/__main__.py', 'result = store.add(args.title)',
                 'rows = store.list_all()\n            result = {"id": len(rows) + 1, "title": args.title, "status": "open"}\n'
                 '            rows.append(result)\n            ' + excuse + '\n'
                 '            store.path.write_text(json.dumps(rows))'),
            ]
            expected_refs = set()
            for path, before, after in rewrites:
                file = excuse_repo / path
                original = file.read_text()
                self.ensure(original.count(before) == 1, f'excuse fixture replaces the actual Store.add caller in {path}')
                file.write_text(original.replace(before, after))
                line = next(n for n, text in enumerate(file.read_text().splitlines(), 1) if text.strip() == excuse)
                expected_refs.add(f'{path}:{line}')
                self.run('git', 'add', path, cwd=excuse_repo)
                self.run('git', '-c', 'user.name=example-dev', '-c', 'user.email=dev@example.local',
                         '-c', 'commit.gpgsign=false', 'commit', '-qm', f'feat: write rows directly in {path}',
                         cwd=excuse_repo)
            comments = self.json(self.script(self.name, 'mine_mistakes.py'), excuse_repo, '--json')
            evidence = comments['unclassified'] + [item for c in comments['classes'] for item in c['evidence']]
            self.ensure({item['ref'] for item in evidence if item['source'] == 'code-comment'} == expected_refs,
                        'mining catches both committed copies of the for-now direct-write excuse at their actual lines')
            feedback = self.json(self.script('feedback-to-architecture', 'collect_feedback.py'),
                                 '--repo', excuse_repo, '--code-comments')
            self.ensure({item['ref'] for item in feedback['items'] if item['source'] == 'code-comment'} == expected_refs,
                        'collect_feedback --code-comments mines the same two excuse comments through the shared rule')
            excuse_rules = ROOT / 'skills/correct/references/excuse-comments.json'
            excuse_rule = json.loads(excuse_rules.read_text())['rules'][0]
            arithmetic_repo = self.scratch / 'excuse-arithmetic'
            self.run('git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', self.repo, arithmetic_repo)
            (arithmetic_repo / 'average.py').write_text('average = total // temporary\n')
            self.run('git', 'add', 'average.py', cwd=arithmetic_repo)
            arithmetic = self.lint(excuse_rules, arithmetic_repo, root=arithmetic_repo)
            self.ensure(arithmetic['ok'] and not arithmetic['violations'],
                        'Python floor division by temporary is code, not an excuse comment')
            mined_arithmetic = self.json(self.script(self.name, 'mine_mistakes.py'), arithmetic_repo, '--json')
            feedback_arithmetic = self.json(self.script('feedback-to-architecture', 'collect_feedback.py'),
                                            '--repo', arithmetic_repo, '--code-comments')
            self.ensure(mined_arithmetic['counts'].get('code-comment', 0) == 0
                        and feedback_arithmetic['counts'].get('code-comment', 0) == 0,
                        'mining and feedback share the lint exemption for Python floor division')
            replay = self.json(self.script(self.name, 'patlint.py'), '--rules', excuse_rules,
                               '--root', excuse_repo, '--test', '--json')
            self.ensure(replay['ok'] and {'no-excuse-comment', 'ok'} <= {t['expect'] for t in excuse_rule['tests']},
                        'no-excuse-comment --test proves both offending comments and quiet code or issue references')
            excuse_baseline = self.scratch / 'excuse-baseline.json'
            ratchet = ('--baseline', excuse_baseline)
            self.lint(excuse_rules, excuse_repo, root=excuse_repo, extra=(*ratchet, '--update-baseline'))
            self.ensure(json.loads(excuse_baseline.read_text())['counts']['no-excuse-comment'] ==
                        {'todo/api.py': 1, 'todo/__main__.py': 1},
                        'first adoption baselines the two existing committed excuse comments')
            adopted_baseline = excuse_baseline.read_text()
            store = excuse_repo / 'todo/store.py'
            original_store = store.read_text()
            third = '# temporary fix until Store.add\'s lock is faster\n'
            store.write_text(original_store + '\n' + third)
            failure = self.run(self.script(self.name, 'patlint.py'), '--rules', excuse_rules,
                               '--root', excuse_repo, *ratchet, excuse_repo, expected=1)
            self.ensure('no-excuse-comment' in failure and 'todo/store.py' in failure and excuse_rule['message'] in failure,
                        'a new excuse comment after baseline exits 1 and names the root-cause or TODO(#123) issue fix')
            store.write_text(original_store + '\n' + third.replace('# temporary', '# TODO(#7): temporary'))
            self.ensure(self.lint(excuse_rules, excuse_repo, root=excuse_repo, extra=ratchet)['ok']
                        and excuse_baseline.read_text() == adopted_baseline,
                        'the same new comment tracked as TODO(#7) exits 0 without raising the baseline')
            literal_repo = self.scratch / 'excuse-literals'
            literal_repo.mkdir()
            self.run('git', 'init', '-q', cwd=literal_repo)
            miner_copy = literal_repo / 'mine_mistakes.py'
            shutil.copyfile(self.script(self.name, 'mine_mistakes.py'), miner_copy)
            legacy_literals = [r'WORKAROUND_RE = r"\b(HACK|WORKAROUND|FIXME|XXX)\b"',
                               'ap.add_argument("--no-code-comments", action="store_true", '
                               'help="skip HACK/WORKAROUND/FIXME/XXX comments")']
            miner_copy.write_text(miner_copy.read_text() + '\n' + '\n'.join(legacy_literals) + '\n')
            self.run('git', 'add', 'mine_mistakes.py', cwd=literal_repo)
            self.run('git', '-c', 'user.name=example-dev', '-c', 'user.email=dev@example.local',
                     '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test: retain mining source literals', cwd=literal_repo)
            literals = self.json(self.script(self.name, 'mine_mistakes.py'), literal_repo, '--json')
            self.ensure(literals['counts'].get('code-comment', 0) == 0,
                        'a copy of mine_mistakes and its legacy regex/help literals produces no code-comment items')
            self.correct_grok_regressions()
            self.correct_baseline_contract()
            self.review_baseline_retry()
            self.review_git_warning()
            self.review_git_fail_closed()
            self.review_git_locale()
            self.review_git_inherited_environment()
            self.review_git_case_aliases()
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
            checker = self.script(self.name, 'brief.py')
            task = 'quick fix: stop todos getting lost when two adds overlap'
            quick = self.json(checker, task, '--repo', self.repo, '--json')
            root_label = '- Root cause (the first place it goes wrong; a path or path:symbol):'
            change_label = '- The change that was asked for:'
            self.ensure('quick' not in quick['keywords'], 'quick-fix constraints are not repo search keywords')
            self.ensure(root_label in quick['brief'] and change_label + ' fixes it | covers it' in quick['brief'],
                        'quick-fix template asks for the root cause and whether the requested change fixes or covers it')
            self.ensure(re.search(r'^- Over-specified[^\n]*:\s*quick fix\b', quick['brief'], re.M),
                        'the matched quick-fix phrase starts the over-specified constraint')
            multiline = self.json(checker, 'stop todos getting lost\nUse a quick fix', '--repo', self.repo, '--json')
            missing_root = self.art / 'MULTILINE-MISSING-ROOT.md'
            fixture = (ROOT / 'examples/fixtures/sharpen/MULTILINE-MISSING-ROOT.md').read_text()
            missing_root.write_text(multiline['brief'].split('\n\n', 1)[0] + '\n' + fixture.split('\n', 1)[1])
            failure = self.run(checker, '--check', missing_root, '--repo', self.repo, expected=1)
            self.ensure('Root cause:' in failure and 'The change that was asked for:' in failure,
                        'a multiline quick-fix task still requires both root-cause fields after they are removed')
            self.ensure(multiline['brief'].splitlines()[0] == '# Brief: stop todos getting lost Use a quick fix',
                        'multiline task whitespace collapses into the brief title')
            retry = self.art / 'RETRY-BRIEF.md'
            retry_text = f'''# Brief: {task}

## In my own words

Stop losing todos when adds overlap.
{change_label} fixes it

## How a top expert would approach it

Read todo/api.py and retry three times in todo/api.py, because it is the quickest change.

## Specification check

- Over-specified: quick fix
- Under-specified: none

## Outside check

- Command or link: `python3 -m unittest -q`
- Pass condition: concurrent adds retain both todos.
'''
            retry.write_text(retry_text)
            failure = self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
            self.ensure('quick fix' in failure and 'Root cause:' in failure,
                        'a complete retry-only brief fails for its missing root cause and names quick fix')
            fixed = retry_text.replace('Stop losing todos when adds overlap.',
                                       'Stop losing todos when adds overlap.\n- Root cause: todo/store.py')
            retry.write_text(fixed)
            self.run(checker, '--check', retry, '--repo', self.repo)
            self.ensure(True, 'an existing root-cause path with fixes it passes')
            retry.write_text(fixed.replace('- Root cause: todo/store.py', root_label + ' todo/store.py:Store.add'))
            self.run(checker, '--check', retry, '--repo', self.repo)
            self.ensure(True, 'the generated root-cause label accepts an existing path with a symbol')
            retry.write_text(fixed.replace('- Root cause: todo/store.py', '- Root cause: todo/missing.py'))
            failure = self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
            self.ensure('todo/missing.py' in failure, 'a nonexistent root-cause path fails and names the path')
            for invalid in ('', 'maybe', 'fixes it | covers it'):
                retry.write_text(fixed.replace(change_label + ' fixes it', change_label + ' ' + invalid))
                failure = self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
                self.ensure('fixes it' in failure and 'covers it' in failure,
                            f'the requested-change value {invalid!r} must choose fixes it or covers it')
            covered = fixed.replace(change_label + ' fixes it', change_label + ' covers it')
            retry.write_text(covered)
            failure = self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
            self.ensure('-> yours:' in failure, 'covers it fails with a fix naming the user-owned choice syntax')
            incomplete_choice = '- Under-specified:\n  - Retry three times in todo/api.py -> yours:'
            retry.write_text(covered.replace('- Under-specified: none', incomplete_choice))
            self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
            self.ensure(True, 'a user-owned bandaid choice without the root-cause fix still fails')
            choice = ('- Under-specified:\n'
                      '  - Retry three times in todo/api.py (bandaid) or fix the lost-write root cause '
                      'in todo/store.py -> yours:')
            retry.write_text(covered.replace('- Under-specified: none', choice))
            self.run(checker, '--check', retry, '--repo', self.repo)
            self.ensure(True, 'covers it passes when an open user-owned choice names both the bandaid and root-cause fix')
            for options, description in (
                    ('fix locking in todo/store.py or fix locking in todo/store.py',
                     'repeating the root-cause fix does not name two alternatives'),
                    ('todo/store.py:Store.add or do something',
                     'a path and symbol alone do not name the root-cause fix')):
                invalid_choice = '- Under-specified:\n  - ' + options + ' -> yours:'
                retry.write_text(covered.replace('- Under-specified: none', invalid_choice))
                failure = self.run(checker, '--check', retry, '--repo', self.repo, expected=1)
                self.ensure('-> yours:' in failure, description)
            path_repo = self.scratch / 'brief-paths'
            (path_repo / 'todo').mkdir(parents=True)
            shutil.copyfile(self.repo / 'todo/store.py', path_repo / 'todo/store.py')
            (path_repo / 'todo/store-link.py').symlink_to('store.py')
            linked = covered.replace('- Under-specified: none', choice).replace('todo/store.py', 'todo/store-link.py')
            retry.write_text(linked)
            self.run(checker, '--check', retry, '--repo', path_repo)
            self.ensure(True, 'an internal root-cause symlink reused in the named user choice passes')
            outside = self.scratch / 'brief-outside.py'
            outside.write_text('pass\n')
            (path_repo / 'todo/outside.py').symlink_to(outside)
            for reference in ('todo/outside.py', '../brief-outside.py'):
                retry.write_text(fixed.replace('- Root cause: todo/store.py', '- Root cause: ' + reference))
                failure = self.run(checker, '--check', retry, '--repo', path_repo, expected=1)
                self.ensure(reference in failure and 'path in this repo' in failure,
                            f'root-cause path {reference!r} cannot escape the repo through a symlink or traversal')
            ordinary = self.json(checker, 'set the minimum title length to 3', '--repo', self.repo, '--json')
            self.ensure(root_label not in ordinary['brief'] and change_label not in ordinary['brief'],
                        'minimum title length is an ordinary task and adds neither root-cause field')
            self.ensure(filled.read_bytes() == (ROOT / 'examples/fixtures/FILLED-BRIEF.md').read_bytes(),
                        'the existing filled brief fixture passes unchanged')

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
            self.review_net_change_docs()
            self.review_property_handoff()
            self.review_call_timeout_exit()
            self.review_oneshot_timers()
            self.review_disabled_limits()
            self.review_confirmed_signal_boundary()
            self.review_swallowed_alarm()
            self.review_budget_namespace()
            self.review_timeout_format_handoff()
            self.review_interruptible_result_format()
            self.review_exception_format_timeout()
            self.review_infrastructure_exception_format()
            self.review_acceptance_record_scope()
            self.review_supplemental_acceptance_scope()
            self.review_bounded_limit_workload()

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

    def review_confirmed_signal_boundary(self):
        target = self.repo / 'confirmed_target.py'
        target.write_text('def checked(x): return False\n')
        driver = self.art / 'confirmed_signal_driver.py'
        driver.write_text('''import importlib.util, json, signal, sys
checker, fixture, phase, *argv = sys.argv[1:]
name = "gen_check" if "gen_check" in checker else "bounded_check"
spec = importlib.util.spec_from_file_location(name, checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[name] = mod
spec.loader.exec_module(mod)
fired = False
def inject(frame, event, arg):
    global fired
    data = frame.f_locals
    returned = ((name == "gen_check" and phase == "initial" and frame.f_code.co_name == "run_property"
                 and data.get("status") == "fail" and data["result"]["result"] != "fail")
                or (name == "gen_check" and phase == "shrink" and frame.f_code.co_name == "reduce_failure"
                    and data.get("status") == "fail" and data.get("candidate") == 7)
                or (name == "gen_check" and phase == "shrink_handoff" and frame.f_code.co_name == "run_property"
                    and data.get("value") == 7 and "attempts" in data)
                or (name == "bounded_check" and frame.f_code.co_name == "check"
                    and data.get("ok") is False))
    if event == "line" and returned and not fired:
        fired = True
        limits = data["limits"]
        limits.deadline = limits.begun - 1
        signal.raise_signal(signal.SIGALRM)
    return inject
sys.settrace(inject)
try:
    code = mod.main([fixture, *argv])
finally:
    sys.settrace(None)
assert fired, "return-to-record signal injection did not run"
raise SystemExit(code)
''')
        failures = []
        try:
            for checker, phase in (('gen_check.py', 'initial'), ('gen_check.py', 'shrink'),
                                   ('gen_check.py', 'shrink_handoff'),
                                   ('bounded_check.py', 'initial')):
                fixture = self.art / ('confirmed_' + checker)
                if checker == 'gen_check.py':
                    fixture.write_text('from gen_check import Generator\nfrom confirmed_target import checked\n'
                                       'TARGETS = ["confirmed_target.py:checked"]\n'
                                       'properties = {"broken": (Generator(lambda rng: 42, lambda x: [7] if x == 42 else []), checked)}\n')
                    flags = ('--repo', self.repo, '--examples', '1')
                else:
                    fixture.write_text('def init(): return 42\ndef actions(s): return []\n'
                                       'invariants = {"broken": lambda s: False}\n')
                    flags = ()
                report = self.json(sys.executable, driver, self.script('formal-verification', checker), fixture, phase,
                                   *flags, '--json', expected=(1, 3))
                if checker == 'gen_check.py':
                    found = report['properties']['broken']
                    valid = (found['result'] == 'fail' and found['counterexample'] == ('7' if phase.startswith('shrink') else '42')
                             and found['detail'] == 'returned False' and found['accepted'] == 1
                             and report['called'] == ['confirmed_target.py:checked'] and not report['missing_targets'])
                else:
                    found = report['violation']
                    valid = (report['result'] == 'violation' and found is not None
                             and found['kind'] == 'invariant' and found['name'] == 'broken'
                             and found['trace'] and found['trace'][0]['state'] == 42)
                if self.last_returncode != 1 or not valid:
                    failures.append((checker, phase, self.last_returncode, report))
            self.ensure(not failures, f'SIGALRM after a checked failure returns preserves exit 1, counterexample and coverage: {failures}')
        finally:
            target.unlink()

    def review_swallowed_alarm(self):
        from types import SimpleNamespace
        failures = []
        for checker in ('gen_check.py', 'bounded_check.py'):
            mod = load_module(self.script('formal-verification', checker), 'retry_' + checker[:-3])
            for scope, run, call in (('call', 15, 5), ('run', 5, 15)):
                now, due, repeat = 0.0, 0.0, 0.0
                handler = signal.SIG_DFL
                timers = []

                def set_handler(sig, value):
                    nonlocal handler
                    old, handler = handler, value
                    return old

                def set_timer(which, delay, interval=0.0):
                    nonlocal due, repeat
                    old = (max(0, due - now), repeat)
                    due, repeat = now + delay if delay else 0.0, interval
                    timers.append((now, delay, interval))
                    return old

                def deliver():
                    nonlocal due
                    if due and now >= due:
                        due = 0.0  # The OS consumes a one-shot before delivering SIGALRM.
                        handler(signal.SIGALRM, None)

                alarm = SimpleNamespace(SIGALRM=signal.SIGALRM, ITIMER_REAL=signal.ITIMER_REAL,
                                        SIG_DFL=signal.SIG_DFL, signal=set_handler,
                                        getsignal=lambda sig: handler, getitimer=lambda which: (0.0, 0.0),
                                        setitimer=set_timer)
                with patch.object(mod, 'signal', alarm), patch.object(mod, 'time', SimpleNamespace(monotonic=lambda: now)):
                    with mod.Limits(run, call) as limits:
                        limits.started = 0.0
                        limits.arm()
                        initial = list(timers)
                        now = 5.0
                        try:
                            deliver()
                        except BaseException:
                            pass  # The checked function swallowed the first alarm.
                        if not due or not 0 < due - now <= 0.5 or repeat:
                            failures.append((checker, scope, 'no short one-shot retry', timers))
                        now += 0.5
                        try:
                            deliver()
                        except mod.Timeout as error:
                            if error.scope != scope:
                                failures.append((checker, scope, 'changed timeout scope', error.scope))
                        else:
                            failures.append((checker, scope, 'swallowed alarm disabled the deadline'))
                        # Swallowing a retry must schedule another one, not just a single rescue alarm.
                        if not due or not 0 < due - now <= 0.5 or repeat:
                            failures.append((checker, scope, 'second swallowed alarm lost retry', timers))
                        now += 0.5
                        try:
                            deliver()
                        except mod.Timeout as error:
                            if error.scope != scope:
                                failures.append((checker, scope, 'third alarm changed timeout scope', error.scope))
                        else:
                            failures.append((checker, scope, 'repeated swallowing disabled the deadline'))
                        if any(interval for _, _, interval in timers) or any(delay < 5 for _, delay, _ in initial):
                            failures.append((checker, scope, 'polling before deadline', timers))
                        limits.started = None
                        limits.arm()
                        if (scope == 'call' and due != 15) or (scope == 'run' and due):
                            failures.append((checker, scope, 'call exit did not reset retry', due))
        # A real busy loop catches its first alarm; the retry must interrupt before natural completion.
        for checker in ('gen_check.py', 'bounded_check.py'):
            fixture = self.art / ('swallowed_' + checker)
            body = ('import time\n'
                    'def busy(x):\n'
                    '    end = time.monotonic() + 3\n'
                    '    try:\n'
                    '        while time.monotonic() < end: pass\n'
                    '    except:\n'
                    '        pass\n'
                    '    while time.monotonic() < end: pass\n'
                    '    raise AssertionError("busy loop ran to completion")\n')
            if checker == 'gen_check.py':
                body += ('from gen_check import sampled_from\nTARGETS = ["todo/store.py:Store.add"]\n'
                         'properties = {"busy": (sampled_from([0]), busy)}\n')
                flags, expected = ('--repo', self.repo, '--examples', '1'), 3
            else:
                body += 'def init(): return 0\ndef actions(s): return []\ninvariants = {"busy": busy}\n'
                flags, expected = (), 2
            fixture.write_text(body)
            started = time.monotonic()
            report = self.json(self.script('formal-verification', checker), fixture, *flags,
                               '--call-timeout', '0.3', '--timeout', '1.5', '--json', expected=expected, timeout=8)
            elapsed = time.monotonic() - started
            self.log.write(f'{checker} swallowed-alarm busy loop: {elapsed:.3f} s (natural duration 3 s)\n')
            if elapsed >= 2.5 or 'busy loop ran to completion' in json.dumps(report):
                failures.append((checker, 'busy loop reached its natural end', elapsed, report))
        self.ensure(not failures, f'swallowed alarms retry once at short intervals only after expiry and interrupt busy loops: {failures}')

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
                    body = ('import review_clock as clock\nCHECK_TIMEOUT = 0\n'
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
        # 2026-11-24 #1 moved replay and limit details into --help and history into CHANGELOG.md.
        text = (ROOT / 'skills/formal-verification/SKILL.md').read_text()
        self.ensure(all(term in text for term in ('90 s', '10 s', 'gen_check.py --help')),
                    'formal-verification/SKILL.md states the default limits and points to --help for the rest')
        self.ensure('migration' not in text.lower() and "main's" not in text,
                    'formal-verification/SKILL.md carries no migration history for generated skills to copy')
        for checker in ('gen_check.py', 'bounded_check.py'):
            usage = self.run(sys.executable, self.script('formal-verification', checker), '--help')
            terms = ('--expect-fail', '--timeout 0', '--call-timeout 0') if checker == 'gen_check.py' else ('--timeout 0', '--call-timeout 0')
            self.ensure(all(term in usage for term in terms), f'{checker} --help documents {", ".join(terms)}')
        changelog = (ROOT / 'CHANGELOG.md').read_text()
        self.ensure('--timeout 0 --call-timeout 0' in changelog and '--seed N --expect-fail NAME' in changelog,
                    'CHANGELOG.md keeps the default-limit and replay migration notes')

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

    def review_budget_namespace(self):
        """Business TIMEOUT values never configure the runner; CHECK_TIMEOUT is explicit."""
        target = self.repo / 'namespace_target.py'
        target.write_text('def checked(value): return True\n')
        failures = []
        cases = [
            ('business fraction', 'TIMEOUT = 0.3', 1, None, 0, 90),
            ('business None', 'TIMEOUT = None', 1, None, 0, 90),
            ('business ticks', 'TIMEOUT = 3', 4, None, 0, 90),
            ('explicit module', 'TIMEOUT = None\nCHECK_TIMEOUT = 0.3', 1, None, 3, 0.3),
            ('module disabled', 'TIMEOUT = 0.3\nCHECK_TIMEOUT = 0', 100, None, 0, 0),
            ('CLI override', 'TIMEOUT = None\nCHECK_TIMEOUT = 0.3', 1, 2, 0, 2),
            ('CLI disabled', 'TIMEOUT = None\nCHECK_TIMEOUT = 0.3', 100, 0, 0, 0),
        ]
        try:
            for checker in ('bounded_check.py', 'gen_check.py'):
                for label, constants, elapsed, override, code, budget in cases:
                    body = ('import review_clock as clock\n' + constants + '\n'
                            f'clock.advance({elapsed}, tick=False)\n'
                            'def check(x):\n    clock.events.append("checked")\n    return True\n')
                    if checker == 'gen_check.py':
                        body += ('from gen_check import sampled_from\nfrom namespace_target import checked\n'
                                 'TARGETS = ["namespace_target.py:checked"]\n'
                                 'def covered(x):\n    check(x)\n    return checked(x)\n'
                                 'properties = {"ok": (sampled_from([0]), covered)}\n')
                    else:
                        body += 'def init(): return 0\ndef actions(s): return []\ninvariants = {"ok": check}\n'
                    result = self.clock_check(checker, body, run=override, call=0, flags=('--json',))
                    report = json.loads(result['stdout']) if result['stdout'].strip() else {}
                    self.log.write(json.dumps({'checker': checker, 'case': label, **result}) + '\n')
                    if (result['code'] != code or report.get('ok') != (code == 0)
                            or report.get('timeout') != budget
                            or result['events'] != (['checked'] if code == 0 else [])):
                        failures.append((checker, label, result))
            self.ensure(not failures,
                        f'business TIMEOUT is ignored (including None); CHECK_TIMEOUT and CLI budgets remain explicit: {failures}')
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
                    body = ('import review_clock as clock\nCHECK_TIMEOUT = 1\n'
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
                    and 'CHECK_TIMEOUT' in text and 'revalidate' in text and 'pinned' in text,
                    'changelog documents changed seed sequences, added limits and replay migration')
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
        for file in ('bounded_check.py', 'gen_check.py'):
            text = self.run(sys.executable, self.script('formal-verification', file), '--help').lower()
            self.ensure(all(term in text for term in ('sigalrm', 'main thread', 'native code', 'external')),
                        f'{file} --help documents timer/thread support, native-code limits and an external deadline')
        self.ensure("--help" in (ROOT / 'skills/formal-verification/SKILL.md').read_text(),
                    'formal-verification/SKILL.md points to the checkers\' --help for timer details')

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
            # Deliberately broken prose lives only in a scratch collection. These
            # assertions execute the shipped CLI, including its actual exit code.
            budget_root = self.scratch/'prose-budget'
            shutil.copytree(ROOT/'skills', budget_root/'skills',
                            ignore=shutil.ignore_patterns('__pycache__'))
            (budget_root/'scripts').mkdir()
            budget_file = budget_root/'scripts/prose-budget.json'
            shutil.copyfile(ROOT/'scripts/prose-budget.json', budget_file)
            budget_checker = ROOT/'scripts/prose_budget.py'
            budget_command = [sys.executable, budget_checker, '--repo', budget_root]
            real = self.json(sys.executable, budget_checker, '--repo', ROOT, '--json', cwd=ROOT)
            self.ensure(real['ok'], 'the real skill collection passes prose budgets after paydown')
            self.run(*budget_command, '--update', cwd=ROOT)
            budget_skill = budget_root/'skills/formal-verification/SKILL.md'
            budget_original = budget_skill.read_text()
            body, sources_heading, sources = budget_original.partition('\n## Sources\n')
            self.ensure(bool(sources_heading), 'the prose fixture retains the real Sources section')
            budgets = json.loads(budget_file.read_text())
            original_budget = budgets['formal-verification']['budget']
            twenty_words = ' '.join(f'added{i}' for i in range(20))
            budget_skill.write_text(body + '\n' + twenty_words + sources_heading + sources)
            output = self.run(*budget_command, expected=1, cwd=ROOT)
            self.ensure(f'skills/formal-verification/SKILL.md: {original_budget + 20} words, '
                        f'budget {original_budget} (+20)' in output,
                        'the over-budget SKILL.md fixture exits 1 naming the skill, count, and budget')
            budget_skill.write_text(budget_original + '\n' + twenty_words + '\n')
            self.run(*budget_command, cwd=ROOT)
            self.ensure(True, 'adding the same 20 words under Sources does not consume prose budget')
            budget_skill.write_text(budget_original.replace('\n---\n', '\n# ' + twenty_words + '\n---\n', 1))
            self.run(*budget_command, cwd=ROOT)
            self.ensure(True, 'frontmatter words are excluded from the prose budget')
            fenced_sources = '\n```\n## Sources\n' + twenty_words + '\n```\n'
            budget_skill.write_text(body + fenced_sources + sources_heading + sources)
            fenced_report = self.json(*budget_command, '--json', expected=1, cwd=ROOT)
            self.ensure(any(row['skill'] == 'formal-verification'
                            and row['words'] == original_budget + len(fenced_sources.split())
                            for row in fenced_report['skills']),
                        'a fenced Sources heading cannot hide over-budget body words')
            resumed_prose = '\n## After sources\n' + twenty_words + '\n'
            budget_skill.write_text(budget_original + resumed_prose)
            resumed_report = self.json(*budget_command, '--json', expected=1, cwd=ROOT)
            self.ensure(any(row['skill'] == 'formal-verification'
                            and row['words'] == original_budget + len(resumed_prose.split())
                            for row in resumed_report['skills']),
                        'counting resumes at the next H2 after the Sources section')

            frontmatter, boundary, prose = body.partition('\n---\n')
            self.ensure(bool(boundary) and len(prose.split()) >= 20,
                        'the deletion fixture changes body words without changing frontmatter')
            shorter = re.sub(r'\S+', '', prose, count=20)
            budget_skill.write_text(frontmatter + boundary + shorter + sources_heading + sources)
            self.run(*budget_command, '--update', cwd=ROOT)
            lowered = budget_file.read_bytes()
            self.ensure(json.loads(lowered)['formal-verification']['budget'] == original_budget - 20,
                        'deleting 20 body words and --update lowers the skill budget by 20')
            self.run(*budget_command, '--update', cwd=ROOT)
            self.ensure(budget_file.read_bytes() == lowered,
                        'a second --update leaves the budget file byte-for-byte unchanged')
            budget_skill.write_text(budget_original)
            self.run(*budget_command, '--update', expected=1, cwd=ROOT)
            self.ensure(budget_file.read_bytes() == lowered,
                        '--update fails on restored extra prose and never raises its budget')
            self.run(*budget_command, '--raise', 'formal-verification', '--reason', 'adds --exhaustive', cwd=ROOT)
            raised = json.loads(budget_file.read_text())['formal-verification']
            self.ensure(raised['budget'] == original_budget and raised['reason'] == 'adds --exhaustive'
                        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', raised['date']),
                        '--raise exits 0 and records the current count, review reason, and date')

            history_line = "the defaults are incompatible with main's behavior"
            budget_skill.write_text(body + '\n' + history_line + sources_heading + sources)
            history_budget = json.loads(budget_file.read_text())
            history_budget['formal-verification']['budget'] += len(history_line.split())
            budget_file.write_text(json.dumps(history_budget, indent=2) + '\n')
            output = self.run(*budget_command, expected=1, cwd=ROOT)
            history_report = self.json(*budget_command, '--json', expected=1, cwd=ROOT)
            history_skill = next(row for row in history_report['skills'] if row['skill'] == 'formal-verification')
            self.ensure('skills/formal-verification/SKILL.md' in output and 'CHANGELOG.md' in output
                        and history_skill['words'] == history_skill['budget'],
                        "the main's fixture exits 1 within its word budget and names CHANGELOG.md as the fix")
            budget_skill.write_text(budget_original)
            budget_file.write_text(json.dumps(budgets, indent=2) + '\n')
            missing_budget = dict(budgets)
            del missing_budget['formal-verification']
            budget_file.write_text(json.dumps(missing_budget, indent=2) + '\n')
            output = self.run(*budget_command, expected=1, cwd=ROOT)
            self.ensure('formal-verification' in output and '--update' in output,
                        'a skill without a budget exits 1 and names --update as the fix')
            orphan_budget = {**budgets, 'missing-skill': {'budget': 0}}
            budget_file.write_text(json.dumps(orphan_budget, indent=2) + '\n')
            output = self.run(*budget_command, expected=1, cwd=ROOT)
            self.ensure('missing-skill' in output,
                        'a budget entry without a skill exits 1 and names the orphan entry')
            budget_file.write_text(json.dumps(budgets, indent=2) + '\n')
            self.run(*budget_command, cwd=ROOT)
            self.ensure(True, 'restoring the scratch skill and budgets restores a passing collection')

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
            fixtures = ROOT/'examples/fixtures/curator'
            checker = self.script(self.name,'check_proposals.py')
            # Inline real-post fixtures keep ignored curator history out of the test inputs.
            # A transport tripwire makes every timestamp assertion prove it needs no fetch.
            timestamp_root = self.scratch/'post-times'
            timestamp_root.mkdir()
            timestamp_bin = timestamp_root/'bin'
            timestamp_bin.mkdir()
            timestamp_fetch = timestamp_root/'unexpected-fetch'
            timestamp_curl = timestamp_bin/'curl'
            timestamp_curl.write_text('#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n'
                                      'Path(os.environ["TIMESTAMP_FETCH"]).write_text("unexpected fetch")\n'
                                      'raise SystemExit(97)\n')
            timestamp_curl.chmod(0o755)
            timestamp_env = dict(self.env,PATH=str(timestamp_bin)+os.pathsep+self.env.get('PATH',''),
                                 TIMESTAMP_FETCH=str(timestamp_fetch))
            digest_file = timestamp_root/'digest.md'
            digest = ('# X digest\nWindow: 2026-11-14T00:00:00Z to 2026-11-24T00:00:00Z\n\n'
                      '## 1. Quick fixes\n'
                      '- URL: https://x.com/poteto/status/2121646643124809848\n'
                      '- Time: 2026-11-15 00:49 Asia/Taipei\n\n'
                      '## 2. Referenced post\n'
                      '- URL: https://x.com/rachelnabors/status/2124176031011475785\n'
                      '- Time: 2026-11-22 00:20 Asia/Taipei\n')

            def check_digest(body, expected=0):
                digest_file.write_text(body)
                return self.json(sys.executable,checker,'--digest',digest_file,'--json',
                                 env=timestamp_env,expected=expected)

            result = check_digest(digest.replace('2026-11-22 00:20','2026-11-21 00:20'),expected=1)
            self.ensure(any(f'{digest_file}:10:' in p and '2124176031011475785' in p
                            and '2026-11-22 00:20 Asia/Taipei' in p and '2026-11-21 16:20 UTC' in p
                            and 'not 2026-11-21 00:20' in p and 'created_at' in p
                            and 'stated zone' in p for p in result['problems']),
                        'the real digest day-early mistake fails offline with its line, correct local/UTC times, and fix')
            result = check_digest(digest)
            self.ensure(result['ok'] and result['timestamps_checked'] == 2,
                        'two real status IDs pass offline at 2026-11-15 00:49 and 2026-11-22 00:20 Asia/Taipei')
            result = check_digest(digest.replace('- Time: 2026-11-15 00:49 Asia/Taipei\n',''),expected=1)
            self.ensure(any('2121646643124809848' in p and 'Time' in p for p in result['problems']),
                        'an item missing Time fails without borrowing the next item timestamp')
            result = check_digest(digest.replace('2026-11-14T00:00:00Z','2026-11-14T16:49:13Z'),expected=1)
            self.ensure(any('2121646643124809848' in p and 'window' in p.lower() for p in result['problems']),
                        'a numbered digest item before the ISO window start fails using the exact status instant')

            adjacent = ('# X digest\nWindow: 2026-11-14T00:00:00Z to 2026-11-24T00:00:00Z\n\n'
                        '1. https://x.com/poteto/status/2121646643124809848 '
                        '(2026-11-15 00:49 Asia/Taipei; quoted post)\n'
                        '2. https://x.com/rachelnabors/status/2124176031011475785 '
                        '(2026-11-22 00:20 Asia/Taipei)\n')
            result = check_digest(adjacent.replace('2026-11-22 00:20','2026-11-21 00:20'),expected=1)
            self.ensure(any('2124176031011475785' in p and '2026-11-22 00:20' in p
                            for p in result['problems']),
                        'a timestamp immediately parenthesized after a status URL fails on the real wrong day')
            result = check_digest(adjacent)
            self.ensure(result['ok'] and result['timestamps_checked'] == 2,
                        'immediately parenthesized status times pass to the minute with trailing context')

            dated_proposal = timestamp_root/'dated-proposal.md'
            dated_body = ('# Curator proposals\nDates below are UTC.\n\n'
                          '2026-11-14 (status 2121646643124809848)\n\n'
                          '## 1. Check cited post times\n'
                          '- Source: https://x.com/poteto/status/2121646643124809848\n'
                          '- Level: lint\n- Skill: curator\n'
                          '- Change: Compare cited post dates with their status timestamps.\n')

            def check_dated_proposal(body, expected=0):
                dated_proposal.write_text(body)
                return self.json(sys.executable,checker,dated_proposal,'--json',
                                 env=timestamp_env,expected=expected)

            result = check_dated_proposal(dated_body.replace('2026-11-14 (status','2026-11-13 (status'),expected=1)
            self.ensure(any(f'{dated_proposal}:4:' in p and '2121646643124809848' in p
                            and '2026-11-14' in p and 'UTC' in p for p in result['problems']),
                        'a real proposal citing 2026-11-13 (status 2121646643124809848) fails with its UTC date')
            result = check_dated_proposal(dated_body)
            self.ensure(result['ok'] and result['items'] == 1 and result['timestamps_checked'] == 1,
                        'the same real proposal passes with 2026-11-14 and the original proposal field gates')
            reversed_body = dated_body.replace('2026-11-14 (status 2121646643124809848)',
                                               '(status 2121646643124809848, 2026-11-14)')
            result = check_dated_proposal(reversed_body.replace(', 2026-11-14)',', 2026-11-13)'),expected=1)
            self.ensure(any('2121646643124809848' in p and '2026-11-14' in p for p in result['problems']),
                        'the parenthesized status-ID-first proposal form rejects the wrong UTC date offline')
            result = check_dated_proposal(reversed_body)
            self.ensure(result['ok'] and result['timestamps_checked'] == 1,
                        'the parenthesized status-ID-first proposal form accepts the correct UTC date offline')
            for citation in ('2026-11-10 (status 403)', '(status 403, 2026-11-10)'):
                result = check_dated_proposal(dated_body.replace('2026-11-14 (status 2121646643124809848)', citation))
                self.ensure(result['ok'] and result.get('timestamps_checked', 0) == 0,
                            f'HTTP status citation {citation} is not checked as an X status ID')
            result = check_digest(digest.replace('2121646643124809848', '403'))
            self.ensure(result['ok'] and result['timestamps_checked'] == 1,
                        'a short numeric status URL is not decoded as an X Snowflake ID')
            dated_proposal.write_text(reversed_body)
            digest_file.write_text(digest)
            result = self.json(sys.executable,checker,dated_proposal,'--digest',digest_file,'--json',
                               env=timestamp_env)
            self.ensure(result['ok'] and result['timestamps_checked'] == 3,
                        'one offline invocation validates proposal UTC dates and digest local times together')
            self.ensure(not timestamp_fetch.exists(),
                        'real digest and proposals pass/fail timestamp fixtures never invoke the network transport')
            for fixture_name, baseline_name in (('legacy-valid.md','legacy-baseline.json'),
                                                ('legacy-descriptive-h3.md','legacy-descriptive-baseline.json'),
                                                ('legacy-fenced-h3.md','legacy-fenced-baseline.json')):
                baseline = json.loads((fixtures/baseline_name).read_text())
                legacy = (fixtures/fixture_name).relative_to(ROOT)
                self.ensure(module.parse((ROOT/legacy).read_text()) == baseline['parsed'],
                            f'{fixture_name}: legacy H2 proposals retain the exact count, fields, titles, and URLs')
                legacy_output = self.run(checker,legacy,'--skills-dir','skills',cwd=ROOT,
                                         expected=baseline['exit_code'])
                self.ensure(legacy_output == 'quotes not checked (no --fetch)\n'+baseline['stdout']
                            and not baseline['stderr'],
                            f'{fixture_name}: legacy CLI output is retained after the explicit no-fetch quote notice')
                self.ensure(self.json(checker,legacy,'--skills-dir','skills','--json',cwd=ROOT)
                            == dict(baseline['result'],quotes_checked=False),
                            f'{fixture_name}: original JSON check fields are retained with quotes_checked false')
            fenced = (fixtures/'legacy-fenced-h3.md').read_text()
            fenced_baseline = json.loads((fixtures/'legacy-fenced-baseline.json').read_text())
            for fence in ('```', '~~~', '````', '~~~~'):
                for heading in ('##', '###'):
                    body = fenced.replace('```', fence).replace('### Example', heading+' Example')
                    self.ensure(module.parse(body) == fenced_baseline['parsed'],
                                f'{fence} fences keep {heading} example headings inside one legacy proposal')
                    self.ensure(module.parse(body+'\n'+body) == fenced_baseline['parsed'] * 2,
                                f'{fence} closing fences preserve the next proposal after {heading} example headings')
            self.ensure(module.parse('```inline example```\n\n'+fenced) == fenced_baseline['parsed'],
                        'an inline backtick span before a proposal is not an unclosed fenced block')
            references = self.scratch/'proposal-references.md'
            references.write_text('## 1. Check proposal references\n'
                                  '- Source: https://example.invalid/proposal\n'
                                  '- Level: lint\n- Skill: curator\n'
                                  '- Change: Check proposal fields before acceptance.\n'
                                  '### References\n- Source: https://example.invalid/reference\n')
            result = self.json(checker,references,'--skills-dir',collection/'skills','--json')
            self.ensure(result['ok'] and result['items'] == 1
                        and len(module.parse(references.read_text())) == 1,
                        'a Source-only References H3 remains part of its valid H2 proposal')
            result = self.json(checker,fixtures/'fragment-h3-valid.md','--json',expected=1)
            self.ensure(result['items'] == 0 and any('no proposal sections' in p for p in result['problems']),
                        'bare H3 proposals require --fragment instead of silently passing as a full file')
            before_h2 = (fixtures/'fragment-h3-valid.md').read_text() + '\n' + references.read_text()
            self.ensure(len(module.parse(before_h2)) == 1 and len(module.parse(before_h2,fragment=True)) == 3,
                        'H3 sections before the first H2 count only in fragment mode')
            for layout, flags in (('nested',()), ('fragment',('--fragment',))):
                valid = fixtures/f'{layout}-h3-valid.md'
                bad = fixtures/f'{layout}-h3-banana.md'
                result = self.json(checker,valid,*flags,'--skills-dir',collection/'skills','--json')
                self.ensure(result['items'] == 2 and len(module.parse(valid.read_text(), fragment=bool(flags))) == 2,
                            f'two H3 proposals parse and validate as two in a {layout}')
                result = self.json(checker,bad,*flags,'--skills-dir',collection/'skills','--json',expected=1)
                self.ensure(result['items'] == 2 and any('#2' in p and 'banana' in p for p in result['problems']),
                            f'the second H3 Level banana fails independently in a {layout}')
            for name, problem in (('h2-banana.md','banana'), ('h2-missing-source.md','Source')):
                result = self.json(checker,fixtures/name,'--skills-dir',collection/'skills','--json',expected=1)
                self.ensure(any(problem in p for p in result['problems']),
                            f'legacy H2 rejection is retained for {name}')
            mixed = self.scratch/'mixed-h2-h3.md'
            mixed.write_text((fixtures/'h2-missing-source.md').read_text()+'\n'
                             +(fixtures/'fragment-h3-valid.md').read_text())
            result = module.check(mixed,collection/'skills',False)
            self.ensure(result['items'] == 3 and not result['ok']
                        and any('#1' in p and 'Source' in p for p in result['problems']),
                        'a malformed numbered H2 parent stays invalid when valid H3 children follow it')
            mixed.write_text('## 1. Missing fields\n\n'+(fixtures/'fragment-h3-valid.md').read_text())
            result = module.check(mixed,collection/'skills',False)
            self.ensure(result['items'] == 3 and not result['ok'],
                        'an empty numbered H2 proposal cannot disappear into an H3 grouping')
            mixed.write_text('## 1. Parent\n- Source: https://example.invalid/parent\n'
                             '- Level: ci\n- Skill: curator\n### Implementation\n'
                             '- Change: Add a parent proposal check.\n\n'
                             +(fixtures/'fragment-h3-valid.md').read_text())
            result = module.check(mixed,collection/'skills',False)
            self.ensure(result['items'] == 3 and result['ok']
                        and module.parse(mixed.read_text())[0]['fields']['change'] == 'Add a parent proposal check.',
                        'H2 parent fields under a descriptive H3 remain intact alongside two sourced H3 proposals')
            mixed.write_text((fixtures/'fragment-h3-banana.md').read_text()+'\nNo proposals.\n')
            self.ensure(not module.check(mixed,collection/'skills',False,fragment=True)['ok'],
                        'No proposals. cannot override a malformed proposal already present')

            # Repeat every field gate behind an earlier valid proposal: it cannot lend fields to the next one.
            first, second = (fixtures/'fragment-h3-valid.md').read_text().split('### Second',1)
            mutations = (
                ('source URL', '- Source: https://example.invalid/second', '- Source: not-a-url', 'Source'),
                ('missing source', '- Source: https://example.invalid/second', '', 'Source'),
                ('missing level', '- Level: test', '', 'Level'),
                ('short change', '- Change: Check each proposal independently before acceptance.', '- Change: Fix it', 'Change'),
                ('missing change', '- Change: Check each proposal independently before acceptance.', '', 'Change'),
                ('unknown skill', '- Skill: curator', '- Skill: nonexistent-skill', 'Skill'),
                ('missing skill', '- Skill: curator', '', 'Skill'),
            )
            mutant = self.scratch/'proposal-field-mutant.md'
            for label, old, new, problem in mutations:
                for layout in ('h2','nested','fragment'):
                    body = first+'### Second'+second.replace(old,new)
                    if layout == 'h2':
                        body = body.replace('### First','## 1. First').replace('### Second','## 2. Second')
                    elif layout == 'nested':
                        body = '## Panel proposals\n\n'+body
                    mutant.write_text(body)
                    result = module.check(mutant,collection/'skills',False,fragment=layout == 'fragment')
                    self.ensure(result['items'] == 2 and not result['ok']
                                and any('#2' in p and problem in p for p in result['problems']),
                                f'{layout} proposals retain the {label} gate without borrowing earlier fields')
            for alias, level in module.ALIASES.items():
                mutant.write_text(first+'### Second'+second.replace('- Level: test',f'- Level: {alias}'))
                result = module.check(mutant,collection/'skills',False,fragment=True)
                self.ensure(result['ok'] and result['proposals'][1]['level'] == level,
                            f'H3 level alias {alias!r} retains its canonical value {level!r}')
            for skill in ('collection','new:panel-example'):
                mutant.write_text(first+'### Second'+second.replace('- Skill: curator',f'- Skill: {skill}'))
                self.ensure(module.check(mutant,collection/'skills',False,fragment=True)['ok'],
                            f'H3 proposals retain the accepted skill target {skill!r}')
            fetched = []
            def fake_fetch(url):
                fetched.append(url)
                return 200, 'A fixture page with no quoted claims.'
            with patch.object(module,'fetch_source',fake_fetch):
                result = module.check(fixtures/'nested-h3-valid.md',collection/'skills',True)
            self.ensure(result['ok'] and fetched == ['https://example.invalid/first','https://example.invalid/second']
                        and all(p['fetch'] == {p['urls'][0]:200} for p in result['proposals']),
                        '--fetch requests and records each H3 section URL independently')
            for status in (0,199,300,401,403,429,500):
                with patch.object(module,'fetch_source',
                                  lambda url, status=status: (status if url.endswith('/second') else 200, '')):
                    result = module.check(fixtures/'nested-h3-valid.md',collection/'skills',True)
                self.ensure(not result['ok'] and any('#2' in p and 'unreachable' in p for p in result['problems']),
                            f'the second H3 source still fails on HTTP {status or "error"}')

            # Quote fixtures use the real checker and transport subprocess, never a patched matcher.
            # Only the real correct skill is tracked here: this test's own strings cannot prove themselves.
            quote_root = self.scratch/'quote-collection'
            (quote_root/'skills/correct').mkdir(parents=True)
            shutil.copyfile(ROOT/'skills/correct/SKILL.md',quote_root/'skills/correct/SKILL.md')
            self.run('git','init','-q','--template=',quote_root)
            self.run('git','-C',quote_root,'add','skills/correct/SKILL.md')
            (quote_root/'proposals').mkdir()
            quote_file = quote_root/'proposals/current.md'
            transport = self.scratch/'quote-transport'
            transport.mkdir()
            quote_fetch_log = transport/'fetched.jsonl'
            quote_pages = transport/'pages.json'
            quote_curl = transport/'curl'
            quote_curl.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nimport json, os, sys, time\n'
                                  'args = sys.argv[1:]\n'
                                  'assert args[args.index("--max-time")+1] == os.environ.get("QUOTE_MAX_TIME", "20")\n'
                                  'url = args[-1]\n'
                                  'if any(char in url for char in "{}[]"):\n'
                                  '    assert "-g" in args or "--globoff" in args, "Source URL must stay literal"\n'
                                  'with open(os.environ["QUOTE_FETCH_LOG"], "a") as stream:\n'
                                  '    stream.write(json.dumps(url)+"\\n")\n'
                                  'status, page = json.loads(Path(os.environ["QUOTE_PAGES"]).read_text())[url]\n'
                                  'Path(args[args.index("-D")+1]).write_text(f"HTTP/1.1 {status} Fixture\\r\\n\\r\\n")\n'
                                  'time.sleep(float(os.environ.get("QUOTE_CURL_DELAY", "0")))\n'
                                  'sys.stdout.buffer.write(Path(page).read_bytes())\n'
                                  'sys.exit(int(os.environ.get("QUOTE_CURL_EXIT", "0")))\n')
            quote_curl.chmod(0o755)
            quote_env = dict(self.env,PATH=str(transport)+os.pathsep+self.env.get('PATH',''),
                             QUOTE_FETCH_LOG=str(quote_fetch_log),QUOTE_PAGES=str(quote_pages))
            first_url, second_url = 'https://example.invalid/quote-first', 'https://example.invalid/quote-second'
            blob_url = 'https://github.com/fixture/skills/blob/abc123/SKILL.md'
            raw_url = 'https://raw.githubusercontent.com/fixture/skills/abc123/SKILL.md'
            tweet_url = 'https://api.fxtwitter.com/fixture/status/123'
            phrase = 'violet lanterns protect the quiet archive'
            sources = {
                first_url: (200, '<html><p>'+phrase+'</p><p>The curator\'s evidence stays beside each claim.</p>'
                            '<p>amber birds arrive before dusk; several unrelated details; silver foxes leave at dawn.</p>'
                            '<p>THE  <em>luminous</em>\n archive &amp; its caretakers — keep_a *careful* `record`.</p></html>'),
                second_url: (200, '<p>cobalt rivers carry every unlisted quotation</p>'),
                blob_url: (503, '<p>Service unavailable</p>'),
                raw_url: (200, 'Raw file evidence confirms the cedar gate.\n'
                          'a < b\nCopper lanterns illuminate the comparison branch.\nresult -> value'),
                tweet_url: (200, json.dumps({'tweet':{'text':"The keeper's nested evidence survives JSON decoding."},
                                             'These hidden keys are never quoted evidence':False})
                            .replace("'",r'\u0027')),
            }
            manifest = {}
            for index, (url, (status, body)) in enumerate(sources.items()):
                page = transport/f'page-{index}.body'
                page.write_text(body)
                manifest[url] = [status,str(page)]
            quote_pages.write_text(json.dumps(manifest))

            def quoted_proposal(change, why='', url=first_url, title='1. Quote evidence', heading='##'):
                return (f'{heading} {title}\n- Source: {url}\n- Level: lint\n- Skill: collection\n'
                        f'- Change: Validate each quoted claim. {change}\n- Why: {why or "Keep fetched evidence accurate."}\n')

            def check_quotes(body, expected=0, fetch=True, fragment=False):
                quote_file.write_text(body)
                return self.json(checker,quote_file,'--skills-dir',quote_root/'skills','--json',
                                 *(('--fetch',) if fetch else ()), *(('--fragment',) if fragment else ()),
                                 env=quote_env,expected=expected)

            result = check_quotes(quoted_proposal(f'"{phrase}"'))
            self.ensure(result['ok'] and result['quotes_checked'],
                        'a four-word-or-longer quote on a real stubbed page body passes --fetch')
            wrong_phrase = phrase.replace('protect','disturb')
            result = check_quotes(quoted_proposal(f'"{wrong_phrase}"'),expected=1)
            self.ensure(any('#1 "1. Quote evidence"' in p and f'quote "{wrong_phrase}"' in p
                            and 'quote the fetched page exactly' in p and 'add the page you quoted to Source' in p
                            and 'put proposed text in backticks' in p for p in result['problems']),
                        'one changed word fails through the real transport and names the quote, proposal, and fixes')
            diagnostics = quote_root/'logs/curator'
            diagnostics.mkdir(parents=True)
            rejected = result
            for name, content in (
                    ('2026-11-24.log', '\n'.join(rejected['problems'])),
                    ('2026-11-24.test-seat-x-propose.err', '\n'.join(rejected['problems'])),
                    ('2026-11-24.test-seat-x-propose.md', quoted_proposal(f'"{wrong_phrase}"')),
                    ('2026-11-24.x-propose-prompt.md', quoted_proposal(f'"{wrong_phrase}"'))):
                artifact = diagnostics/name
                artifact.write_text(content)
                try:
                    for tracked in (False, True):
                        if tracked:
                            self.run('git', '-C', quote_root, 'add', artifact)
                        result = check_quotes(quoted_proposal(f'"{wrong_phrase}"'), expected=1)
                        self.ensure(result['problems'] == rejected['problems'],
                                    f'{name} (tracked={tracked}) cannot validate its rejected quote on rerun')
                finally:
                    artifact.unlink()
            digest_evidence = diagnostics/'2026-11-24.x-digest.md'
            digest_evidence.write_text(wrong_phrase)
            try:
                self.ensure(check_quotes(quoted_proposal(f'"{wrong_phrase}"'))['ok'],
                            'an untracked x-digest remains valid collection quotation evidence')
            finally:
                digest_evidence.unlink()
            result = check_quotes(quoted_proposal('“The curator’s evidence stays beside each claim.”'))
            self.ensure(result['ok'], 'curly double quotes and apostrophes match straight apostrophes in a fetched body')
            result = check_quotes(quoted_proposal('"amber birds arrive before dusk…silver foxes leave at dawn"'))
            self.ensure(result['ok'], 'ellipsis quote parts match in order within one fetched page body')
            result = check_quotes(quoted_proposal('"silver foxes leave at dawn...amber birds arrive before dusk"'),expected=1)
            self.ensure(any('quote "silver foxes' in p for p in result['problems']),
                        'ellipsis parts present only in reverse order fail')
            result = check_quotes(quoted_proposal('"the luminous archive & its caretakers - keepa careful record."'))
            self.ensure(result['ok'], 'HTML tags/entities, case, dashes, Markdown markers and backticks normalize alike')
            correct_line = next(line for line in (quote_root/'skills/correct/SKILL.md').read_text().splitlines()
                                if line.startswith('Human review is not a level.'))
            result = check_quotes(quoted_proposal(f'"{correct_line}"'))
            self.ensure(result['ok'] and all(correct_line not in body for _, body in sources.values()),
                        'a quote of an actual tracked correct/SKILL.md line passes without any source page holding it')
            result = check_quotes(quoted_proposal('"three invented words" and `"an invented proposed message stays unchecked"`'
                                                  '\n  ```text\n  "a fenced proposed message is also skipped"\n  ```'))
            self.ensure(result['ok'], 'three-word quotes and backticked inline or fenced proposed messages are skipped')
            result = check_quotes(quoted_proposal('\n  - Nested evidence: "'+phrase+'"',
                                                  why='Check this continuation:\n    “The curator’s evidence stays beside each claim.”'))
            self.ensure(result['ok'], 'multiline Change and Why bullets retain quoted claims and normalize their text')
            result = check_quotes(quoted_proposal('Add a concrete check.',why='Check this continuation:\n    "'+wrong_phrase+'"'),
                                  expected=1)
            self.ensure(any(wrong_phrase in p for p in result['problems']), 'a missing quote in a multiline Why fails')
            borrowed = 'cobalt rivers carry every unlisted quotation'
            result = check_quotes(quoted_proposal('"'+borrowed+'"',title='Borrower',heading='###')
                                  +quoted_proposal('Add a concrete check.',url=second_url,title='Other source',heading='###'),
                                  expected=1,fragment=True)
            self.ensure(any('#1 "Borrower"' in p and borrowed in p for p in result['problems'])
                        and not any('#2' in p for p in result['problems']),
                        'a quote on only another proposal\'s fetched page fails without borrowing its evidence')
            result = check_quotes(quoted_proposal('"Raw file evidence confirms the cedar gate."',url=blob_url))
            self.ensure(result['ok'] and blob_url in quote_fetch_log.read_text() and raw_url in quote_fetch_log.read_text(),
                        'a GitHub blob returning 503 passes with the quote on its additional raw file fetch')
            result = check_quotes(quoted_proposal('"Copper lanterns illuminate the comparison branch."',url=blob_url))
            self.ensure(result['ok'], 'a verbatim raw-body quote between a < b and a later -> survives HTML normalization')
            for url in ('https://example.invalid/{a,b}', 'https://example.invalid/[1-2]'):
                manifest[url] = manifest[first_url]
                quote_pages.write_text(json.dumps(manifest))
                with patch.dict(os.environ, quote_env):
                    status, body = module.fetch_source(url)
                self.ensure(status == 200 and phrase in body
                            and json.loads(quote_fetch_log.read_text().splitlines()[-1]) == url,
                            f'curl disables glob expansion for the literal Source URL {url}')
            result = check_quotes(quoted_proposal('“The keeper’s nested evidence survives JSON decoding.”',url=tweet_url))
            self.ensure(result['ok'], 'FxTwitter nested JSON string values decode escaped apostrophes before matching')
            result = check_quotes(quoted_proposal('"These hidden keys are never quoted evidence"',url=tweet_url),expected=1)
            self.ensure(any('These hidden keys' in p for p in result['problems']),
                        'FxTwitter JSON keys cannot count as quoted page text')
            cap_url = 'https://example.invalid/oversized-body'
            cap_page = transport/'oversized.body'
            beyond_cap = 'Maroon observatories sit beyond the retained body'
            cap_page.write_bytes(phrase.encode()+b' '*(5*1024*1024-len(phrase))+beyond_cap.encode())
            manifest[cap_url] = [200,str(cap_page)]
            quote_pages.write_text(json.dumps(manifest))
            result = check_quotes(quoted_proposal(f'"{phrase}"',url=cap_url))
            self.ensure(result['ok'], 'a quote within the retained 5 MiB body prefix passes even when the server ignores bounds')
            result = check_quotes(quoted_proposal(f'"{beyond_cap}"',url=cap_url),expected=1)
            self.ensure(any(beyond_cap in p for p in result['problems']),
                        'page text beyond the 5 MiB body cap cannot prove a quote')
            with patch.dict(quote_env,QUOTE_CURL_EXIT='7'):
                result = check_quotes(quoted_proposal(f'"{phrase}"'),expected=1)
            self.ensure(any('unreachable' in p for p in result['problems']),
                        'a nonzero curl exit cannot accept an otherwise matching body and HTTP 200 header')
            start = time.monotonic()
            with patch.dict(os.environ,dict(quote_env,QUOTE_MAX_TIME='0.1',QUOTE_CURL_DELAY='3')):
                with patch.object(module,'FETCH_SECONDS',0.1):
                    timed_out = module.fetch_source(first_url)
            self.ensure(timed_out == (0,'') and time.monotonic()-start < 2.5,
                        'a stalled transport is killed within the fetch deadline even when the stub ignores --max-time')
            before_quote_fetch = quote_fetch_log.read_text()
            result = check_quotes(quoted_proposal(f'"{wrong_phrase}"'),fetch=False)
            output = self.run(checker,quote_file,'--skills-dir',quote_root/'skills',env=quote_env)
            self.ensure(result['ok'] and result['quotes_checked'] is False
                        and 'quotes not checked (no --fetch)' in output and quote_fetch_log.read_text() == before_quote_fetch,
                        'without --fetch quotes are unchecked, the report says so, and no transport is invoked')

            # Real subprocesses, stub seats and a stub curl exercise the panel without any network or model CLI.
            panel = self.scratch/'panel'
            panel.mkdir()
            prompt = panel/'prompt.md'
            prompt.write_text('Read this exact prompt and return a proposal fragment.\n')
            no_proposals = panel/'no-proposals.md'
            no_proposals.write_text('No proposals.\n')
            empty = panel/'empty.md'
            empty.write_text('')
            unstructured = panel/'unstructured.md'
            unstructured.write_text('This fragment has no proposal or explicit outcome.\n')
            bin_dir = panel/'bin'
            bin_dir.mkdir()
            fetch_log = panel/'fetched.jsonl'
            curl = bin_dir/'curl'
            curl.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nimport json, os, sys\n'
                            'with open(os.environ["PANEL_FETCH_LOG"], "a") as stream:\n'
                            '    stream.write(json.dumps(sys.argv[-1])+"\\n")\n'
                            'status = 403 if os.environ.get("PANEL_FAIL_FETCH") == sys.argv[-1] else 200\n'
                            'Path(sys.argv[sys.argv.index("-D")+1]).write_text(f"HTTP/1.1 {status} Fixture\\r\\n\\r\\n")\n'
                            'sys.stdout.write("A stubbed panel source body.")\n')
            curl.chmod(0o755)
            panel_env = dict(env,PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),
                             PANEL_FETCH_LOG=str(fetch_log),CURATOR_TIMEOUT='10',
                             POTETO_CACHE=str(panel/'unused-cache'),POTETO_UPSTREAM_URL='invalid-panel-upstream')
            config = panel/'seats.json'
            seat_code = ('from pathlib import Path; import sys; '
                         'Path(sys.argv[2]).write_text(sys.stdin.read()); '
                         'sys.stderr.write("seat diagnostics\\n"); '
                         'sys.stdout.write(Path(sys.argv[1]).read_text()); sys.exit(int(sys.argv[3]))')
            def seat(name, vendor, fixture, code=0):
                return {'name':name,'vendor':vendor,
                        'argv':[sys.executable,'-c',seat_code,str(fixture),str(panel/f'{name}.stdin'),str(code)]}
            panel_runs = 0
            def run_panel(seats, expected=0, extra=(), environment=None):
                nonlocal panel_runs
                panel_runs += 1
                date = f'2026-11-{panel_runs:02d}'
                config.write_text(json.dumps(seats))
                output = self.run(self.script(self.name,'curate.sh'),'--panel',prompt,'--panel-config',config,
                                  '--repo',collection,'--date',date,*extra,env=environment or panel_env,expected=expected)
                self.ensure(not (collection/f'logs/curator/{date}.upstream.json').exists(),
                            f'panel run {panel_runs} does not invoke the upstream diff stage')
                return date, output
            good_seats = [seat('claude-stub','anthropic',fixtures/'fragment-h3-valid.md'),
                          seat('codex-stub','openai',no_proposals)]
            date, _ = run_panel(good_seats)
            panel_log = collection/f'logs/curator/{date}.log'
            records = [json.loads(line) for line in panel_log.read_text().splitlines() if line.startswith('{')]
            for entry in good_seats:
                name = entry['name']
                self.ensure((panel/f'{name}.stdin').read_text() == prompt.read_text(),
                            f'{name} receives the exact prompt on stdin')
                self.ensure((collection/f'logs/curator/{date}.{name}-x-propose.md').read_text()
                            == Path(entry['argv'][-3]).read_text()
                            and (collection/f'logs/curator/{date}.{name}-x-propose.err').read_text() == 'seat diagnostics\n',
                            f'{name} saves the fragment and separate stderr artifact')
                self.ensure(any((r.get('name') or r.get('seat')) == name and r['vendor'] == entry['vendor']
                                and r['argv'] == entry['argv'] and r['exit_code'] == 0 and r['check_exit_code'] == 0
                                for r in records), f'{name} logs vendor, argv, command exit, and gate exit')
            self.ensure([json.loads(line) for line in fetch_log.read_text().splitlines()]
                        == ['https://example.invalid/first','https://example.invalid/second'],
                        'panel invokes the fragment fetch gate on every H3 URL with stubbed transport')
            self.ensure((collection/f'logs/curator/{date}.codex-stub-x-propose.md').read_text() == 'No proposals.\n',
                        'a fragment containing only No proposals. passes the panel gate')
            panel_module = load_module(self.script(self.name,'run_panel.py'),'example_panel_cleanup')
            for number, error in enumerate((PermissionError, ProcessLookupError), 1):
                cleanup_date = f'2026-12-{number:02d}'
                config.write_text(json.dumps([seat('finished-seat','anthropic',no_proposals),
                                              seat('finished-peer','openai',no_proposals)]))
                with patch.dict(os.environ,panel_env), patch.object(panel_module.os,'killpg',side_effect=error) as killpg:
                    code = panel_module.main([str(prompt),'--panel-config',str(config),
                                              '--repo',str(collection),'--date',cleanup_date])
                records = [json.loads(line) for line in (collection/f'logs/curator/{cleanup_date}.log').read_text().splitlines()
                           if line.startswith('{')]
                self.ensure(code == 0 and killpg.call_count == 2 and len(records) == 2
                            and all(r['exit_code'] == 0 and r['check_exit_code'] == 0 for r in records),
                            f'{error.__name__} during finished-seat cleanup preserves exit 0 instead of reporting exit 127')
            for name, fixture, code in (('empty-seat',empty,0), ('unstructured-seat',unstructured,0),
                                        ('banana-seat',fixtures/'fragment-h3-banana.md',0),
                                        ('crashed-seat',no_proposals,23)):
                date, output = run_panel([seat(name,'anthropic',fixture,code),
                                          seat('later-seat','openai',no_proposals)],expected=1)
                self.ensure(name in output and (collection/f'logs/curator/{date}.later-seat-x-propose.md').is_file(),
                            f'{name} exits 1 naming the failed seat and still runs the later seat')
            same_vendor = [seat('one-vendor-a','openai',no_proposals),seat('one-vendor-b','openai',no_proposals)]
            before_fetch = fetch_log.read_text()
            _, output = run_panel(same_vendor,expected=2)
            self.ensure('--allow-single-vendor' in output and all(not (panel/f"{s['name']}.stdin").exists() for s in same_vendor)
                        and fetch_log.read_text() == before_fetch,
                        'same-vendor panel exits 2 before any seat or fetch and names the override fix')
            run_panel(same_vendor,extra=('--allow-single-vendor',))
            self.ensure(all((panel/f"{s['name']}.stdin").is_file() for s in same_vendor),
                        '--allow-single-vendor explicitly enables the same-vendor panel')
            _, output = run_panel(good_seats,expected=1,
                                  environment=dict(panel_env,PANEL_FAIL_FETCH='https://example.invalid/second'))
            self.ensure('claude-stub' in output and '403' in output,
                        'panel names the failed seat when its second H3 source cannot be fetched')
            preflight = seat('preflight-first','anthropic',no_proposals)
            for malformed_seat, fix in (
                ({'name':'later-invalid','vendor':'openai','argv':'not-an-array'},'argv'),
                (seat('PREFLIGHT-FIRST','openai',no_proposals),'duplicate'),
                (seat('../invalid-name','openai',no_proposals),'name'),
            ):
                date, output = run_panel([preflight,malformed_seat],expected=2)
                self.ensure(fix in output and not (panel/'preflight-first.stdin').exists()
                            and not (collection/f'logs/curator/{date}.log').exists(),
                            f'invalid later seat {fix} fails preflight before an earlier seat or log is started')
            for flag in ('--offline','--no-fetch'):
                _, output = run_panel([preflight,seat('preflight-second','openai',no_proposals)],
                                      extra=(flag,),expected=2)
                self.ensure(flag in output and not (panel/'preflight-first.stdin').exists(),
                            f'panel rejects {flag} before running seats instead of silently skipping source fetches')
            override_args = panel/'override-argv.json'
            shell_sentinel = panel/'shell-must-not-run'
            override_seat = {'name':'override-seat','vendor':'anthropic','argv':[
                sys.executable,'-c',
                'from pathlib import Path; import json, sys; sys.stdin.read(); '
                'Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); print("No proposals.")',
                str(override_args),'${CURATOR_MODEL:-default-model}','${CURATOR_EFFORT:-default-effort}',
                'prefix-${CURATOR_MODEL:-unchanged}']}
            override_value = f'model with spaces $(touch {shell_sentinel})'
            for model, effort, expected_args in (
                (override_value,'high effort',[override_value,'high effort','prefix-${CURATOR_MODEL:-unchanged}']),
                ('','',['default-model','default-effort','prefix-${CURATOR_MODEL:-unchanged}']),
            ):
                run_panel([override_seat,seat('override-peer','openai',no_proposals)],
                          environment=dict(panel_env,CURATOR_MODEL=model,CURATOR_EFFORT=effort))
                self.ensure(json.loads(override_args.read_text()) == expected_args and not shell_sentinel.exists(),
                            'panel model and effort overrides/defaults expand only whole argv tokens without shell evaluation')
            child_started = panel/'timeout-child-started'
            late_marker = panel/'timeout-child-late'
            child_code = ('from pathlib import Path; import sys, time; '
                          'Path(sys.argv[1]).write_text("started"); time.sleep(2); '
                          'Path(sys.argv[2]).write_text("escaped timeout"); print("late child output", flush=True)')
            parent_code = ('import subprocess, sys, time; '
                           'subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2],sys.argv[3]]); '
                           'print("No proposals.", flush=True); time.sleep(10)')
            timeout_seat = {'name':'timeout-seat','vendor':'anthropic','argv':[
                sys.executable,'-c',parent_code,child_code,str(child_started),str(late_marker)]}
            date, output = run_panel([timeout_seat,seat('timeout-peer','openai',no_proposals)],expected=1,
                                     environment=dict(panel_env,CURATOR_TIMEOUT='1'))
            records = [json.loads(line) for line in (collection/f'logs/curator/{date}.log').read_text().splitlines()
                       if line.startswith('{')]
            self.ensure('timeout-seat' in output and child_started.is_file()
                        and any(r.get('name') == 'timeout-seat' and r['exit_code'] == 124 for r in records),
                        'a seat that already spawned a child times out with exit 124 and a named failure')
            fragment = collection/f'logs/curator/{date}.timeout-seat-x-propose.md'
            checked_bytes = fragment.read_bytes()
            time.sleep(2.3)
            self.ensure(not late_marker.exists() and fragment.read_bytes() == checked_bytes,
                        'seat timeout kills descendants so a child cannot mutate the validated fragment afterwards')
            for status in (401,403,429):
                with patch.object(module,'fetch_source',lambda url, status=status: (status,'')):
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

    def review_with_item_provenance(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'with_item_scan')
        failures = []
        for route in ('/var/www', '/Users/shared', '/opt/service'):
            probe = f'if path == {route!r}: pass'
            negatives = [
                'path = filesystem\nwith suppress(PermissionError), denied(), nullcontext(path := self.path): pass\n' + probe,
                'path = self.path\nwith suppress(PermissionError), denied(), nullcontext(path := filesystem): pass\n' + probe,
                'path = filesystem\nasync with suppress_error(), denied(), context(path := self.path): pass\n' + probe,
            ]
            positives = [
                'path = filesystem\nwith nullcontext(path := self.path): pass\n' + probe,
                'path = filesystem\nwith suppress(PermissionError), nullcontext(path := self.path):\n    ' + probe,
                'path = filesystem\nwith suppress(PermissionError), nullcontext(path := self.path), nullcontext(path == ' + repr(route) + '): pass',
                'path = filesystem\nwith nullcontext(path := self.path), denied(): pass\n' + probe,
                'path = filesystem\nwith suppress(PermissionError), denied(), nullcontext(path := self.path): pass\npath = self.path\n' + probe,
            ]
            for expected, sources in ((set(), negatives), ({('GET', route)}, positives)):
                for source in sources:
                    body = 'class Handler:\n    async def do_GET(self):\n' + ''.join(
                        '        ' + line + '\n' for line in source.splitlines())
                    info = scanner.analyze_python('server.py', body)
                    routes = {(r['method'], r['path']) for r in info['routes']}
                    if routes != expected or info['notes']:
                        failures.append((source, sorted(routes), info['notes']))
        root = self.scratch / 'with-item-route-map'
        root.mkdir()
        (root / 'server.py').write_text(
            'from contextlib import suppress, nullcontext\n'
            'def denied(): raise PermissionError("denied")\n'
            'class Handler:\n    def do_GET(self):\n'
            '        path = "/var/www"\n'
            '        with suppress(PermissionError), denied(), nullcontext(path := self.path): pass\n'
            '        if path == "/var/www": pass\n'
            '        if self.path == "/api/items": pass\n'
            '        return path\n')
        self.run(sys.executable, '-c', 'from server import Handler; h = Handler(); h.path = "/api/items"; '
                 'assert h.do_GET() == "/var/www"', cwd=root)
        skill = root / '.agents/skills/verify-items'
        (skill / 'features').mkdir(parents=True)
        (skill / 'features/README.md').write_text('[Items](items.md)\n')
        (skill / 'features/items.md').write_text('# Items\n\n## Sub-features\nGET /api/items\n\n'
                                                '## How to get to it (user POV)\n\n'
                                                '## Driving it with control.sh\n\n## Gotchas\n')
        drift = self.json(self.script('maintain-verification-skill', 'drift_check.py'), skill, '--repo', root,
                          '--json', expected=(0, 1))
        if self.last_returncode != 0 or drift['drift'] or drift['routes']['source'] != 1:
            failures.append(('unentered context item must not create a route or drift', self.last_returncode, drift))
        self.ensure(not failures, f'with items establish path provenance only along an evaluated prefix: {failures}')

    def review_exception_path_provenance(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'exception_path_scan')
        prefix = 'class Handler:\n    def do_GET(self):\n'
        probe = 'if path == "/var/www": pass'
        negatives = [
            'path = filesystem\ntry:\n    denied()\n    path = self.path\nexcept PermissionError:\n    ' + probe,
            'path = self.path\ntry:\n    path = filesystem\n    denied()\nexcept PermissionError:\n    ' + probe,
            'path = filesystem\ntry:\n    denied()\n    path = self.path\nexcept PermissionError: pass\n' + probe,
            'path = filesystem\ntry:\n    denied()\n    path = self.path\nfinally:\n    ' + probe,
            'path = filesystem\nfor item in []:\n    path = self.path\n' + probe,
            'path = filesystem\nwhile ready:\n    path = self.path\n' + probe,
            'path = filesystem\nwith suppress(PermissionError):\n    denied()\n    path = self.path\n' + probe,
            'path = filesystem\n[(path := self.path) for item in []]\n' + probe,
        ]
        positives = [
            'try:\n    path = self.path\n    ' + probe + '\nexcept PermissionError: pass',
            'try:\n    denied()\nexcept PermissionError:\n    path = self.path\n    ' + probe,
            'try:\n    path = self.path\nexcept PermissionError: pass\nelse:\n    ' + probe,
            'try:\n    denied()\nfinally:\n    path = self.path\n' + probe,
            'for item in values:\n    path = self.path\n    ' + probe,
        ]
        failures = []
        for expected, sources in ((set(), negatives), ({('GET', '/var/www')}, positives)):
            for source in sources:
                body = prefix + ''.join('        ' + line + '\n' for line in source.splitlines())
                info = scanner.analyze_python('server.py', body)
                routes = {(r['method'], r['path']) for r in info['routes']}
                if routes != expected or info['notes']:
                    failures.append((source, sorted(routes), info['notes']))
        root = self.scratch / 'exception-route-map'
        root.mkdir()
        source = ('def denied(): raise PermissionError("denied")\n' + prefix
                  + '        path = "/var/www"\n        try:\n            denied()\n            path = self.path\n'
                  + '        except PermissionError:\n            if path == "/var/www": pass\n'
                  + '        if self.path == "/api/items": pass\n')
        (root / 'server.py').write_text(source)
        skill = root / '.agents/skills/verify-items'
        (skill / 'features').mkdir(parents=True)
        (skill / 'features/README.md').write_text('[Items](items.md)\n')
        (skill / 'features/items.md').write_text('# Items\n\n## Sub-features\nGET /api/items\n\n'
                                                '## How to get to it (user POV)\n\n'
                                                '## Driving it with control.sh\n\n## Gotchas\n')
        self.run(sys.executable, '-c', 'from server import Handler; h = Handler(); h.path = "/api/items"; h.do_GET()', cwd=root)
        drift = self.json(self.script('maintain-verification-skill', 'drift_check.py'), skill, '--repo', root,
                          '--json', expected=(0, 1))
        if self.last_returncode != 0 or drift['drift'] or drift['routes']['source'] != 1:
            failures.append(('correct feature map must exit 0 with only /api/items', self.last_returncode, drift))
        self.ensure(not failures, f'only proven executed assignments establish path provenance across exceptional and optional control flow: {failures}')

    def review_parser_action_bindings(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'parser_binding_scan')
        root = self.scratch / 'parser-binding-map'
        (root / 'app').mkdir(parents=True)
        (root / 'app/__init__.py').write_text('')
        skill = root / '.agents/skills/verify-app'
        (skill / 'features').mkdir(parents=True)
        (skill / 'features/README.md').write_text('[List](list.md)\n')
        (skill / 'features/list.md').write_text('# List\n\n## Sub-features\n\n'
                                               '## How to get to it (user POV)\n\n'
                                               '## Driving it with control.sh\n`python3 -m app --json list`\n\n## Gotchas\n')
        registration = 'parser.register("action", "boolean", argparse.BooleanOptionalAction)\n'
        option = 'parser.add_argument("--json", action="boolean")\n'
        commands = ('parser.add_argument("--output", action="store", default=False)\n'
                    'parser.add_argument("--label", default=False)\n'
                    'commands = parser.add_subparsers(dest="command", required=True)\n'
                    'commands.add_parser("list")\n')
        finish = 'if __name__ == "__main__":\n    print(parser.parse_args())\n'
        prefix = 'import argparse\nparser = argparse.ArgumentParser()\n'
        variants = {
            'global parser': prefix + registration + 'def main():\n    ' + option
                             + 'main()\n' + commands + finish,
            'argument group': prefix + registration + 'out = parser.add_argument_group("output")\n'
                              + option.replace('parser.add_argument', 'out.add_argument') + commands + finish,
            'mutually exclusive group': prefix + 'out = parser.add_mutually_exclusive_group()\n'
                                       + registration.replace('parser.register', 'out.register')
                                       + option + commands + finish,
            'helper argument': 'import argparse\ndef configure(out):\n    '
                               + option.replace('parser.add_argument', 'out.add_argument')
                               + 'parser = argparse.ArgumentParser()\n' + registration
                               + 'configure(parser)\n' + commands + finish,
            'subclass and helper': ('import argparse\nclass Parser(argparse.ArgumentParser):\n'
                                   '    def __init__(self, **kwargs):\n        super().__init__(**kwargs)\n'
                                   '        self.register("action", "boolean", argparse.BooleanOptionalAction)\n'
                                   'def configure(out):\n    ' + option.replace('parser.add_argument', 'out.add_argument')
                                   + 'parser = Parser()\nconfigure(parser)\n' + commands + finish),
            'subclass method helper': ('import argparse\nclass Parser(argparse.ArgumentParser):\n'
                                      '    def __init__(self, **kwargs):\n        super().__init__(**kwargs)\n'
                                      '        self.register("action", "boolean", argparse.BooleanOptionalAction)\n'
                                      '        self.configure()\n'
                                      '    def configure(self):\n        self.add_argument("--json", action="boolean")\n'
                                      'parser = Parser()\n' + commands.replace('parser.add_subparsers(',
                                          'parser.add_subparsers(parser_class=argparse.ArgumentParser, ') + finish),
            'parser alias': prefix + 'out = parser\n' + registration.replace('parser.register', 'out.register')
                            + option + commands + finish,
        }
        failures = []
        for label, source in variants.items():
            (root / 'app/__main__.py').write_text(source)
            self.run(sys.executable, '-m', 'app', '--json', 'list', cwd=root)
            options = scanner.analyze_python('app/__main__.py', source)['parser_options']
            actual = {o['names'][0]: o['takes_value'] for group in options.values() for o in group}
            if actual != {'--json': False, '--output': True, '--label': True}:
                failures.append((label, 'known registered action without bool-default fallback', actual))
            drift = self.json(self.script('maintain-verification-skill', 'drift_check.py'), skill, '--repo', root,
                              '--json', expected=(0, 1))
            if self.last_returncode != 0 or drift['drift'] or drift['commands']['source'] != 1:
                failures.append((label, 'actual --json list must remain mapped', self.last_returncode, drift))
        # A bool default is only the fallback for opaque actions, never for built-in store.
        opaque = ('import argparse\nclass Toggle(argparse.Action):\n'
                  '    def __init__(self, option_strings, dest, **kwargs):\n'
                  '        super().__init__(option_strings, dest, nargs=0, **kwargs)\n'
                  '    def __call__(self, parser, namespace, values, option_string=None):\n'
                  '        setattr(namespace, self.dest, True)\n'
                  'parser = argparse.ArgumentParser()\n'
                  'parser.register("action", "toggle", Toggle)\n'
                  'parser.add_argument("--direct", action=Toggle, default=False)\n'
                  'parser.add_argument("--alias", action="toggle", default=False)\n'
                  'parser.add_argument("--unknown", action="external", default=False)\n'
                  'parser.add_argument("--custom-value", action="external", default="text")\n'
                  'parser.add_argument("--store", action="store", default=False)\n'
                  'parser.add_argument("--append", action="append", default=False)\n'
                  'parser.add_argument("--none", action=None, default=False)\n')
        (root / 'app/__main__.py').write_text(
            ''.join(line + '\n' for line in opaque.splitlines() if 'action="external"' not in line)
            + commands + finish)
        self.run(sys.executable, '-m', 'app', '--direct', '--alias', 'list', cwd=root)
        actual = {o['names'][0]: o['takes_value'] for o in scanner.analyze_python('cli.py', opaque)['parser_options']['parser']}
        if actual != {'--direct': False, '--alias': False, '--unknown': False,
                      '--custom-value': True, '--store': True, '--append': True, '--none': True}:
            failures.append(('direct and registered opaque classes use the same fallback', actual))
        # Preserve the existing inventory of registrations inside ordinary compound suites.
        for label, suite in {
            'try': 'try:\n    REGISTER\nexcept ValueError:\n    pass\n',
            'with': 'from contextlib import nullcontext\nwith nullcontext():\n    REGISTER\n',
            'for': 'for _ in range(1):\n    REGISTER\n',
            'match': 'match \"text\":\n    case \"text\":\n        REGISTER\n',
        }.items():
            source = (prefix + suite.replace('REGISTER', 'parser.register("action", "text", argparse._StoreAction)')
                      + 'parser.add_argument("--payload", action="text", default=False)\n' + finish)
            (root / 'app/__main__.py').write_text(source)
            self.run(sys.executable, '-m', 'app', '--payload', 'list', cwd=root)
            option_info = scanner.analyze_python('cli.py', source)['parser_options']['parser'][0]
            if not option_info['takes_value']:
                failures.append((label, 'registered store in compound suite must still take a value', option_info))
        child = (prefix + 'commands = parser.add_subparsers()\nsub = commands.add_parser("list")\n'
                 + registration.replace('parser.register', 'sub.register')
                 + option.replace('parser.add_argument', 'sub.add_argument')
                 + 'parser.add_argument("--root", action="boolean", default="text")\n')
        options = scanner.analyze_python('cli.py', child)['parser_options']
        actual = {o['names'][0]: o['takes_value'] for group in options.values() for o in group}
        if actual != {'--json': False, '--root': True}:
            failures.append(('subparser has an independent registry', actual))
        # Lexical name matches must not leak a different parser's registry, even with a bool default.
        store = 'parser.register("action", "boolean", argparse._StoreAction)\n'
        checks = {
            'local shadow': prefix + registration + 'def main():\n    parser = argparse.ArgumentParser()\n'
                            + '    ' + store + '    ' + option.replace(')', ', default=False)') + 'main()\n',
            'reassignment': prefix + registration + 'parser = argparse.ArgumentParser()\n' + store
                            + option.replace(')', ', default=False)'),
            'old alias after reassignment': prefix + registration + 'out = parser\n'
                                            + 'parser = argparse.ArgumentParser()\n' + store
                                            + option.replace(')', ', default=False)')
                                            + 'out.add_argument("--old", action="boolean")\n',
            'global reassignment': prefix + registration + 'def reset():\n    global parser\n'
                                   '    parser = argparse.ArgumentParser()\n    ' + store
                                   + 'reset()\n' + option.replace(')', ', default=False)'),
            'lexical shadow before assignment': prefix + registration + 'def main():\n    ' + option
                                                + '    parser = argparse.ArgumentParser()\n',
            'local import shadow': prefix + 'def main():\n    argparse = replacement\n'
                                   '    ' + registration + '    ' + option + 'main()\n',
        }
        for label, source in checks.items():
            options = scanner.analyze_python('cli.py', source)['parser_options']
            actual = {o['names'][0]: o['takes_value'] for group in options.values() for o in group}
            expected = {'--json': True, '--old': False} if label == 'old alias after reassignment' else {'--json': True}
            if actual != expected:
                failures.append((label, actual, expected))
        self.ensure(not failures, f'argparse action registries follow parser bindings across scopes, groups, constructors and helpers: {failures}')

    def parser_call_variants(self, name, variants):
        """Cross-check authored fixtures against real argparse and the complete drift CLI."""
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), name)
        root = self.scratch / name
        (root / 'app').mkdir(parents=True)
        (root / 'app/__init__.py').write_text('')
        skill = root / '.agents/skills/verify-app'
        (skill / 'features').mkdir(parents=True)
        (skill / 'features/README.md').write_text('[List](list.md)\n')
        (skill / 'features/list.md').write_text('# List\n\n## Sub-features\n\n'
                                               '## How to get to it (user POV)\n\n'
                                               '## Driving it with control.sh\n`python3 -m app --json list`\n\n## Gotchas\n')
        finish = ('parser.add_argument("--output", action="store", default=False)\n'
                  'parser.add_argument("--label", default=False)\n'
                  'commands = parser.add_subparsers(dest="command", required=True,\n'
                  '                                 parser_class=argparse.ArgumentParser)\n'
                  'commands.add_parser("list")\n'
                  'if __name__ == "__main__":\n'
                  '    args = parser.parse_args()\n'
                  '    assert args.json is True and args.command == "list"\n')
        failures = []
        for label, body in variants.items():
            self.log.write(f'parser binding variant: {label}\n')
            source = 'import argparse\n' + body + finish
            (root / 'app/__main__.py').write_text(source)
            self.run(sys.executable, '-m', 'app', '--json', 'list', cwd=root)
            # A scan must never import/execute the target, even when following default expressions.
            static_source = 'raise RuntimeError("scanner executed repository code")\n' + source
            options = scanner.analyze_python('app/__main__.py', static_source)['parser_options']
            actual = {o['names'][0]: o['takes_value'] for group in options.values() for o in group}
            if actual != {'--json': False, '--output': True, '--label': True}:
                failures.append((label, 'token consumption', actual))
            (root / 'app/__main__.py').write_text(static_source)
            drift = self.json(self.script('maintain-verification-skill', 'drift_check.py'), skill, '--repo', root,
                              '--json', expected=(0, 1))
            if self.last_returncode != 0 or drift['drift'] or drift['commands']['source'] != 1:
                failures.append((label, 'real --json list must remain mapped', self.last_returncode, drift))
        return failures

    def review_parser_helper_defaults(self):
        prefix = ('parser = argparse.ArgumentParser()\n'
                  'parser.register("action", "boolean", argparse.BooleanOptionalAction)\n')
        option = 'out.add_argument("--json", action="boolean")\n'
        variants = {
            'positional default': prefix + 'def configure(out=parser):\n    ' + option + 'configure()\n',
            'keyword-only default': prefix + 'def configure(*, out=parser):\n    ' + option + 'configure()\n',
            'positional-only default after required arg': prefix
                + 'def configure(required, out=parser, /):\n    ' + option + 'configure(None)\n',
            'default evaluated before local shadow': prefix + 'def configure(out=parser):\n'
                + '    parser = argparse.ArgumentParser()\n    ' + option + 'configure()\n',
            'default captures parser before reassignment': prefix + 'original = parser\n'
                + 'def configure(out=parser):\n    ' + option
                + 'parser = argparse.ArgumentParser()\n'
                + 'parser.register("action", "boolean", argparse._StoreAction)\n'
                + 'configure()\nparser = original\n',
            'explicit positional overrides default': prefix
                + 'def configure(out=None):\n    ' + option + 'configure(parser)\n',
            'explicit keyword overrides default': prefix
                + 'def configure(*, out=None):\n    ' + option + 'configure(out=parser)\n',
            'method positional and keyword-only defaults': prefix
                + 'class Parser(argparse.ArgumentParser):\n'
                + '    def configure(self, action=argparse.BooleanOptionalAction, *, out=parser):\n'
                + '        out.register("action", "boolean", action)\n        ' + option
                + 'helper = Parser()\nhelper.configure()\n',
        }
        failures = self.parser_call_variants('parser-helper-defaults', variants)
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'default_override_scan')
        # An explicitly supplied but statically unknown argument must not fall back to the default parser.
        source = prefix + 'def configure(out=parser):\n    ' + option + 'configure(external_parser)\n'
        options = scanner.analyze_python('cli.py', source)['parser_options']
        if not all(o['takes_value'] for group in options.values() for o in group):
            failures.append(('unknown explicit argument incorrectly reused default', options))
        self.ensure(not failures, f'helper and method defaults retain definition-time parser bindings with explicit overrides: {failures}')

    def review_parser_constructor_arguments(self):
        variants = {}
        for label, signature, call in [
            ('explicit positional', 'self, action', 'argparse.BooleanOptionalAction'),
            ('explicit keyword', 'self, action', 'action=argparse.BooleanOptionalAction'),
            ('keyword-only explicit', 'self, *, action', 'action=argparse.BooleanOptionalAction'),
            ('positional-only self and action', 'self, action, /', 'argparse.BooleanOptionalAction'),
            ('positional default', 'self, action=argparse.BooleanOptionalAction', ''),
            ('keyword-only default', 'self, *, action=argparse.BooleanOptionalAction', ''),
            ('explicit overrides default', 'self, action=argparse._StoreAction', 'argparse.BooleanOptionalAction'),
        ]:
            variants[label] = ('class Parser(argparse.ArgumentParser):\n'
                               f'    def __init__({signature}):\n'
                               '        super().__init__()\n'
                               '        self.register("action", "boolean", action)\n'
                               f'parser = Parser({call})\n'
                               'parser.add_argument("--json", action="boolean")\n')
        variants['constructor default captures action before reassignment'] = (
            'action = argparse.BooleanOptionalAction\n'
            'class Parser(argparse.ArgumentParser):\n'
            '    def __init__(self, action=action):\n'
            '        super().__init__()\n'
            '        self.register("action", "boolean", action)\n'
            'action = argparse._StoreAction\n'
            'parser = Parser()\nparser.add_argument("--json", action="boolean")\n')
        failures = self.parser_call_variants('parser-constructor-arguments', variants)
        self.ensure(not failures, f'direct parser constructors share helper binding for explicit arguments and captured defaults: {failures}')

    def review_registered_boolean_actions(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'registered_action_scan')
        root = self.scratch / 'registered-action-map'
        (root / 'app').mkdir(parents=True)
        (root / 'app/__init__.py').write_text('')
        source = ('import argparse\nparser = argparse.ArgumentParser()\n'
                  'parser.register("action", "boolean", argparse.BooleanOptionalAction)\n'
                  'parser.add_argument("--json", action="boolean", default=False)\n'
                  'parser.add_argument("--output", action="store", default=False)\n'
                  'parser.add_argument("--label", default=False)\n'
                  'commands = parser.add_subparsers(dest="command", required=True)\n'
                  'commands.add_parser("list")\n'
                  'if __name__ == "__main__":\n    print(parser.parse_args())\n')
        (root / 'app/__main__.py').write_text(source)
        skill = root / '.agents/skills/verify-app'
        (skill / 'features').mkdir(parents=True)
        (skill / 'features/README.md').write_text('[List](list.md)\n')
        (skill / 'features/list.md').write_text('# List\n\n## Sub-features\n\n'
                                               '## How to get to it (user POV)\n\n'
                                               '## Driving it with control.sh\n`python3 -m app --json list`\n\n## Gotchas\n')
        self.run(sys.executable, '-m', 'app', '--json', 'list', cwd=root)
        failures = []
        variants = [source,
                    source.replace('import argparse', 'import argparse as ap').replace('argparse.', 'ap.'),
                    source.replace('import argparse', 'import argparse\nfrom argparse import BooleanOptionalAction as Toggle')
                          .replace('argparse.BooleanOptionalAction', 'Toggle'),
                    source.replace('"boolean"', '"toggle"').replace(', default=False)\nparser.add_argument("--output"',
                                                                      ')\nparser.add_argument("--output"')]
        for body in variants:
            options = scanner.analyze_python('app/__main__.py', body)['parser_options']['parser']
            actual = {o['names'][0]: o['takes_value'] for o in options}
            if actual != {'--json': False, '--output': True, '--label': True}:
                failures.append(('registered boolean and ordinary value options', body, actual))
        # An existing value-action registration must not be replaced by another parser's or a later registration.
        for body in (source.replace('parser.register(', 'other.register('),
                     source.replace('parser.register("action", "boolean", argparse.BooleanOptionalAction)\n', '')
                           + 'parser.register("action", "boolean", argparse.BooleanOptionalAction)\n'):
            body = body.replace('parser = argparse.ArgumentParser()\n',
                                'parser = argparse.ArgumentParser()\n'
                                'other = argparse.ArgumentParser()\n'
                                'parser.register("action", "boolean", argparse._StoreAction)\n')
            option = scanner.analyze_python('app/__main__.py', body)['parser_options']['parser'][0]
            if not option['takes_value']:
                failures.append(('registration must precede the option on the same parser', body, option))
        drift = self.json(self.script('maintain-verification-skill', 'drift_check.py'), skill, '--repo', root,
                          '--json', expected=(0, 1))
        if self.last_returncode != 0 or drift['drift'] or drift['commands']['source'] != 1:
            failures.append(('registered boolean leaves list mapped and exits 0', self.last_returncode, drift))
        self.ensure(not failures, f'registered no-value argparse actions retain their token consumption and clean feature maps: {failures}')

    def review_expression_paths(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'expression_path_scan')
        prefix = 'class Handler:\n    def do_GET(self):\n'
        probe = 'if path.startswith("/var/www/"): pass'
        negatives = [
            'path = self.translate_path(self.path)\nif api_mode and (path := self.path): return\n' + probe,
            'path = filesystem\napi_mode or (path := self.path)\n' + probe,
            'path = filesystem\n(path := self.path) if api_mode else None\n' + probe,
            'path = filesystem\nNone if api_mode else (path := self.path)\n' + probe,
            'path = self.path\napi_mode and (path := filesystem)\n' + probe,
            'path = self.path\napi_mode or (path := filesystem)\n' + probe,
            'path = self.path\n(path := filesystem) if api_mode else None\n' + probe,
            'path = self.path\nNone if api_mode else (path := filesystem)\n' + probe,
            '(path := self.path) if api_mode else path.startswith("/var/www/")',
            'api_mode and ((path := self.path) if alternate else None)\n' + probe,
            'path = filesystem\n(path := filesystem) if (path := self.path) else None\n' + probe,
            'path = filesystem\nNone if (path := self.path) else (path := filesystem)\n' + probe,
        ]
        positives = [
            '(path := self.path) and api_mode\n' + probe,
            '(path := self.path) or api_mode\n' + probe,
            'None if (path := self.path) else None\n' + probe,
            'api_mode and (path := self.path) and path.startswith("/var/www/")',
            'api_mode or (path := self.path) or path.startswith("/var/www/")',
            '((path := self.path) and path.startswith("/var/www/")) if api_mode else None',
            'None if api_mode else ((path := self.path) and path.startswith("/var/www/"))',
            'api_mode and (path := filesystem)\npath = self.path\n' + probe,
            'path = filesystem\nif api_mode and (path := self.path):\n    ' + probe,
            'path = filesystem\nif api_mode or (path := self.path): pass\nelse:\n    ' + probe,
            'path = filesystem\nif not (api_mode or (path := self.path)):\n    ' + probe,
            'path = filesystem\npath.startswith("/var/www/") if (path := self.path) else None',
            'path = filesystem\npath.startswith("/var/www/") if api_mode and (path := self.path) else None',
            'path = filesystem\nNone if api_mode or (path := self.path) else path.startswith("/var/www/")',
            'path = self.path\n(path := filesystem) if path.startswith("/var/www/") else None',
            'path = filesystem\nmatch mode:\n    case "url" if api_mode and (path := self.path):\n        ' + probe,
        ]
        failures = []
        for expected, sources in ((set(), negatives), ({('GET', '/var/www/<*>')}, positives)):
            for source in sources:
                body = prefix + ''.join('        ' + line + '\n' for line in source.splitlines())
                info = scanner.analyze_python('server.py', body)
                routes = {(r['method'], r['path']) for r in info['routes']}
                if routes != expected or info['notes']:
                    failures.append((source, sorted(routes), info['notes']))
        self.ensure(not failures, f'short-circuit and conditional assignments revoke uncertain joins but preserve evaluated aliases: {failures}')

    def review_query_path_aliases(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'query_path_scan')
        prefix = 'class Handler:\n    def do_GET(self):\n'
        positives = [
            "path = self.path.split('?')[0]",
            "path = self.path.split('?', 1)[0]",
            "path = self.path.partition('?')[0]",
            "path, query = self.path.split('?', 1)",
            "path, query = self.path.split('?')",
            "path, sep, query = self.path.partition('?')",
            "[path, sep, query] = self.path.partition('?')",
            "raw = self.path\npath, sep, query = raw.partition('?')",
            "path = request.path_info.split('?', 1)[0]",
            "path = self.path.partition('#')[0]",
            "path = self.path.split('?')[0]\nparts = path.strip('/').split('/')",
        ]
        negatives = [
            "path = self.translate_path(self.path).split('?')[0]",
            "path, sep, query = self.translate_path(self.path).partition('?')",
            "path = self.path.partition('?')[2]",
            "path = self.path.split('?', 1)[1]",
            "path = self.path.split('/')[0]",
            "path = self.path.split(separator)[0]",
            "path = self.path.rsplit('?', 1)[0]",
            "query, sep, path = self.path.partition('?')",
        ]
        failures = []
        for expected, sources in (({('GET', '/home'), ('GET', '/Users')}, positives), (set(), negatives)):
            for source in sources:
                body = prefix + ''.join('        ' + line + '\n' for line in source.splitlines())
                body += '        if path == "/home": pass\n        if path == "/Users": pass\n'
                info = scanner.analyze_python('server.py', body)
                routes = {(r['method'], r['path']) for r in info['routes']}
                if routes != expected or info['notes']:
                    failures.append((source, sorted(routes), info['notes']))
        self.ensure(not failures, f'query and fragment stripping retain request-path aliases, including unpacking, without blessing disk paths or query elements: {failures}')

    def review_go_receiver_workload(self):
        scanner = load_module(self.script('create-verification-skill', 'scan_repo.py'), 'go_receiver_work_scan')
        original_search = scanner.re.search
        samples, failures = [], []
        for count in (100, 200, 400, 800):
            source = 'package main\nfunc main() {\n' + ''.join(
                f'router.GET("/home/{i}", handler)\n' for i in range(count)) + '}\n'
            metrics = {'n': count, 'receiver_searches': 0, 'searched_characters': 0}

            def search(pattern, text, *args, **kwargs):
                if pattern == r'\b(\w+)\s*$':
                    metrics['receiver_searches'] += 1
                    metrics['searched_characters'] += len(text)
                return original_search(pattern, text, *args, **kwargs)

            started = time.monotonic()
            with patch.object(scanner.re, 'search', search):
                retained = [match.group(2) for match in scanner.GO_ROUTER.finditer(source)
                            if scanner.go_route_receiver(source, match.start(), scanner.GO_ROUTER_RECEIVERS)]
            metrics['seconds'] = round(time.monotonic() - started, 6)
            samples.append(metrics)
            if retained != [f'/home/{i}' for i in range(count)]:
                failures.append(('route inventory', count, len(retained)))
            if metrics['searched_characters'] > count * 512:
                failures.append(('unbounded receiver work', metrics))
        # Long unrelated prefixes must not turn field or identifier suffixes into trusted receivers.
        for source, expected in [('router.GET', True), ('s.router.GET', False),
                                 ('x' * 300 + 'router.GET', False),
                                 ('s.' + ' ' * 300 + 'router.GET', False),
                                 ('x' * 300 + '\nrouter.GET', True)]:
            actual = scanner.go_route_receiver(source, source.rindex('.'), scanner.GO_ROUTER_RECEIVERS)
            if actual != expected:
                failures.append(('receiver boundary', source[-40:], expected, actual))
        self.log.write('GO RECEIVER WORKLOAD ' + json.dumps(samples) + '\n')
        self.log.flush()
        if samples[-1]['searched_characters'] > samples[0]['searched_characters'] * 10:
            failures.append(('eightfold routes exceed near-linear receiver work', samples))
        self.ensure(not failures, f'Go receiver lookup uses bounded work per route and preserves conventional receivers: {failures}')

    def review_net_change_docs(self):
        """The public budget API is new; branch-only names are not a main migration."""
        public = {
            'CHANGELOG.md': (ROOT / 'CHANGELOG.md').read_text().split('## [0.1.0]', 1)[0],
            'MAPPING.md': (ROOT / 'MAPPING.md').read_text(),
            'formal SKILL.md': (ROOT / 'skills/formal-verification/SKILL.md').read_text().split('## Sources', 1)[0],
        }
        for checker in ('gen_check.py', 'bounded_check.py'):
            public[checker + ' --help'] = self.run(sys.executable, self.script('formal-verification', checker), '--help')
        failures = [name for name, text in public.items()
                    if not all(api in text for api in ('--timeout', '--call-timeout', 'CHECK_TIMEOUT'))
                    or re.search(r'\bTIMEOUT\b', text)]
        self.ensure(not failures,
                    f'public docs/help describe CHECK_TIMEOUT and the added limits without a nonexistent TIMEOUT migration: {failures}')
        changelog = public['CHANGELOG.md']
        self.ensure(not re.search(r'(?im)^- (?:Round \d|(?:Final )?[Rr]e-review|Follow-up review|Curator review fixes)', changelog)
                    and all(word in changelog for word in ('overhead', 'main', '90 s', 'exit 3', 'mutant', 'exit 2', 'untracked', 'empty')),
                    'Unreleased records net behavior, default-limit overhead and incompatible exits rather than branch review rounds')


    def review_infrastructure_exception_format(self):
        """Import/hook failures stay errors when formatting exceeds the run deadline."""
        failures = []
        def fixture(checker, phase, error):
            if phase == 'import':
                return error + 'raise SlowInfrastructureError()\n'
            body = ('from gen_check import Generator\n' + error +
                    'TARGETS = ["todo/store.py:Store.add"]\n'
                    'def broken(*args): raise SlowInfrastructureError()\n')
            if phase == 'profiling':
                body += ('import gen_check\n'
                         'class BrokenPath:\n    def __init__(self, *args): pass\n'
                         '    def resolve(self): raise SlowInfrastructureError()\n'
                         'def setup(): gen_check.Path = BrokenPath\n')
            if phase in ('setup', 'reset'):
                body += phase + ' = broken\n'
            draw = 'broken' if phase == 'generation' else 'lambda rng: 7'
            shrink = 'broken' if phase == 'shrink' else 'lambda value: []'
            check = 'lambda value: False' if phase == 'shrink' else 'lambda value: True'
            return body + f'properties = {{"probe": (Generator({draw}, {shrink}), {check})}}\n'

        for checker, phases in (('gen_check.py', ('import', 'setup', 'reset', 'generation', 'shrink', 'profiling')),
                                ('bounded_check.py', ('import',))):
            for phase in phases:
                for tick in (True, False):
                    error = ('import review_clock as clock\n'
                             'class SlowInfrastructureError(Exception):\n    def __str__(self):\n'
                             '        clock.events.append([clock.limits.recording, clock.limits.started])\n'
                             f'        clock.advance(20, tick={tick})\n        return "slow diagnostic"\n')
                    try:
                        result = self.clock_check(checker, fixture(checker, phase, error), run=10, call=0,
                                                  flags=('--json',))
                    except AssertionError as error:
                        failures.append((checker, phase, tick, str(error)))
                        continue
                    diagnostic = result['stdout'] + result['stderr']
                    valid = (result['code'] == 2 and 'SlowInfrastructureError' in diagnostic
                             and result['events'] == [[0, None]])
                    if checker == 'gen_check.py':
                        report = json.loads(result['stdout'])
                        valid = valid and report.get('result') == 'error' and not report['ok']
                    if not valid:
                        failures.append((checker, phase, tick, result))
            # Infinite messages exercise real SIGALRM as well as the manual-clock paths.
            for phase in (('import', 'setup') if checker == 'gen_check.py' else ('import',)):
                error = ('class SlowInfrastructureError(Exception):\n    def __str__(self):\n'
                         '        while True: pass\n')
                source = self.art / (phase + '_' + checker)
                source.write_text(fixture(checker, phase, error))
                args = ('--repo', self.repo, '--examples', '1') if checker == 'gen_check.py' else ()
                try:
                    diagnostic = self.run(sys.executable, self.script('formal-verification', checker), source,
                                          *args, '--timeout', '.05', '--call-timeout', '0', '--json',
                                          expected=(0, 1, 2, 3), timeout=1.2)
                except subprocess.TimeoutExpired:
                    failures.append((checker, phase, 'infinite message exceeded the external 1.2 s guard'))
                    continue
                if self.last_returncode != 2 or 'SlowInfrastructureError' not in diagnostic:
                    failures.append((checker, phase, self.last_returncode, diagnostic))
        # Deliver a pending alarm exactly after recording the import error's safe detail.
        body = ('import review_clock as clock\nfrom contextlib import contextmanager\n'
                'original_record = clock.limits.record_result\n'
                '@contextmanager\ndef record():\n    with original_record():\n'
                '        yield\n        clock.advance(20)\n'
                'clock.limits.record_result = record\n'
                'class RecordedImportError(Exception): pass\nraise RecordedImportError("known failure")\n')
        result = self.clock_check('bounded_check.py', body, run=10, call=0, flags=('--json',))
        if result['code'] != 2 or 'RecordedImportError' not in result['stdout'] + result['stderr']:
            failures.append(('bounded import record-to-format alarm', result))
        self.ensure(not failures, f'import and infrastructure errors retain exit 2 and safe diagnostics during interrupted formatting: {failures}')

    def review_exception_format_timeout(self):
        """An exception is a confirmed finding before its user-defined message is rendered."""
        target = self.repo / 'exception_format_target.py'
        target.write_text('def checked(value): return False\n')
        failures = []
        error = ('class SlowError(Exception):\n    def __str__(self):\n'
                 '        clock.events.append([clock.limits.recording, clock.limits.started])\n'
                 '        clock.advance(20)\n        return "slow message"\n')
        try:
            for phase in ('initial', 'shrink'):
                body = ('import review_clock as clock\nfrom gen_check import Generator\n'
                        'from exception_format_target import checked\n' + error +
                        'TARGETS = ["exception_format_target.py:checked"]\n'
                        'def check(value):\n    checked(value)\n'
                        '    if value == 42: return False\n    raise SlowError()\n'
                        'properties = {"broken": (Generator(lambda rng: ' + ('42' if phase == 'shrink' else '7') +
                        ', lambda value: [7] if value == 42 else []), check)}\n')
                result = self.clock_check('gen_check.py', body, run=10, call=0, flags=('--json',))
                report = json.loads(result['stdout'])
                found = report['properties']['broken']
                if not (result['code'] == 1 and result['events'] == [[0, None]]
                        and found['result'] == 'fail' and found['counterexample'] == '7'
                        and found['accepted'] == 1 and 'SlowError' in (found['detail'] or '')
                        and found.get('output_incomplete') and report['incomplete']
                        and report['called'] == ['exception_format_target.py:checked'] and not report['missing_targets']):
                    failures.append(('property', phase, result))
            for phase in ('invariant', 'actions', 'mutant'):
                body = ('import review_clock as clock\n' + error +
                        'def init(): return 7\ndef broken(s): raise SlowError()\n' +
                        ('def actions(s): return []\ninvariants = {"broken": broken}\n' if phase == 'invariant' else
                         'actions = broken\ninvariants = {"ok": lambda s: True}\n' if phase == 'actions' else
                         'def actions(s): return []\ninvariants = {"ok": lambda s: True}\n'
                         'mutants = {"crashed": broken, "later": lambda s: []}\n'))
                flags = ('--mutants', '--json') if phase == 'mutant' else ('--json',)
                result = self.clock_check('bounded_check.py', body, run=10, call=0, flags=flags)
                report = json.loads(result['stdout'])
                found = report['mutants']['crashed'] if phase == 'mutant' else report
                trace = found.get('trace') if phase == 'mutant' else (found['violation'] or {}).get('trace')
                if not (result['code'] == 2 and result['events'] == [[0, None]] and found['result'] == 'error'
                        and 'SlowError' in (found['detail'] or '') and found['states'] == 1
                        and trace and trace[0]['state'] == 7 and report['incomplete']
                        and (phase != 'mutant' or report['not_run'] == ['later'])):
                    failures.append(('model', phase, result))
            # Real SIGALRM proves that an infinite __str__ cannot block the checker indefinitely.
            for checker in ('gen_check.py', 'bounded_check.py'):
                fixture = self.art / ('exception_' + checker)
                body = 'class SlowError(Exception):\n    def __str__(self):\n        while True: pass\n'
                if checker == 'gen_check.py':
                    body += ('from gen_check import Generator\nfrom exception_format_target import checked\n'
                             'TARGETS = ["exception_format_target.py:checked"]\n'
                             'def broken(value):\n    checked(value)\n    raise SlowError()\n'
                             'properties = {"broken": (Generator(lambda rng: 7, lambda value: []), broken)}\n')
                    flags, expected = ('--repo', self.repo, '--examples', '1'), 1
                else:
                    body += 'def init(): return 7\ndef actions(s): raise SlowError()\ninvariants = {"ok": lambda s: True}\n'
                    flags, expected = (), 2
                fixture.write_text(body)
                try:
                    report = self.json(sys.executable, self.script('formal-verification', checker), fixture, *flags,
                                       '--timeout', '0.5', '--call-timeout', '0', '--json', expected=(expected, 3), timeout=8)
                except subprocess.TimeoutExpired:
                    failures.append((checker, 'infinite exception formatting exceeded the external guard'))
                    continue
                found = report['properties']['broken'] if checker == 'gen_check.py' else report
                if not (self.last_returncode == expected and found['result'] == ('fail' if expected == 1 else 'error')
                        and 'SlowError' in (found['detail'] or '') and report['incomplete']):
                    failures.append((checker, self.last_returncode, report))
            self.ensure(not failures, f'exception formatting preserves confirmed failures, coverage and diagnostics outside recording: {failures}')
        finally:
            target.unlink()

    def review_timeout_format_handoff(self):
        body = ('import review_clock as clock\nfrom gen_check import Generator\n'
                'TARGETS = ["todo/store.py:Store.add"]\n'
                'class Value:\n    def __repr__(self):\n        clock.advance(20)\n        return "value"\n'
                'def check(x): clock.advance(2)\n'
                'properties = {"slow": (Generator(lambda rng: Value(), lambda x: []), check)}\n')
        failures = []
        for flags in ((), ('--expect-fail', 'slow')):
            result = self.clock_check('gen_check.py', body, run=10, call=1, flags=(*flags, '--json'))
            report = json.loads(result['stdout'])
            found = report['properties']['slow']
            if result['code'] != 3 or found['result'] != 'timeout' or found['accepted'] != 0 or report['ok']:
                failures.append(result)
        self.ensure(not failures, f'a run timeout while rendering a call timeout remains inconclusive with no accepted input: {failures}')

    def review_interruptible_result_format(self):
        target = self.repo / 'format_target.py'
        target.write_text('def checked(value): return False\n')
        failures = []
        # A manual signal verifies that no user representation runs with alarms deferred.
        for checker in ('gen_check.py', 'bounded_check.py'):
            body = ('import review_clock as clock\n'
                    'class Value:\n    def __repr__(self):\n'
                    '        clock.events.append(clock.limits.recording)\n'
                    '        clock.advance(20)\n        return "value"\n')
            if checker == 'gen_check.py':
                body += ('from gen_check import Generator\nfrom format_target import checked\n'
                         'TARGETS = ["format_target.py:checked"]\n'
                         'properties = {"broken": (Generator(lambda rng: Value(), lambda x: []), checked)}\n')
            else:
                body += 'def init(): return Value()\ndef actions(s): return []\ninvariants = {"broken": lambda s: False}\n'
            result = self.clock_check(checker, body, run=10, call=0, flags=('--json',))
            if result['code'] != 1 or result['events'] != [0]:
                failures.append((checker, 'formatting ran with deferred alarms', result))
        try:
            for checker in ('gen_check.py', 'bounded_check.py'):
                fixture = self.art / ('format_' + checker)
                body = ('class Value:\n    def __repr__(self):\n        while True: pass\n')
                if checker == 'gen_check.py':
                    body += ('from gen_check import Generator\nfrom format_target import checked\n'
                             'TARGETS = ["format_target.py:checked"]\n'
                             'properties = {"broken": (Generator(lambda rng: Value(), lambda x: []), checked)}\n')
                    flags = ('--repo', self.repo, '--examples', '1')
                else:
                    body += 'def init(): return Value()\ndef actions(s): return []\ninvariants = {"broken": lambda s: False}\n'
                    flags = ()
                fixture.write_text(body)
                try:
                    report = self.json(sys.executable, self.script('formal-verification', checker), fixture, *flags,
                                       '--timeout', '0.5', '--call-timeout', '0', '--json', expected=1, timeout=8)
                except subprocess.TimeoutExpired:
                    failures.append((checker, 'result formatting ignored the internal timeout'))
                    continue
                if checker == 'gen_check.py':
                    found = report['properties']['broken']
                    valid = (found['result'] == 'fail' and found['accepted'] == 1 and found.get('output_incomplete')
                             and report['called'] == ['format_target.py:checked'] and not report['missing_targets'])
                else:
                    found = report['violation']
                    valid = (report['result'] == 'violation' and found and found['name'] == 'broken'
                             and found.get('trace_incomplete'))
                if not valid or not report['incomplete']:
                    failures.append((checker, report))
            self.ensure(not failures, f'unbounded user repr during result formatting remains interruptible and retains confirmed failures: {failures}')
        finally:
            target.unlink()

    def review_bounded_limit_workload(self):
        from types import SimpleNamespace
        checker = load_module(self.script('formal-verification', 'bounded_check.py'), 'workload_bounded')
        failures, measurements = [], []
        for size in (100, 1000):
            for enabled in (False, True):
                now = 0.0
                handler = signal.SIG_DFL
                counters = dict(timers=0, wrappers=0, records=0, invariants=0, actions=0)
                def set_handler(sig, value):
                    nonlocal handler
                    old, handler = handler, value
                    return old
                def set_timer(*args):
                    counters['timers'] += 1
                    return (0.0, 0.0)
                alarm = SimpleNamespace(SIGALRM=signal.SIGALRM, ITIMER_REAL=signal.ITIMER_REAL, SIG_DFL=signal.SIG_DFL,
                                        signal=set_handler, getsignal=lambda sig: handler,
                                        getitimer=lambda which: (0.0, 0.0), setitimer=set_timer)
                class CountedLimits(checker.Limits):
                    def call(self, *args, **kwargs):
                        counters['wrappers'] += 1
                        return super().call(*args, **kwargs)
                    def record_result(self):
                        counters['records'] += 1
                        return super().record_result()
                def inv(state):
                    counters['invariants'] += 1
                    return True
                def actions(state):
                    counters['actions'] += 1
                    return [('next', state + 1)] if state + 1 < size else []
                model = SimpleNamespace(init=lambda: 0, actions=actions)
                with patch.object(checker, 'signal', alarm), patch.object(checker, 'time', SimpleNamespace(monotonic=lambda: now)):
                    with CountedLimits(90 if enabled else 0, 10 if enabled else 0) as limits:
                        report = checker.explore(model, actions, dict(a=inv, b=inv, c=inv), size + 1, size + 1, False, limits)
                measurements.append(dict(size=size, enabled=enabled, **counters))
                valid = (report['result'] == 'pass' and report['states'] == size
                         and counters['invariants'] == 3 * size and counters['actions'] == size)
                if enabled:
                    valid = valid and counters['timers'] <= 4 and counters['records'] == 0
                else:
                    valid = valid and counters['timers'] == counters['wrappers'] == counters['records'] == 0
                if not valid:
                    failures.append(measurements[-1])
        # Reusing an earlier alarm must neither expire a newer call early nor lose its deadline.
        for run, expected_scope, expected_due in ((40, 'call', 15), (12, 'run', 12)):
            now, due = 0.0, 0.0
            handler = signal.SIG_DFL
            def set_handler(sig, value):
                nonlocal handler
                old, handler = handler, value
                return old
            def set_timer(which, delay, repeat=0.0):
                nonlocal due
                due = now + delay if delay else 0.0
                if repeat:
                    failures.append(('periodic timer', repeat))
                return (0.0, 0.0)
            alarm = SimpleNamespace(SIGALRM=signal.SIGALRM, ITIMER_REAL=signal.ITIMER_REAL, SIG_DFL=signal.SIG_DFL,
                                    signal=set_handler, getsignal=lambda sig: handler,
                                    getitimer=lambda which: (0.0, 0.0), setitimer=set_timer)
            with patch.object(checker, 'signal', alarm), patch.object(checker, 'time', SimpleNamespace(monotonic=lambda: now)):
                with checker.Limits(run, 10) as limits:
                    kwargs = {'coalesce': True} if 'coalesce' in checker.Limits.call.__code__.co_varnames else {}
                    limits.call(lambda: True, **kwargs)
                    now = 5
                    def delayed():
                        nonlocal now
                        now = 10
                        limits.tick(None, None)  # An earlier call's alarm is now obsolete.
                        if due != expected_due:
                            failures.append(('early alarm was not rearmed', run, due, expected_due))
                        now = expected_due
                        limits.tick(None, None)
                        failures.append(('actual deadline was lost', run))
                    try:
                        limits.call(delayed, **kwargs)
                    except checker.Timeout as error:
                        if error.scope != expected_scope:
                            failures.append(('wrong deadline scope', run, error.scope))
                    else:
                        failures.append(('overdue call returned', run))
        self.log.write('bounded deterministic work: ' + json.dumps(measurements) + '\n')
        self.ensure(not failures, f'bounded search coalesces one-shot timers and bypasses all call/record wrappers with both limits disabled: {failures}')

    def review_acceptance_record_scope(self):
        history = (ROOT / 'logs/review-fixes/README.md').read_text().split(
            '## Authorized fixes for HEAD 40dae9c, 2026-11-15\n', 1)[1].split('\n## ', 1)[0]
        self.ensure('9/9 twice' in history and
                    'No out-of-sandbox or Linux acceptance was performed for this pass.' not in history,
                    'the historical Linux acceptance and Mac sandbox limitation do not contradict each other')

    def review_supplemental_acceptance_scope(self):
        history = (ROOT / 'logs/review-fixes/README.md').read_text().partition(
            '## Authorized supplemental fixes for HEAD 400b062, 2026-11-15\n')[2].split('\n## ', 1)[0]
        self.ensure('43/43' in history and '9/9 twice' in history and '7 PASS / 2 FAIL' in history
                    and 'head-400b062-linux/verify-box.log' in history
                    and 'head-400b062-verification/verify.log' in history
                    and 'No new Linux acceptance was performed for this supplemental pass.' not in history,
                    'supplemental acceptance retains Linux and Mac evidence without denying the recorded Linux runs')

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
        methods += [self.review_confirmed_signal_boundary, self.review_match_case_paths,
                    self.review_git_locale, self.review_swallowed_alarm,
                    self.review_route_workload, self.review_budget_namespace]
        methods += [self.review_expression_paths, self.review_query_path_aliases, self.review_go_receiver_workload]
        methods += [self.review_net_change_docs, self.review_timeout_format_handoff,
                    self.review_interruptible_result_format, self.review_bounded_limit_workload]
        methods += [self.review_exception_format_timeout, self.review_exception_path_provenance,
                    self.review_registered_boolean_actions, self.review_git_inherited_environment]
        methods += [self.review_infrastructure_exception_format, self.review_with_item_provenance,
                    self.review_parser_action_bindings, self.review_git_case_aliases,
                    self.review_acceptance_record_scope]
        methods += [self.review_parser_helper_defaults, self.review_parser_constructor_arguments,
                    self.review_supplemental_acceptance_scope]
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
