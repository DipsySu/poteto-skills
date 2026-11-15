#!/usr/bin/env python3
"""Seeded Python property checks against declared repo targets (stdlib only).

PROPS.py imports combinators and reject from gen_check, defines
properties = {name: (generator, check)}, and TARGETS = ["path.py:Class.method"].
Optional examples = {name: [inputs]}, setup(), reset(), CHECK_TIMEOUT (seconds for the
run). Hooks run before each input and shrink attempt. Only check(input) is
profiled; rejected and timed-out inputs do not count toward target coverage. A
failure is reduced with at most 500 attempts, not guaranteed minimal. An input
whose hooks and check run past --call-timeout ends its property as timeout; the
other properties still run. During shrinking, a timeout instead keeps the
confirmed failure and marks the reduction incomplete; --expect-fail cannot pass.
Exit 0 pass, 1 property/filter/target failure, 2
usage or infrastructure error, 3 inconclusive: a call/run limit or Ctrl-C stopped a
check and nothing failed outright. --expect-fail never accepts infrastructure
failures or timeouts. Zero disables the corresponding time limit. While enabled,
the checker reserves SIGALRM; checked code must not use alarm/setitimer.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import itertools
import json
import random
import signal
import string
import sys
import threading
import time
from pathlib import Path
from contextlib import contextmanager

DEFAULT_TIMEOUT = 90.0
DEFAULT_CALL_TIMEOUT = 10.0


class PropsError(Exception):
    pass


class Timeout(BaseException):
    """A time limit stopped a call or the run: not a failure and not a proof.

    A BaseException, like KeyboardInterrupt, so a check's `except Exception` cannot swallow it.
    """

    def __init__(self, scope, seconds):
        super().__init__(f"no return within {seconds:g} s" if scope == 'call'
                         else f"stopped at the {seconds:g} s limit")
        self.scope = scope


class Limits:
    """Run and per-call limits, checked synchronously and by a one-shot SIGALRM.

    Where setitimer is missing (Windows) or off the main thread, warn that the limits are not enforced.
    """
    def __init__(self, run_seconds, call_seconds):
        self.call_seconds = call_seconds
        self.begun = time.monotonic()
        self.installed = False
        self.limit_run(run_seconds)
        self.started = None
        self.expired = False
        self.previous = None
        self.recording = 0
        self.pending = None
        self.active = (callable(getattr(signal, 'setitimer', None)) and hasattr(signal, 'SIGALRM')
                       and hasattr(signal, 'ITIMER_REAL')
                       and threading.current_thread() is threading.main_thread())

    def limit_run(self, seconds):
        self.run_seconds = seconds
        self.deadline = self.begun + seconds if seconds else float('inf')
        self.arm()

    def arm(self):
        """Reserve SIGALRM for the nearest enabled deadline, never for periodic polling."""
        if not self.installed or signal.getsignal(signal.SIGALRM) != self.tick:
            return
        call_deadline = (self.started + self.call_seconds
                         if self.started is not None and self.call_seconds else float('inf'))
        deadline = min(self.deadline, call_deadline)
        delay = max(0.0, deadline - time.monotonic()) if deadline < float('inf') and not self.expired else 0
        signal.setitimer(signal.ITIMER_REAL, delay)

    def __enter__(self):
        if self.active and (self.run_seconds or self.call_seconds):
            self.entered_at = time.monotonic()
            self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
            self.previous = signal.signal(signal.SIGALRM, self.tick)
            self.installed = True
            try:
                self.arm()
                self.check_run()  # An already elapsed budget must not disarm the timer and start importing.
            except BaseException:
                self.__exit__()
                raise
        elif not self.active and (self.run_seconds or self.call_seconds):
            print("gen_check: WARNING: --timeout and --call-timeout are not enforced; "
                  "SIGALRM/setitimer in the main thread is required. Use an external process deadline.",
                  file=sys.stderr, flush=True)
        return self

    def __exit__(self, *exc):
        self.started, self.expired = None, True
        if self.installed and signal.getsignal(signal.SIGALRM) == self.tick:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous if self.previous is not None else signal.SIG_DFL)
            delay, interval = self.previous_timer
            if delay:
                remaining = max(0.000001, delay - (time.monotonic() - self.entered_at))
                signal.setitimer(signal.ITIMER_REAL, remaining, interval)
        self.installed = False
        return False

    @contextmanager
    def record_result(self):
        """Defer alarms only between a validated call's return and recording its result."""
        self.recording += 1
        try:
            yield
        finally:
            self.recording -= 1
            if not self.recording and self.pending is not None:
                error, self.pending = self.pending, None
                raise error

    def tick(self, signum, frame):
        try:
            self.check()
        except Timeout as error:
            # A bare except may swallow this alarm. Retry only after expiry, never before.
            if self.installed and signal.getsignal(signal.SIGALRM) == self.tick:
                signal.setitimer(signal.ITIMER_REAL, 0.1)
            if self.recording and self.started is None:
                self.pending = error
            else:
                raise

    def check(self):
        """Check both absolute deadlines before clearing the current call's start time."""
        if not self.active:
            return
        now = time.monotonic()
        call_deadline = (self.started + self.call_seconds
                         if self.started is not None and self.call_seconds else float('inf'))
        if now >= self.deadline:
            if call_deadline < self.deadline:
                raise Timeout('call', self.call_seconds)
            self.expired = True
            raise Timeout('run', self.run_seconds)
        if now >= call_deadline:
            raise Timeout('call', self.call_seconds)

    def check_run(self):
        """Check the run deadline synchronously, including a budget learned after import."""
        if self.active and time.monotonic() >= self.deadline:
            self.expired = True
            raise Timeout('run', self.run_seconds)

    def iterate(self, values):
        """Check before and after generation, including an iterator's final StopIteration."""
        iterator = iter(values)
        while True:
            self.check_run()
            try:
                value = next(iterator)
            except StopIteration:
                self.check_run()
                return
            self.check_run()
            yield value


