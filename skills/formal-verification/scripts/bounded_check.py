#!/usr/bin/env python3
"""Explicit-state bounded model checker for small Python models (stdlib only).

MODEL.py defines:
  init()                     -> the initial state (or inits() -> iterable of initial states)
  actions(state)             -> [(action_name, next_state), ...]  every enabled step
  invariants = {name: fn}    fn(state) -> bool, must hold in every reachable state
optional:
  terminal(state) -> bool    states allowed to have no next step (for --deadlock)
  describe(state) -> str     how a state prints in a trace
  mutants = {name: fn}       broken variants of actions(); --mutants requires each to break an invariant
  DEPTH, MAX_STATES          defaults for --depth / --max-states

States must be hashable or built from dicts, lists, sets, tuples, namedtuples, or dataclasses.
The checker explores breadth-first up to --depth steps, so the counterexample it prints is a
shortest one. Exit 0 = every invariant holds within the bound (and every mutant was killed),
1 = violation, deadlock, or surviving mutant, 2 = model or usage error.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import sys
import time
from collections import deque
from pathlib import Path


class ModelError(Exception):
    pass


def freeze(x):
    """A hashable key for a state."""
    try:
        hash(x)
        return x
    except TypeError:
        pass
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return (type(x).__name__, freeze(dataclasses.asdict(x)))
    if isinstance(x, dict):
        return ("dict", tuple(sorted(((freeze(k), freeze(v)) for k, v in x.items()), key=repr)))
    if isinstance(x, (set, frozenset)):
        return ("set", tuple(sorted((freeze(v) for v in x), key=repr)))
    if isinstance(x, (list, tuple)):
        return (type(x).__name__, tuple(freeze(v) for v in x))
    return repr(x)


def jsonable(x):
    if hasattr(x, "_asdict"):
        return {k: jsonable(v) for k, v in x._asdict().items()}
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {k: jsonable(v) for k, v in dataclasses.asdict(x).items()}
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return sorted((jsonable(v) for v in x), key=repr)
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    return repr(x)


def load_model(path: Path):
    if not path.is_file():
        raise ModelError(f"model {path} not found")
    sys.dont_write_bytecode = True  # don't leave __pycache__ next to the user's model
    sys.path.insert(0, str(path.resolve().parent))
    spec = importlib.util.spec_from_file_location(f"bounded_model_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 - report any import-time failure of the model
        raise ModelError(f"importing {path} failed: {type(e).__name__}: {e}")
    if not callable(getattr(mod, "inits", None)) and not callable(getattr(mod, "init", None)):
        raise ModelError("MODEL.py must define init() (or inits())")
    if not callable(getattr(mod, "actions", None)):
        raise ModelError("MODEL.py must define actions(state) -> [(name, next_state), ...]")
    inv = getattr(mod, "invariants", None)
    if not isinstance(inv, dict) or not inv or not all(callable(f) for f in inv.values()):
        raise ModelError("MODEL.py must define invariants = {name: fn(state) -> bool} with at least one entry")
    return mod


def initial_states(mod) -> list:
    if callable(getattr(mod, "inits", None)):
        return list(mod.inits())
    return [mod.init()]


def explore(mod, actions_fn, invariants: dict, depth: int, max_states: int, deadlock: bool) -> dict:
    describe = getattr(mod, "describe", None)
    terminal = getattr(mod, "terminal", None)
    states: dict = {}
    parent: dict = {}  # key -> (parent_key, action, depth)
    queue: deque = deque()
    out = {"result": "pass", "states": 0, "transitions": 0, "max_depth": 0, "hit_depth_bound": False,
           "hit_state_bound": False, "violation": None}

    def trace(k) -> list[dict]:
        steps = []
        while k is not None:
            pk, act, d = parent[k]
            s = states[k]
            steps.append({"step": d, "action": act, "state": jsonable(s),
                          "describe": describe(s) if callable(describe) else None})
            k = pk
        return list(reversed(steps))

    def bad(k, kind: str, name: str, detail: str | None = None) -> dict:
        out["result"] = "error" if kind == "error" else "violation"
        out["violation"] = {"kind": kind, "name": name, "detail": detail, "trace": trace(k)}
        out["states"] = len(states)
        return out

    def check(k, s):
        for name, fn in invariants.items():
            try:
                ok = fn(s)
            except Exception as e:  # noqa: BLE001 - an invariant that crashes is a model bug; show where
                return bad(k, "error", name, f"invariant raised {type(e).__name__}: {e}")
            if not ok:
                return bad(k, "invariant", name)
        return None

    for s in initial_states(mod):
        k = freeze(s)
        if k in states:
            continue
        states[k] = s
        parent[k] = (None, "init", 0)
        if (r := check(k, s)) is not None:
            return r
        queue.append(k)
    while queue:
        k = queue.popleft()
        s = states[k]
        d = parent[k][2]
        if d >= depth:
            out["hit_depth_bound"] = True
            continue
        try:
            succ = list(actions_fn(s))
        except Exception as e:  # noqa: BLE001
            return bad(k, "error", "actions", f"actions() raised {type(e).__name__}: {e}")
        if not succ and deadlock and not (callable(terminal) and terminal(s)):
            return bad(k, "deadlock", "deadlock", "no action is enabled and terminal(state) is not true")
        for item in succ:
            if not (isinstance(item, tuple) and len(item) == 2):
                return bad(k, "error", "actions", f"actions() must yield (name, next_state) pairs, got {item!r:.80}")
            name, ns = item
            out["transitions"] += 1
            nk = freeze(ns)
            if nk in states:
                continue
            states[nk] = ns
            parent[nk] = (k, str(name), d + 1)
            out["max_depth"] = max(out["max_depth"], d + 1)
            if (r := check(nk, ns)) is not None:
                return r
            if len(states) >= max_states:
                out["hit_state_bound"] = True
                queue.clear()
                break
            queue.append(nk)
    out["states"] = len(states)
    out["exhaustive"] = not out["hit_depth_bound"] and not out["hit_state_bound"]
    return out


def print_trace(v: dict) -> None:
    for st in v["trace"]:
        shown = st["describe"] if st["describe"] is not None else json.dumps(st["state"])
        print(f"  {st['step']:>3}  {st['action']:<36} {shown}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bounded_check.py",
        description="Breadth-first explicit-state model checker for a Python model (init, actions, invariants). "
                    "Prints a shortest counterexample trace on violation.",
        epilog="examples:\n"
               "  bounded_check.py references/example_model.py\n"
               "  bounded_check.py model.py --depth 30 --deadlock\n"
               "  bounded_check.py model.py --mutants                 # every broken variant must be caught\n"
               "  bounded_check.py model.py --expect-violation no_lost_todo   # exit 0 only if it fails\n\n"
               "MODEL.py: init() or inits(); actions(state) -> [(name, next_state)]; invariants = {name: fn};\n"
               "optional terminal(state), describe(state), mutants = {name: actions_fn}, DEPTH, MAX_STATES.\n"
               "exit: 0 holds within the bound, 1 violation/deadlock/surviving mutant, 2 model or usage error",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("model", type=Path, help="the MODEL.py file")
    ap.add_argument("--depth", type=int, help="max steps from an initial state (default: MODEL.DEPTH or 25)")
    ap.add_argument("--max-states", type=int, help="stop after this many distinct states (default: MODEL.MAX_STATES or 200000)")
    ap.add_argument("--invariant", action="append", default=[], metavar="NAME", help="check only these invariants (repeatable)")
    ap.add_argument("--deadlock", action="store_true", help="also fail on states with no enabled action (unless terminal)")
    ap.add_argument("--mutants", action="store_true", help="also run every MODEL.mutants entry; each must violate an invariant")
    ap.add_argument("--expect-violation", metavar="NAME", help="succeed only if invariant NAME is violated (able-to-fail proof)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        mod = load_model(args.model)
        invariants = dict(mod.invariants)
        if args.invariant:
            unknown = [n for n in args.invariant if n not in invariants]
            if unknown:
                raise ModelError(f"unknown invariant(s): {', '.join(unknown)}; known: {', '.join(invariants)}")
            invariants = {n: invariants[n] for n in args.invariant}
        if args.expect_violation and args.expect_violation not in invariants:
            raise ModelError(f"--expect-violation {args.expect_violation}: no such invariant; known: {', '.join(invariants)}")
        depth = args.depth or getattr(mod, "DEPTH", 25)
        max_states = args.max_states or getattr(mod, "MAX_STATES", 200_000)
        t0 = time.time()
        main_res = explore(mod, mod.actions, invariants, depth, max_states, args.deadlock)
        main_res["seconds"] = round(time.time() - t0, 3)
        mutant_res = {}
        if args.mutants:
            muts = getattr(mod, "mutants", None)
            if not isinstance(muts, dict) or not muts:
                raise ModelError("--mutants needs MODEL.mutants = {name: broken_actions_fn}")
            for name, fn in muts.items():
                r = explore(mod, fn, invariants, depth, max_states, args.deadlock)
                mutant_res[name] = {"killed": r["result"] == "violation", "by": (r["violation"] or {}).get("name"),
                                    "steps": len((r["violation"] or {}).get("trace", [])) - 1 if r["violation"] else None,
                                    "result": r}
    except ModelError as e:
        print(f"bounded_check: {e}", file=sys.stderr)
        return 2

    if args.expect_violation:
        v = main_res["violation"]
        ok = bool(v) and v["name"] == args.expect_violation and v["kind"] == "invariant"
    else:
        ok = main_res["result"] == "pass" and all(m["killed"] for m in mutant_res.values())
    if main_res["result"] == "error":
        code = 2
    else:
        code = 0 if ok else 1

    if args.json:
        print(json.dumps({"model": str(args.model), "depth": depth, "max_states": max_states,
                          "invariants": list(invariants), "ok": ok, **main_res,
                          "mutants": {k: {kk: vv for kk, vv in m.items() if kk != "result"} | {
                              "trace": (m["result"]["violation"] or {}).get("trace")} for k, m in mutant_res.items()},
                          "expect_violation": args.expect_violation}, indent=2))
        return code

    print(f"bounded_check: {args.model} (depth <= {depth}, {len(invariants)} invariant(s): {', '.join(invariants)})")
    if main_res["result"] == "pass":
        if main_res.get("exhaustive"):
            scope = "state space exhausted: complete for this model"
        elif main_res["hit_state_bound"]:
            scope = f"PARTIAL: state cap {max_states} hit at depth {main_res['max_depth']}; raise --max-states or shrink the model"
        else:
            scope = f"bound reached: holds up to depth {depth}, not beyond"
        print(f"PASS: invariants hold on {main_res['states']} states ({main_res['transitions']} transitions, "
              f"max depth {main_res['max_depth']}); {scope}")
    else:
        v = main_res["violation"]
        label = {"invariant": f'invariant "{v["name"]}" violated', "deadlock": "deadlock",
                 "error": f'model error in {v["name"]}'}[v["kind"]]
        print(f"{'FAIL' if v['kind'] != 'error' else 'ERROR'}: {label} after {len(v['trace']) - 1} step(s)"
              + (f": {v['detail']}" if v.get("detail") else ""))
        print_trace(v)
        print(f"explored {main_res['states']} states")
    if mutant_res:
        print("mutants (each broken variant must be caught, or the check proves nothing):")
        for name, m in mutant_res.items():
            if m["killed"]:
                print(f"  {name:<24} killed by {m['by']} after {m['steps']} step(s)")
            else:
                print(f"  {name:<24} SURVIVED: no invariant catches this broken variant; strengthen the property or the model")
        for name, m in mutant_res.items():
            if m["killed"]:
                print(f"counterexample for mutant {name}:")
                print_trace(m["result"]["violation"])
    if args.expect_violation:
        print(f"expect-violation {args.expect_violation}: {'met' if ok else 'NOT met'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
