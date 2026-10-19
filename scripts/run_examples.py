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
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback

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

    def run(self, *argv, expected=0, cwd=None, env=None):
        args = [str(a) for a in argv]
        self.log.write("$ " + shlex.join(args) + "\n")
        self.log.flush()
        result = subprocess.run(args, cwd=cwd or self.repo, env=env or self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
        self.log.write(result.stdout + f"\n[exit {result.returncode}; expected {expected}]\n")
        self.log.flush()
        self.last_output = result.stdout
        self.last_returncode = result.returncode
        accepted = expected if isinstance(expected, tuple) else (expected,)
        if result.returncode not in accepted:
            raise AssertionError(f"{shlex.join(args)}: exit {result.returncode}, expected {expected}; see {self.name}.log")
        return result.stdout

    def json(self, *argv, **kwargs):
        return json.loads(self.run(*argv, **kwargs))

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
            self.live_pass('initial')

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
            drive('health', 'http', 'GET', '/health', '--expect', '200')
            drive('create-todo', 'http', 'POST', '/todos', '{"title":"verify HTTP"}', '--expect', '201')
            drive('create-todo', 'cli', '--', sys.executable, '-m', 'todo', 'add', 'verify CLI', '--data', '{DATA_DIR}')
            drive('list-todos', 'http', 'GET', '/todos', '--expect', '200')
            drive('list-todos', 'cli', '--', sys.executable, '-m', 'todo', 'list', '--data', '{DATA_DIR}')
            drive('delete-todo', 'http', 'DELETE', '/todos/1', '--expect', '409')
            drive('done-todo', 'http', 'POST', '/todos/1/done', '--expect', '200')
            drive('done-todo', 'http', 'POST', '/todos/1/done', '--expect', '409')
            drive('done-todo', 'cli', '--', sys.executable, '-m', 'todo', 'done', '2', '--data', '{DATA_DIR}')
            drive('delete-todo', 'http', 'DELETE', '/todos/1', '--expect', '200')
            drive('delete-todo', 'cli', '--', sys.executable, '-m', 'todo', 'rm', '2', '--data', '{DATA_DIR}')
            drive('list-todos', 'http', 'GET', '/todos', '--expect', '200')
            evidence = self.json(control, 'evidence', '--copy-data', env=env)
            evdir = Path(launch['evidence_dir'])
            records = [json.loads(p.read_text()) for p in sorted(evdir.glob('[0-9]*.json'))]
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
            self.run(*command)
            self.live_pass('maintain')

    def lint(self, rules, path, *, root=None, expected=0, extra=()):
        return self.json(self.script('correct', 'patlint.py'), '--rules', rules, '--root', root or self.repo,
                         '--json', *extra, path, expected=expected)

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
            rules = self.save('rules.json', {'rules':[{'id':'no-direct-json-write','regex':r'\b\w+\.path\.write_text\s*\(',
                'include':['todo/*.py'], 'exclude':['todo/store.py'],
                'message':'Use Store.add or Store.transition in todo/store.py for atomic persistence.'}]})
            old = self.scratch / 'past-direct-write'
            self.run('git', 'worktree', 'add', '--detach', old, self.built['direct_write_commits'][1])
            try:
                result = self.lint(rules, old / 'todo', root=old, expected=1)
                self.ensure(any(v['path']=='todo/api.py' for v in result['violations']), 'patlint fails on a real past mistake checked out in a worktree')
                self.save('past-violation.json', result)
            finally:
                self.run('git', 'worktree', 'remove', '--force', old)
            self.ensure(self.lint(rules, self.repo / 'todo')['ok'], 'same persistence lint passes on HEAD')
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

    def curator(self):
        with self.case('curator'):
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
            # Redirect curator outputs to a disposable collection, including its log.
            collection = self.scratch/'collection'
            shutil.copytree(ROOT/'research',collection/'research')
            shutil.copytree(ROOT/'skills',collection/'skills')
            self.run(self.script(self.name,'curate.sh'),'--propose','--offline','--repo',collection,'--date','2026-10-19',env=env)
            proposal = collection/'proposals/2026-10-19.md'
            log = collection/'logs/curator/2026-10-19.log'
            self.ensure(proposal.is_file() and log.is_file(), 'offline curate writes a proposal and execution log')
            self.ensure('correct/SKILL.md' in proposal.read_text() and 'local-new' in proposal.read_text(), 'proposal includes changed and new upstream evidence with source URLs')
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


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--keep-scratch',action='store_true',help='keep the isolated scratch directory for diagnosis')
    args = ap.parse_args()
    return Examples(args.keep_scratch).execute()


if __name__=='__main__':
    raise SystemExit(main())
