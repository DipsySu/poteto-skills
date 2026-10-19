"""Finite abstraction of todo/store.py, not a proof of Python or filesystem semantics.

Store._transaction: flock -> read snapshot -> append -> atomic replace -> unlock.
Two writers each add one distinct title, with all interleavings explored.
Store.transition: open -> done -> archived, modelled on a seed record.
Assumptions: cooperating processes use the same lock inode; replace is atomic;
no disk failure/process crash. Bounds: two writers, two status transitions.
"""
from __future__ import annotations
from dataclasses import dataclass, replace

DEPTH = 20
MAX_STATES = 10000


@dataclass(frozen=True)
class State:
    rows: tuple = ("seed",)
    phases: tuple = (0, 0)
    snapshots: tuple = ((), ())
    owner: int = -1
    status: str = "open"
    edges: tuple = ()


def init():
    return State()


def step(state, unlocked=False, bad_status=False, repeated_status=False):
    out = []
    for i in range(2):
        phases, snapshots = list(state.phases), list(state.snapshots)
        if phases[i] == 0 and (unlocked or state.owner == -1):
            phases[i], snapshots[i] = 1, state.rows
            out.append((f"writer {i}: read snapshot", replace(state, phases=tuple(phases), snapshots=tuple(snapshots),
                                                              owner=-1 if unlocked else i)))
        elif phases[i] == 1 and (unlocked or state.owner == i):
            phases[i] = 2
            out.append((f"writer {i}: append + replace + unlock", replace(state, rows=snapshots[i] + (f"writer-{i}",),
                                                                         phases=tuple(phases), owner=-1)))
    target = {"open": "done", "done": "archived"}.get(state.status)
    if target:
        out.append((f"status {state.status} -> {target}", replace(state, status=target, edges=state.edges + ((state.status, target),))))
    if bad_status and state.status == "open":
        out.append(("BUG: archive open todo", replace(state, status="archived", edges=state.edges + (("open", "archived"),))))
    if repeated_status and state.status == "done" and len(state.edges) < 3:
        out.append(("BUG: complete done todo", replace(state, edges=state.edges + (("done", "done"),))))
    return out


def actions(state):
    return step(state)


def lost_update(state):
    return step(state, unlocked=True)


def skip_done(state):
    return step(state, bad_status=True)


def repeat_done(state):
    return step(state, repeated_status=True)


invariants = {
    "no_lost_todo": lambda s: all(s.phases[i] != 2 or f"writer-{i}" in s.rows for i in range(2)),
    "legal_status": lambda s: all(edge in (("open", "done"), ("done", "archived")) for edge in s.edges),
}
mutants = {"unlocked_writers": lost_update, "skip_done": skip_done, "repeat_done": repeat_done}
