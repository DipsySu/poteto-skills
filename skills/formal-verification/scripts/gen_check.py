#!/usr/bin/env python3
"""Seeded Python property checks against declared repo targets (stdlib only).

PROPS.py imports combinators and reject from gen_check, defines
properties = {name: (generator, check)}, and TARGETS = ["path.py:Class.method"].
Optional examples = {name: [inputs]}, setup(), reset(). Hooks run before each
input and shrink attempt. Only check(input) is profiled; rejected inputs do not
count toward target coverage. A failure is reduced with at most 500 attempts,
not guaranteed minimal. Exit 0 pass, 1 property/filter/target failure, 2 usage
or infrastructure error. --expect-fail never accepts infrastructure failures.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import itertools
import json
import random
import string
import sys
from pathlib import Path


class PropsError(Exception):
    pass


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


def attempt(mod, check, value, repo):
    for name in ('setup', 'reset'):
        hook = getattr(mod, name, None)
        if hook is not None:
            hook()
    value = copy.deepcopy(value)
    called = set()
    profile_error = None

    def profile(frame, event, arg):
        nonlocal profile_error
        code = frame.f_code
        if event == 'call' and code.co_name != '<module>' and not code.co_filename.startswith('<'):
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
        except BaseException as error:
            return 'fail', f"{type(error).__name__}: {error}", called
        return ('fail', 'returned False', called) if result is False else ('pass', None, called)
    finally:
        sys.setprofile(previous)
        if profile_error is not None:
            raise PropsError(f"profiling failed: {profile_error}")


def reduce_failure(mod, gen, check, value, detail, repo):
    seen = {repr(value)}
    attempts = 0
    while attempts < 500:
        for candidate in itertools.islice(gen.shrink(value), 500 - attempts):
            attempts += 1
            key = repr(candidate)
            if key in seen:
                continue
            seen.add(key)
            status, why, _ = attempt(mod, check, candidate, repo)
            if status == 'fail':
                value, detail = candidate, why
                break
        else:
            break
    return value, detail, attempts


def run_property(mod, name, gen, check, rng, count, repo):
    result = {"result": "pass", "accepted": 0, "generated": 0, "rejected": 0,
              "counterexample": None, "detail": None, "shrink_attempts": 0}
    called = set()
    pinned = getattr(mod, 'examples', {}).get(name, [])
    inputs = itertools.chain(((False, value) for value in pinned),
                             ((True, gen.draw(rng)) for _ in range(count)))
    for generated, value in inputs:
        status, detail, ran = attempt(mod, check, value, repo)
        result['generated'] += int(generated)
        if status == 'reject':
            result['rejected'] += int(generated)
            continue
        result['accepted'] += 1
        called.update(ran)
        if status == 'fail':
            value, detail, attempts = reduce_failure(mod, gen, check, value, detail, repo)
            result.update(result='fail', counterexample=repr(value), detail=detail, shrink_attempts=attempts)
            break
    if result['rejected'] * 2 > result['generated']:
        result['result'] = 'filter_too_much'
    return result, called


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('props', type=Path, help='PROPS.py with properties and declared TARGETS')
    ap.add_argument('--repo', type=Path, required=True, help='repository whose functions must run')
    ap.add_argument('--seed', type=int, default=0, help='random seed (default: 0)')
    ap.add_argument('--examples', type=int, default=100, help='generated inputs per property after pinned examples (default: 100)')
    ap.add_argument('--expect-fail', metavar='NAME', help='exit 0 only for a counterexample to this property')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    args = ap.parse_args(argv)
    report = {"seed": args.seed, "ok": False, "properties": {}, "called": [], "missing_targets": []}
    try:
        repo = args.repo.resolve()
        if not repo.is_dir() or not args.props.is_file() or args.examples < 0:
            raise PropsError("repo must be a directory, PROPS.py a file, and examples nonnegative")
        mod, targets = load_props(args.props, repo)
        if args.expect_fail is not None and args.expect_fail not in mod.properties:
            raise PropsError(f"unknown property: {args.expect_fail}")
        rng = random.Random(args.seed)
        called = set()
        for name, (gen, check) in mod.properties.items():
            result, ran = run_property(mod, name, gen, check, rng, args.examples, repo)
            report['properties'][name] = result
            called.update(ran)
        report['called'] = sorted(called)
        report['missing_targets'] = sorted(targets - called)
        ok = not report['missing_targets']
        for name, result in report['properties'].items():
            expected = 'fail' if name == args.expect_fail else 'pass'
            ok = ok and result['result'] == expected
        report['ok'] = ok
        code = 0 if ok else 1
    except (Exception, SystemExit) as error:
        report.update(result='error', error=f"{type(error).__name__}: {error}")
        code = 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"gen_check: {args.props} (seed {args.seed})")
        for name, result in report['properties'].items():
            print(f"  {name}: {result['result']} ({result['accepted']} accepted, {result['rejected']}/{result['generated']} generated rejected)")
            if result['counterexample'] is not None:
                print(f"    reduced counterexample: {result['counterexample']}")
                print(f"    {result['detail']} ({result['shrink_attempts']} shrink attempts)")
        print("  repo functions run: " + (', '.join(report['called']) or '(none)'))
        if report['missing_targets']:
            print("  missing targets: " + ', '.join(report['missing_targets']))
        if 'error' in report:
            print(f"  error: {report['error']}")
        print('PASS' if report['ok'] else 'FAIL')
    return code


# PROPS.py can import this module even when the CLI is the __main__ module.
if __name__ == '__main__':
    sys.modules['gen_check'] = sys.modules[__name__]
    raise SystemExit(main())
