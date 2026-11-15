#!/usr/bin/env python3
"""Repeat the bounded-check workload; wall times are observations, not test gates."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--states', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.states < 1 or args.repeats < 1:
        parser.error('states and repeats must be positive')
    root = Path(__file__).resolve().parents[3]
    path = 'skills/formal-verification/scripts/bounded_check.py'
    refs = {'main': '98c3efb3ad5879ca38faa5879c5fcc523e8dccc5',
            'reviewed_HEAD': '31c8c5a20f3caf447da276571ea5d9aca4ddf8eb'}
    sources = {label: subprocess.check_output(['git', 'show', f'{ref}:{path}'], cwd=root)
               for label, ref in refs.items()}
    sources['working'] = (root / path).read_bytes()
    results = []
    for label, source in sources.items():
        module = ModuleType('benchmark_' + label)
        sys.modules[module.__name__] = module
        exec(compile(source, label + '/bounded_check.py', 'exec'), module.__dict__)
        def actions(state):
            return [('next', state + 1)] if state + 1 < args.states else []
        invariants = {name: lambda state: True for name in ('a', 'b', 'c')}
        model = SimpleNamespace(init=lambda: 0)
        for mode in (('no_limits_API',) if label == 'main' else ('default', 'disabled')):
            durations = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                params = (model, actions, invariants, args.states + 1, args.states + 1, False)
                if label == 'main':
                    report = module.explore(*params)
                else:
                    with module.Limits(90 if mode == 'default' else 0,
                                       10 if mode == 'default' else 0) as limits:
                        report = module.explore(*params, limits)
                durations.append(time.perf_counter() - started)
                assert report['states'] == args.states and report['result'] == 'pass'
            results.append({'version': label, 'mode': mode, 'seconds': durations,
                            'median': statistics.median(durations)})
    print(json.dumps({'refs': refs, 'states': args.states, 'invariants': 3,
                      'repeats': args.repeats, 'python': sys.version,
                      'sha256': {k: hashlib.sha256(v).hexdigest() for k, v in sources.items()},
                      'results': results}, indent=2))


if __name__ == '__main__':
    main()
