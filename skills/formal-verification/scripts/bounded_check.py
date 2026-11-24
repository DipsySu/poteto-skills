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
  CHECK_TIMEOUT              default for --timeout (seconds for the whole run)

States must be hashable or built from dicts, lists, sets, tuples, namedtuples, or dataclasses.
The checker explores breadth-first up to --depth steps, so the counterexample it prints is a
shortest one. Exit 0 = every invariant holds within the bound (and every mutant was killed),
1 = violation, deadlock, or surviving mutant, 2 = model or usage error (including a call to
actions(), an invariant, or a mutant that runs past --call-timeout), 3 = stopped at the
--timeout run limit with nothing failed: not a failure and not a proof.
Zero disables the corresponding time limit. While enabled, the checker reserves
SIGALRM; model code must not use alarm/setitimer.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from contextlib import contextmanager

DEFAULT_TIMEOUT = 90.0
DEFAULT_CALL_TIMEOUT = 10.0
INFINITY = float("inf")


class ModelError(Exception):
    pass


class ModelImportError(ModelError):
    """Keep the original exception without formatting it while importing."""
    def __init__(self, path, original):
        self.context, self.original = f"importing {path} failed", original
        super().__init__(f"{self.context}: {type(original).__name__}: <exception message not formatted>")


class Timeout(BaseException):
    """A time limit stopped a call or the run (a BaseException, so `except Exception` can't swallow it)."""

    def __init__(self, scope, seconds):
        super().__init__(f"no return within {seconds:g} s" if scope == "call"
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
        self.armed_deadline = None
        self.limit_run(run_seconds)
        self.started = None
        self.expired = False
        self.previous = None
        self.recording = 0
        self.pending = None
        self.active = (callable(getattr(signal, "setitimer", None)) and hasattr(signal, "SIGALRM")
                       and hasattr(signal, "ITIMER_REAL")
                       and threading.current_thread() is threading.main_thread())

    def limit_run(self, seconds):
        self.run_seconds = seconds
        self.deadline = self.begun + seconds if seconds else INFINITY
        self.arm()

    def arm(self, keep_earlier=False):
        """Reserve SIGALRM for the nearest enabled deadline, never for periodic polling."""
        if not self.installed:
            return
        call_deadline = (self.started + self.call_seconds
                         if self.started is not None and self.call_seconds else INFINITY)
        deadline = min(self.deadline, call_deadline)
        # Search calls may retain an earlier one-shot alarm. It will check the current
        # absolute deadlines and rearm if it arrives early; never postpone a deadline.
        if (keep_earlier and not self.expired and self.armed_deadline is not None
                and self.armed_deadline <= deadline):
            return
        if signal.getsignal(signal.SIGALRM) != self.tick:
            return
        delay = max(0.0, deadline - time.monotonic()) if deadline < INFINITY and not self.expired else 0
        signal.setitimer(signal.ITIMER_REAL, delay)
        self.armed_deadline = deadline if delay else None

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
            print("bounded_check: WARNING: --timeout and --call-timeout are not enforced; "
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
        self.armed_deadline = None  # The OS consumed this one-shot alarm.
        try:
            self.check()
            self.arm()
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
                         if self.started is not None and self.call_seconds else INFINITY)
        if now >= self.deadline:
            if call_deadline < self.deadline:
                raise Timeout("call", self.call_seconds)
            self.expired = True
            raise Timeout("run", self.run_seconds)
        if now >= call_deadline:
            raise Timeout("call", self.call_seconds)

    def check_run(self):
        """Do not start new work after the run deadline, even between timer ticks."""
        if self.active and time.monotonic() >= self.deadline:
            self.expired = True
            raise Timeout("run", self.run_seconds)

    def call(self, fn, *args, coalesce=False, truth=False):
        self.started = time.monotonic()
        try:
            self.arm(keep_earlier=coalesce)
            self.check()
            result = fn(*args)
            return bool(result) if truth else result
        finally:
            try:
                self.check()
            finally:
                self.started = None
                self.arm(keep_earlier=coalesce)


def seconds(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float("inf"):
        raise ModelError(f"{name} must be a nonnegative finite number of seconds (0 disables the limit)")
    return float(value)


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
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 - report any import-time failure of the model
        raise ModelImportError(path, e) from e
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


def search_result() -> dict:
    return {"result": "pass", "states": 0, "transitions": 0, "max_depth": 0, "hit_depth_bound": False,
            "hit_state_bound": False, "violation": None, "stopped": None, "detail": None}


def exception_detail(error, where, *, formatted=False):
    """Keep error identity without running user __str__ while recording a finding."""
    message = str(error) if formatted else '<exception message not formatted>'
    return f"{where} raised {type(error).__name__}: {message}"


@contextmanager
def model_errors(limits):
    """An existing model error remains exit 2 if its message exceeds the run budget."""
    try:
        yield
    except ModelError as error:
        try:
            with limits.record_result():
                detail = (error.args[0] if type(error) is ModelImportError
                          else exception_detail(error, "model"))
            detail = (f"{error.context}: {type(error.original).__name__}: {error.original}"
                      if type(error) is ModelImportError else str(error))
            limits.check_run()
        except Timeout:
            pass  # The error was already established; keep its safe diagnostic.
        raise ModelError(detail) from error


def explore(mod, actions_fn, invariants: dict, depth: int, max_states: int, deadlock: bool, limits: Limits,
            out: dict | None = None) -> dict:
    """Search breadth-first; the run limit returns result "timeout" with the states seen so far."""
    states: dict = {}
    if out is None:
        out = search_result()
    try:
        limits.check_run()
        search(mod, actions_fn, invariants, depth, max_states, deadlock, limits, states, out)
        limits.check_run()
        return out
    except Timeout as e:
        if e.scope != "run":
            raise
        out.update(stopped=str(e), states=len(states), exhaustive=False)
        if out["violation"] is None:
            out["result"] = "timeout"
            out["detail"] = str(e)
        else:
            out["violation"]["trace_incomplete"] = str(e)
        return out


def search(mod, actions_fn, invariants: dict, depth: int, max_states: int, deadlock: bool, limits: Limits,
           states: dict, out: dict) -> dict:
    describe = getattr(mod, "describe", None)
    terminal = getattr(mod, "terminal", None)
    limited = limits.active and bool(limits.run_seconds or limits.call_seconds)
    parent: dict = {}  # key -> (parent_key, action, depth)
    queue: deque = deque()

    def state_fallback(s, act, d):
        scalar = type(s) in (type(None), bool, int, float, str)
        return {"step": d, "action": act, "state": s if scalar else None, "describe": None,
                **({} if scalar else {"state_incomplete": True})}

    def trace(k, steps) -> None:
        keys = []
        while k is not None:
            keys.append(k)
            k = parent[k][0]
        keys.reverse()
        # Publish JSON-safe placeholders first. User serializers remain interruptible.
        steps.clear()
        for k in keys:
            pk, act, d = parent[k]
            s = states[k]
            scalar = type(s) in (type(None), bool, int, float, str)
            steps.append({"step": d, "action": act, "state": s if scalar else None,
                          "describe": None, **({} if scalar else {"state_incomplete": True})})
        for k, step in zip(keys, steps):
            step["state"] = jsonable(states[k])
            step.pop("state_incomplete", None)
        if callable(describe):
            for k, step in zip(keys, steps):
                limits.check_run()
                step["describe"] = describe(states[k])
                limits.check_run()

    def bad(k, kind: str, name: str, detail: str | None = None, timeout: bool = False,
            record_only: bool = False, fallback=None) -> dict:
        out["result"] = "error" if kind == "error" else "violation"
        out["detail"] = detail
        out["violation"] = {"kind": kind, "name": name, "detail": detail,
                            "trace": [fallback] if fallback is not None else []}
        if timeout:  # a call over --call-timeout: a model error, with the trace to the state it hung on
            out["violation"]["timeout"] = True
            out["stopped"] = detail
        out["states"] = len(states)
        if not record_only:
            trace(k, out["violation"]["trace"])
        return out

    def check(k, s):
        fallback = None
        for name, fn in invariants.items():
            found = error = None
            if limited:
                # Only built-in state metadata is recorded while alarms are deferred.
                # Preparing the fallback here also keeps custom key/hash work interruptible.
                if fallback is None:
                    pk, act, d = parent[k]
                    fallback = state_fallback(s, act, d)
                limits.recording += 1
            try:
                try:
                    ok = limits.call(fn, s, coalesce=True, truth=True) if limited else bool(fn(s))
                except Timeout as e:
                    if e.scope == "run":
                        raise
                    found = bad(k, "error", name, str(e), timeout=True, record_only=True, fallback=fallback)
                except Exception as e:  # noqa: BLE001 - an invariant that crashes is a model bug; show where
                    # Store the error identity before invoking any user-defined exception formatting.
                    found = bad(k, "error", name, exception_detail(e, "invariant"),
                                record_only=True, fallback=fallback)
                    error = e
                else:
                    if not ok:
                        found = bad(k, "invariant", name, record_only=True, fallback=fallback)
            finally:
                if limited:
                    limits.recording -= 1
                    if not limits.recording and limits.pending is not None:
                        pending, limits.pending = limits.pending, None
                        raise pending
            if found is not None:
                if error is not None:
                    detail = exception_detail(error, "invariant", formatted=True)
                    found["detail"] = found["violation"]["detail"] = detail
                trace(k, out["violation"]["trace"])
                return found
        return None

    starts = initial_states(mod)
    if limited: limits.check_run()
    if not starts:
        raise ModelError("model has no initial states")
    for s in starts:
        if limited: limits.check_run()
        k = freeze(s)
        if k in states:
            continue
        states[k] = s
        parent[k] = (None, "init", 0)
        if (r := check(k, s)) is not None:
            return r
        queue.append(k)
    while queue:
        if limited: limits.check_run()
        k = queue.popleft()
        s = states[k]
        pk, act, d = parent[k]
        if d >= depth:
            out["hit_depth_bound"] = True
            continue
        found = error = None
        if limited:
            limits.recording += 1
        try:
            try:
                succ = limits.call(lambda: list(actions_fn(s)), coalesce=True) if limited else list(actions_fn(s))
            except Timeout as e:
                if e.scope == "run":
                    raise
                found = bad(k, "error", "actions", str(e), timeout=True,
                            record_only=True, fallback=state_fallback(s, act, d))
            except Exception as e:  # noqa: BLE001
                found = bad(k, "error", "actions", exception_detail(e, "actions()"),
                            record_only=True, fallback=state_fallback(s, act, d))
                error = e
        finally:
            if limited:
                limits.recording -= 1
                if not limits.recording and limits.pending is not None:
                    pending, limits.pending = limits.pending, None
                    raise pending
        if found is not None:
            if error is not None:
                detail = exception_detail(error, "actions()", formatted=True)
                found["detail"] = found["violation"]["detail"] = detail
            trace(k, out["violation"]["trace"])
            return found
        if not succ and deadlock:
            if limited: limits.check_run()
            is_terminal = callable(terminal) and terminal(s)
            if limited: limits.check_run()
            if not is_terminal:
                return bad(k, "deadlock", "deadlock", "no action is enabled and terminal(state) is not true")
        for item in succ:
            if limited: limits.check_run()
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
    if v.get("trace_incomplete"):
        print(f"  trace descriptions incomplete: {v['trace_incomplete']}; confirmed finding retained")


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
               "optional terminal(state), describe(state), mutants = {name: actions_fn}, DEPTH, MAX_STATES, CHECK_TIMEOUT.\n"
               "exit: 0 holds within the bound, 1 violation/deadlock/surviving mutant, 2 model or usage error\n"
               "(a call to actions(), an invariant, or a mutant past --call-timeout is a model error),\n"
               "3 stopped at the --timeout run limit: not a failure and not a proof\n\n"
               "limits:\n"
               "  Defaults: 90 s per run and 10 s per call. The run includes the main search and all mutants.\n"
               "  Imports initially use --timeout or 90 s; MODEL.CHECK_TIMEOUT then applies to elapsed time\n"
               "  since startup, unless --timeout overrides it. An exhausted budget starts no search.\n"
               "  --timeout 0 and --call-timeout 0 disable their limits independently; CHECK_TIMEOUT=0\n"
               "  disables only the post-import run limit. Previously unlimited checks may need explicit budgets.\n"
               "  The call limit covers actions, invariants and mutant actions. Imports, initialization,\n"
               "  terminal predicates and trace descriptions use the run limit. No mutant starts after it expires.\n\n"
               "signal handling:\n"
               "  Limits reserve SIGALRM/setitimer on the Unix main thread; model code must not use them.\n"
               "  Unsupported platforms or worker threads warn on stderr that limits are not enforced.\n"
               "  Native code can delay signals: keep an external process deadline for a hard wall-clock bound.\n"
               "  One-shot alarms fire no later than the earlier run/call deadline. Searches may reuse an\n"
               "  earlier alarm, recheck current deadlines and rearm; return-time checks still apply.\n"
               "  Only after expiry, a short retry is armed before raising, in case code swallows the timeout.\n"
               "  If both deadlines elapsed, the earlier absolute deadline wins; ties use the run deadline.\n"
               "  On exit the previous handler/timer is restored, accounting for elapsed time, if still owned.\n\n"
               "incomplete output:\n"
               "  Confirmed results are recorded before pending timeouts are delivered. State formatting and\n"
               "  exception __str__ run afterward and remain interruptible. A description timeout keeps the\n"
               "  violation and serialized trace, marked trace_incomplete. Interrupted serialization keeps\n"
               "  step/action metadata with state: null and state_incomplete: true for unfinished values.\n"
               "  Exception type, minimal diagnostic and confirmed result are saved before rendering a message.\n"
               "  Interrupted invariant/actions/mutant exception messages retain exit 2 and available trace;\n"
               "  model-import errors retain their stderr diagnostic and exit 2.\n"
               "  --json also reports run timeouts during model import. Mutants include result, stopped, detail\n"
               "  and state counts; top-level incomplete and not_run identify unfinished work. Crashing mutants\n"
               "  are model errors (exit 2), never survivors. Measure representative models when choosing budgets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("model", type=Path, help="the MODEL.py file")
    ap.add_argument("--depth", type=int, help="max steps from an initial state (default: MODEL.DEPTH or 25)")
    ap.add_argument("--max-states", type=int, help="stop after this many distinct states (default: MODEL.MAX_STATES or 200000)")
    ap.add_argument("--invariant", action="append", default=[], metavar="NAME", help="check only these invariants (repeatable)")
    ap.add_argument("--deadlock", action="store_true", help="also fail on states with no enabled action (unless terminal)")
    ap.add_argument("--mutants", action="store_true", help="also run every MODEL.mutants entry; each must violate an invariant")
    ap.add_argument("--expect-violation", metavar="NAME", help="succeed only if invariant NAME is violated (able-to-fail proof)")
    ap.add_argument("--timeout", type=float, metavar="S",
                    help="seconds for the whole run, then exit 3 (overrides MODEL.CHECK_TIMEOUT; default: 90; "
                         "imports use CLI or 90; 0 disables)")
    ap.add_argument("--call-timeout", type=float, default=DEFAULT_CALL_TIMEOUT, metavar="S",
                    help="seconds for one call to actions(), an invariant, or a mutant, then exit 2 (default: 10; 0 disables)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    main_res, searches, muts = search_result(), {}, {}
    main_started = False
    current = None
    stopped_reason = None
    try:
        if args.timeout is not None:
            seconds(args.timeout, "--timeout")
        limits = Limits(DEFAULT_TIMEOUT if args.timeout is None else args.timeout,
                        seconds(args.call_timeout, "--call-timeout"))
        with limits, model_errors(limits):
            mod = load_model(args.model)
            invariants = dict(mod.invariants)
            if args.invariant:
                unknown = [n for n in args.invariant if n not in invariants]
                if unknown:
                    raise ModelError(f"unknown invariant(s): {', '.join(unknown)}; known: {', '.join(invariants)}")
                invariants = {n: invariants[n] for n in args.invariant}
            if args.expect_violation and args.expect_violation not in invariants:
                raise ModelError(f"--expect-violation {args.expect_violation}: no such invariant; known: {', '.join(invariants)}")
            depth = args.depth if args.depth is not None else getattr(mod, "DEPTH", 25)
            max_states = args.max_states if args.max_states is not None else getattr(mod, "MAX_STATES", 200_000)
            if depth < 0 or max_states < 1:
                raise ModelError("depth must be nonnegative and max-states must be positive")
            muts = getattr(mod, "mutants", {})
            if args.mutants and (not isinstance(muts, dict) or not muts):
                raise ModelError("--mutants needs MODEL.mutants = {name: broken_actions_fn}")
            if args.timeout is None:
                limits.limit_run(seconds(getattr(mod, "CHECK_TIMEOUT", DEFAULT_TIMEOUT), "MODEL.CHECK_TIMEOUT"))
            if not args.json:  # --json output carries the bounds; keep it parseable
                print(f"bounded_check: {args.model}: depth <= {depth}, max {max_states} states, "
                      f"{limits.run_seconds:g} s run limit, {limits.call_seconds:g} s per call",
                      file=sys.stderr, flush=True)
            t0 = time.time()
            main_started = True
            current = main_res
            explore(mod, mod.actions, invariants, depth, max_states, args.deadlock, limits, main_res)
            current = None
            main_res["seconds"] = round(time.time() - t0, 3)
            if args.mutants and main_res["result"] != "timeout" and not limits.expired:
                for name, fn in muts.items():
                    limits.check_run()
                    # Store before exploring so a signal between searches cannot discard prior results.
                    current = searches[name] = search_result()
                    explore(mod, fn, invariants, depth, max_states, args.deadlock, limits, current)
                    current = None
                    if searches[name]["result"] == "timeout" or limits.expired:
                        break
            limits.check_run()
    except ModelError as e:
        print(f"bounded_check: {e}", file=sys.stderr)
        return 2
    except Timeout as e:  # outside a search: while importing the model or between searches
        stopped_reason = f"{e} outside the search"
        if current is not None:
            current.update(stopped=stopped_reason, exhaustive=False)
            if current["violation"] is None:
                current.update(result="timeout", detail=stopped_reason)
        if not main_started:
            # Import/validation stopped before the main search was started.
            main_res.update(result="timeout", stopped=stopped_reason, detail=stopped_reason, exhaustive=False)
            depth, max_states, invariants = args.depth, args.max_states, {}

    # The run limit is inconclusive, so exit 3 unless a completed search failed outright.
    not_run = [name for name in muts if name not in searches] if args.mutants else []
    incomplete = stopped_reason or next((r["stopped"] for r in (main_res, *searches.values()) if r["stopped"]), None)
    if not_run:
        incomplete = incomplete or str(Timeout("run", limits.run_seconds))
        for name in not_run:
            searches[name] = dict(search_result(), result="timeout", stopped=incomplete,
                                  detail=f"not run: {incomplete}", exhaustive=False)
    mutant_res = {}
    for name, r in searches.items():
        v = r["violation"]
        mutant_res[name] = {"killed": r["result"] == "violation" and v["kind"] == "invariant",
                            "by": v["name"] if v else None,
                            "steps": len(v["trace"]) - 1 if v else None, "result": r}
    if args.expect_violation:
        v = main_res["violation"]
        ok = bool(v) and v["name"] == args.expect_violation and v["kind"] == "invariant"
        definitive = main_res["result"] != "timeout"
    else:
        ok = (main_res["result"] == "pass" and all(m["killed"] for m in mutant_res.values())
              and len(mutant_res) == (len(muts) if args.mutants else 0) and not incomplete)
        definitive = main_res["result"] == "violation" or any(
            not m["killed"] and m["result"]["result"] in ("pass", "violation") for m in mutant_res.values())
    if main_res["result"] == "error" or any(r["result"] == "error" for r in searches.values()):
        code = 2
    else:
        code = 0 if ok else 3 if incomplete and not definitive else 1
    ok = ok and code == 0

    if args.json:
        print(json.dumps({"model": str(args.model), "depth": depth, "max_states": max_states,
                          "timeout": limits.run_seconds, "call_timeout": limits.call_seconds,
                          "invariants": list(invariants), "ok": ok, **main_res,
                          "incomplete": incomplete, "not_run": not_run,
                          "mutants": {k: {kk: vv for kk, vv in m.items() if kk != "result"} | {
                              **{key: m["result"].get(key) for key in ("result", "stopped", "detail", "states", "transitions", "max_depth")},
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
    elif main_res["result"] == "timeout":
        print(f"INCOMPLETE: {main_res['stopped']} after {main_res['states']} states ({main_res['transitions']} "
              f"transitions, max depth {main_res['max_depth']}); not a failure and not a proof")
    else:
        v = main_res["violation"]
        label = {"invariant": f'invariant "{v["name"]}" violated', "deadlock": "deadlock",
                 "error": f'model error in {v["name"]}'}[v["kind"]]
        if v.get("timeout"):
            print(f"ERROR: {label}: {v['detail']}, {len(v['trace']) - 1} step(s) from the initial state")
        else:
            print(f"{'FAIL' if v['kind'] != 'error' else 'ERROR'}: {label} after {len(v['trace']) - 1} step(s)"
                  + (f": {v['detail']}" if v.get("detail") else ""))
        print_trace(v)
        print(f"explored {main_res['states']} states")
    if mutant_res:
        print("mutants (each broken variant must be caught, or the check proves nothing):")
        for name, m in mutant_res.items():
            r = m["result"]
            if m["killed"]:
                print(f"  {name:<24} killed by {m['by']} after {m['steps']} step(s)")
            elif r["result"] == "timeout":
                print(f"  {name:<24} INCOMPLETE: {r['stopped']} after {r['states']} states"
                      + ("; not run" if name in not_run else ""))
            elif r["result"] == "error":
                print(f"  {name:<24} ERROR: model error in {m['by']}: {r['violation']['detail']} after {m['steps']} step(s)")
            else:
                print(f"  {name:<24} SURVIVED: no invariant catches this broken variant; strengthen the property or the model")
        for name, m in mutant_res.items():
            if m["killed"]:
                print(f"counterexample for mutant {name}:")
                print_trace(m["result"]["violation"])
            elif m["result"]["result"] == "error":
                print(f"trace to the error in mutant {name}:")
                print_trace(m["result"]["violation"])
    if incomplete:
        print(f"INCOMPLETE: {incomplete}; confirmed findings retained")
    if args.expect_violation:
        print(f"expect-violation {args.expect_violation}: {'met' if ok else 'NOT met'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