LIMIT_CODES = {Limits.tick.__code__, Limits.check.__code__, Limits.check_run.__code__, Timeout.__init__.__code__}


def seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float('inf'):
        raise PropsError(f"{name} must be a nonnegative finite number of seconds (0 disables the limit)")
    return float(value)


class Rejected(Exception):
    pass


def reject():
    """Discard an input whose precondition does not hold."""
    raise Rejected()


class Generator:
    def __init__(self, draw, shrink):
        self.draw = draw
        self.shrink = shrink


def bounds(low, high):
    if type(low) is not int or type(high) is not int or low > high:
        raise PropsError("bounds must be integers with min <= max")


def ints(min_value=-100, max_value=100):
    bounds(min_value, max_value)
    target = max(min_value, min(0, max_value))

    def shrink(value):
        if type(value) is not int or not min_value <= value <= max_value or value == target:
            return
        yield target
        distance = value - target
        while abs(distance) > 1:
            distance = (abs(distance) // 2) * (1 if distance > 0 else -1)
            yield target + distance

    return Generator(lambda rng: rng.randint(min_value, max_value), shrink)


def sizes(min_size, max_size):
    bounds(min_size, max_size)
    if min_size < 0:
        raise PropsError("min_size must be nonnegative")


def deletions(value, min_size):
    """Delete chunks, largest first, preserving the lower size bound."""
    width = len(value) - min_size
    while width:
        for start in range(len(value) - width + 1):
            yield value[:start] + value[start + width:]
        width //= 2


def text(alphabet=string.ascii_letters + string.digits + " \t\n", min_size=0, max_size=30):
    sizes(min_size, max_size)
    if not isinstance(alphabet, str) or not alphabet:
        raise PropsError("text alphabet must be a nonempty string")

    def shrink(value):
        if not isinstance(value, str) or not min_size <= len(value) <= max_size:
            return
        yield from deletions(value, min_size)
        for i, char in enumerate(value):
            if char in alphabet:
                for simpler in alphabet[:alphabet.index(char)]:
                    yield value[:i] + simpler + value[i + 1:]

    def draw(rng):
        return ''.join(rng.choice(alphabet) for _ in range(rng.randint(min_size, max_size)))

    return Generator(draw, shrink)


def require_generators(*generators):
    if not all(isinstance(g, Generator) for g in generators):
        raise PropsError("combinators require generators from gen_check")


def lists(elements, min_size=0, max_size=10):
    require_generators(elements)
    sizes(min_size, max_size)

    def shrink(value):
        if not isinstance(value, list) or not min_size <= len(value) <= max_size:
            return
        yield from deletions(value, min_size)
        for i, item in enumerate(value):
            for smaller in elements.shrink(item):
                yield value[:i] + [smaller] + value[i + 1:]

    return Generator(lambda rng: [elements.draw(rng) for _ in range(rng.randint(min_size, max_size))], shrink)


def tuples(*elements):
    require_generators(*elements)

    def shrink(value):
        if not isinstance(value, tuple) or len(value) != len(elements):
            return
        for i, gen in enumerate(elements):
            for smaller in gen.shrink(value[i]):
                yield value[:i] + (smaller,) + value[i + 1:]

    return Generator(lambda rng: tuple(gen.draw(rng) for gen in elements), shrink)


def dicts(keys, values, min_size=0, max_size=10):
    require_generators(keys, values)
    sizes(min_size, max_size)

    def draw(rng):
        size = rng.randint(min_size, max_size)
        result = {}
        for _ in range(max(100, size * 20)):
            if len(result) == size:
                return result
            result[keys.draw(rng)] = values.draw(rng)
        raise PropsError("dicts could not generate enough distinct keys; reduce size or expand keys")

    def shrink(value):
        if not isinstance(value, dict) or not min_size <= len(value) <= max_size:
            return
        for items in deletions(list(value.items()), min_size):
            yield dict(items)
        for key, val in value.items():
            for smaller in values.shrink(val):
                yield {**value, key: smaller}
            for smaller in keys.shrink(key):
                if smaller != key and smaller not in value:
                    yield {smaller if k == key else k: v for k, v in value.items()}

    return Generator(draw, shrink)


def one_of(*generators):
    require_generators(*generators)
    if not generators:
        raise PropsError("one_of needs at least one generator")

    def shrink(value):
        for gen in generators:
            yield from gen.shrink(value)

    return Generator(lambda rng: rng.choice(generators).draw(rng), shrink)


def sampled_from(values):
    values = tuple(values)
    if not values:
        raise PropsError("sampled_from needs at least one value")

    def shrink(value):
        for item in values:
            if item == value:
                break
            yield copy.deepcopy(item)

    return Generator(lambda rng: copy.deepcopy(rng.choice(values)), shrink)


def load_props(path, repo):
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(repo), str(path.resolve().parent)]
    spec = importlib.util.spec_from_file_location(f"gen_props_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    props = getattr(mod, "properties", None)
    if not isinstance(props, dict) or not props:
        raise PropsError("PROPS.py must define nonempty properties = {name: (gen, check)}")
    for name, pair in props.items():
        if (not isinstance(name, str) or not name or not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(pair[0], Generator) or not callable(pair[1])):
            raise PropsError("each property must be name: (generator, callable check)")
    targets = getattr(mod, "TARGETS", None)
    if not isinstance(targets, list) or not targets:
        raise PropsError('PROPS.py must declare TARGETS = ["path.py:Class.method", ...]')
    normalized = set()
    for target in targets:
        if not isinstance(target, str) or target.count(':') != 1:
            raise PropsError(f"invalid target: {target!r}")
        file, qualname = target.split(':')
        path_in_repo = Path(file)
        if (path_in_repo.is_absolute() or not file or not qualname
                or not all(part.isidentifier() for part in qualname.split('.'))
                or not (repo / file).resolve().is_relative_to(repo)):
            raise PropsError(f"invalid repo-relative target: {target!r}")
        normalized.add(f"{(repo / file).resolve().relative_to(repo).as_posix()}:{qualname}")
    pinned = getattr(mod, "examples", {})
    if (not isinstance(pinned, dict) or any(n not in props or not isinstance(v, list) for n, v in pinned.items())):
        raise PropsError("examples must map property names to lists of inputs")
    for name in ('setup', 'reset'):
        if hasattr(mod, name) and not callable(getattr(mod, name)):
            raise PropsError(f"{name} must be callable")
    return mod, normalized


def attempt(mod, check, value, repo, limits):
    """Run one input's hooks and check under the per-call limit; a Timeout propagates."""
    limits.check_run()
    limits.started = time.monotonic()
    try:
        limits.arm()
        limits.check()
        for name in ('setup', 'reset'):
            hook = getattr(mod, name, None)
            if hook is not None:
                hook()
                limits.check()
        value = copy.deepcopy(value)
        limits.check()
        called = set()
        profile_error = None

        def profile(frame, event, arg):
            nonlocal profile_error
            code = frame.f_code
            if (event == 'call' and code.co_name != '<module>' and not code.co_filename.startswith('<')
                    and code not in LIMIT_CODES):
                try:
                    path = Path(code.co_filename).resolve()
                    if path.is_relative_to(repo):
                        called.add(f"{path.relative_to(repo).as_posix()}:{code.co_qualname}")
                except Exception as error:
                    profile_error = error
                    raise

        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            try:
                result = check(value)
            except Rejected:
                return 'reject', None, set()
            except (Timeout, KeyboardInterrupt):
                raise  # a time limit or Ctrl-C is not a counterexample
            except BaseException as error:
                return 'fail', f"{type(error).__name__}: {error}", called
            return ('fail', 'returned False', called) if result is False else ('pass', None, called)
        finally:
            sys.setprofile(previous)
            if profile_error is not None:
                raise PropsError(f"profiling failed: {profile_error}")
    finally:
        try:
            limits.check()
        finally:
            limits.started = None
            limits.arm()


def reduce_failure(mod, gen, check, value, detail, repo, limits, result):
    """On any timeout or Ctrl-C, retain the best reduction, stop reason and interrupted input."""
    seen = {repr(value)}
    attempts = 0
    checking = None
    try:
        while attempts < 500:
            limits.check_run()
            for candidate in limits.iterate(itertools.islice(gen.shrink(value), 500 - attempts)):
                attempts += 1
                key = repr(candidate)
                if key in seen:
                    continue
                seen.add(key)
                checking = (candidate,)
                with limits.record_result():
                    status, why, _ = attempt(mod, check, candidate, repo, limits)
                    checking = None
                    if status == 'fail':
                        value, detail = candidate, why
                        result.update(counterexample=key, detail=detail, shrink_attempts=attempts)
                if status == 'fail':
                    break
            else:
                break
        limits.check_run()
    except (Timeout, KeyboardInterrupt) as error:
        return value, detail, attempts, error, checking
    return value, detail, attempts, None, None


def property_result():
    return {"result": "pending", "accepted": 0, "generated": 0, "rejected": 0,
            "counterexample": None, "detail": None, "shrink_attempts": 0}


def run_property(mod, name, gen, check, rng, count, repo, limits, result, called):
    """Update registered result/coverage in place so a signal at return cannot discard findings."""
    stopped = checking = None
    pinned = getattr(mod, 'examples', {}).get(name, [])
    inputs = itertools.chain(((False, value) for value in pinned),
                             ((True, gen.draw(rng)) for _ in range(count)))
    try:
        for generated, value in limits.iterate(inputs):
            result['generated'] += int(generated)
            checking = (value,)
            # A bounded fallback is ready before entering the return-to-record region.
            fallback = repr(value) if type(value) in (type(None), bool, int, float, str) else '<unformatted input>'
            with limits.record_result():
                status, detail, ran = attempt(mod, check, value, repo, limits)
                checking = None
                if status == 'reject':
                    result['rejected'] += int(generated)
                    continue
                result['accepted'] += 1
                called.update(ran)
                if status == 'fail':
                    result.update(result='fail', counterexample=fallback, detail=detail,
                                  output_incomplete='counterexample formatting not completed')
            if status == 'fail':
                result['counterexample'] = repr(value)
                result.pop('output_incomplete', None)
                value, detail, attempts, stopped, checking = reduce_failure(mod, gen, check, value, detail, repo, limits, result)
                result.update(result='fail', detail=detail, shrink_attempts=attempts)
                if stopped is not None:
                    scope = 'interrupted' if isinstance(stopped, KeyboardInterrupt) else stopped.scope
                    result.update(timeout_scope=scope, timeout_phase='shrink',
                                  input=repr(checking[0]) if checking else None,
                                  incomplete=('interrupted during shrink' if scope == 'interrupted'
                                              else f'{scope} timeout during shrink: {stopped}'))
                    if scope == 'call':
                        stopped = None  # End this property's shrinking; other properties still run.
                break
        limits.check_run()
    except (Timeout, KeyboardInterrupt) as error:
        stopped = error if isinstance(error, KeyboardInterrupt) or error.scope == 'run' else None
        # An interrupted check is inconclusive; interruption after a confirmed failure cannot erase it.
        if result['result'] != 'fail':
            result.update(result='timeout', input=None,
                          timeout_scope='interrupted' if isinstance(error, KeyboardInterrupt) else error.scope,
                          detail='interrupted' if isinstance(error, KeyboardInterrupt) else str(error))
            result['input'] = repr(checking[0]) if checking else None
    if result['result'] == 'pending':
        result['result'] = 'pass'
    # Too many rejections is a warning about a property that otherwise passed; it never hides a result.
    if result['result'] == 'pass' and result['rejected'] * 2 > result['generated']:
        result['result'] = 'filter_too_much'
    return result, called, stopped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('props', type=Path, help='PROPS.py with properties and declared TARGETS')
    ap.add_argument('--repo', type=Path, required=True, help='repository whose functions must run')
    ap.add_argument('--seed', type=int, default=0, help='random seed (default: 0)')
    ap.add_argument('--examples', type=int, default=100, help='generated inputs per property after pinned examples (default: 100)')
    ap.add_argument('--expect-fail', metavar='NAME', help='exit 0 only for a counterexample to this property')
    ap.add_argument('--timeout', type=float, metavar='S',
                    help='seconds for the whole run, then exit 3 (overrides PROPS.CHECK_TIMEOUT; default: 90; '
                         'imports use CLI or 90; 0 disables)')
    ap.add_argument('--call-timeout', type=float, default=DEFAULT_CALL_TIMEOUT, metavar='S',
                    help="seconds for one input's hooks and check, then exit 3 (default: 10; 0 disables)")
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    args = ap.parse_args(argv)
    report = {"seed": args.seed, "ok": False, "properties": {}, "called": [], "missing_targets": [],
              "timeout": args.timeout, "call_timeout": args.call_timeout, "incomplete": None}
    try:
        repo = args.repo.resolve()
        if not repo.is_dir() or not args.props.is_file() or args.examples < 0:
            raise PropsError("repo must be a directory, PROPS.py a file, and examples nonnegative")
        if args.timeout is not None:
            seconds(args.timeout, '--timeout')
        limits = Limits(DEFAULT_TIMEOUT if args.timeout is None else args.timeout,
                        seconds(args.call_timeout, '--call-timeout'))
        mod, called = None, set()
        try:
            with limits:
                mod, targets = load_props(args.props, repo)
                if args.expect_fail is not None and args.expect_fail not in mod.properties:
                    raise PropsError(f"unknown property: {args.expect_fail}")
                if args.timeout is None:
                    limits.limit_run(seconds(getattr(mod, 'CHECK_TIMEOUT', DEFAULT_TIMEOUT), 'PROPS.CHECK_TIMEOUT'))
                report['timeout'] = limits.run_seconds
                limits.check_run()
                if not args.json:  # --json output carries the seed; keep it parseable
                    print(f"gen_check: seed {args.seed}; limits {limits.run_seconds:g} s per run, "
                          f"{limits.call_seconds:g} s per call", file=sys.stderr, flush=True)
                for name, (gen, check) in mod.properties.items():
                    limits.check_run()
                    rng = random.Random(f"{args.seed}:{name}")
                    result = report['properties'][name] = property_result()
                    _, _, stopped = run_property(mod, name, gen, check, rng, args.examples, repo, limits, result, called)
                    if result.get('incomplete'):
                        detail = f"{name}: {result['incomplete']}"
                        report['incomplete'] = '; '.join(filter(None, (report['incomplete'], detail)))
                    if stopped is not None:
                        raise stopped
                limits.check_run()
        except Timeout as error:
            where = f"in {list(report['properties'])[-1]}" if report['properties'] else "while importing PROPS.py"
            report['incomplete'] = f"{error} {where}"
        except KeyboardInterrupt:
            report['incomplete'] = "interrupted"
        code = 3
        if mod is not None:
            report['called'] = sorted(called)
            report['missing_targets'] = sorted(targets - called)
            results = report['properties']

            def expected(name):
                return 'fail' if name == args.expect_fail else 'pass'
            ok = (report['incomplete'] is None and len(results) == len(mod.properties)
                  and not report['missing_targets'] and all(r['result'] == expected(n) for n, r in results.items()))
            inconclusive = report['incomplete'] is not None or any(r['result'] == 'timeout' for r in results.values())
            definitive = any(r['result'] not in (expected(n), 'timeout', 'pending') for n, r in results.items())
            report['ok'] = ok
            # Unexpected confirmed failures retain exit 1; otherwise call/run timeouts exit 3.
            code = 0 if ok else 3 if inconclusive and not definitive else 1
    except (Exception, SystemExit) as error:
        report.update(result='error', error=f"{type(error).__name__}: {error}")
        code = 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"gen_check: {args.props} (seed {args.seed})")
        for name, result in report['properties'].items():
            if result['result'] == 'fail':
                print(f"  {name}: fail ({result['rejected']} of {result['generated']} generated inputs rejected before the counterexample)")
            else:
                print(f"  {name}: {result['result']} ({result['accepted']} accepted, {result['rejected']}/{result['generated']} generated rejected)")
            if result['counterexample'] is not None:
                print(f"    reduced counterexample: {result['counterexample']}")
                print(f"    {result['detail']} ({result['shrink_attempts']} shrink attempts)")
            if result['result'] == 'timeout':
                print(f"    input: {result['input'] if result['input'] is not None else '(none; stopped between inputs)'}")
                print(f"    {result['detail']}; replay with --seed {args.seed}")
        print("  repo functions run: " + (', '.join(report['called']) or '(none)'))
        if report['missing_targets']:
            print("  missing targets: " + ', '.join(report['missing_targets']))
        if report['incomplete']:
            print(f"  incomplete: {report['incomplete']}")
        if 'error' in report:
            print(f"  error: {report['error']}")
        print('PASS' if report['ok'] else 'INCONCLUSIVE: INCOMPLETE check; a time limit is not a failure and not a proof'
              if code == 3 else 'FAIL')
    return code


# PROPS.py can import this module even when the CLI is the __main__ module.
if __name__ == '__main__':
    sys.modules['gen_check'] = sys.modules[__name__]
    raise SystemExit(main())
