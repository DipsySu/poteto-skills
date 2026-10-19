"""Example model for bounded_check.py: two `todo add` processes append to one JSON file.

Code being modelled (a todo/store.py in the style of the poteto-skills example app):

    def add(data_dir, title):
        with file_lock(data_dir):          # acquire ... release
            todos = load(data_dir)         # read todos.json
            todos.append(title)
            save(data_dir, todos)          # write todos.json (atomic rename)

Each action below is one atomic step of that code: acquire, read, write+release.
Property (no_lost_todo): once a client has finished, its todo is in the file.
Harm if broken: a user's todo silently disappears when two adds overlap.

Broken variants, kept as mutants so the check is shown able to fail:
  no_lock               file_lock removed: both clients read the same list, the second write drops the first todo
  release_before_write  the lock is released after load() instead of after save()

  python3 bounded_check.py example_model.py             # PASS on the locked design
  python3 bounded_check.py example_model.py --mutants   # both mutants must be killed
"""
from typing import NamedTuple

CLIENTS = ("a", "b")
DEPTH = 20


class State(NamedTuple):
    file: tuple        # todos persisted in todos.json
    pc: tuple          # per client: "start" | "locked" | "read" | "done"
    local: tuple       # per client: the list it loaded (None before load)
    lock: str | None   # client holding file_lock


def init() -> State:
    n = len(CLIENTS)
    return State(file=(), pc=("start",) * n, local=(None,) * n, lock=None)


def _set(t: tuple, i: int, v) -> tuple:
    return t[:i] + (v,) + t[i + 1:]


def _steps(s: State, mode: str):
    """mode: locked (the real code), no_lock, early_release."""
    for i, c in enumerate(CLIENTS):
        pc = s.pc[i]
        if pc == "start":
            if mode == "no_lock":
                yield f"{c}: enter add() without lock", s._replace(pc=_set(s.pc, i, "locked"))
            elif s.lock is None:
                yield f"{c}: acquire file_lock", s._replace(pc=_set(s.pc, i, "locked"), lock=c)
        elif pc == "locked":
            nxt = s._replace(pc=_set(s.pc, i, "read"), local=_set(s.local, i, s.file))
            if mode == "early_release":
                nxt = nxt._replace(lock=None)
            yield f"{c}: load {list(s.file)}", nxt
        elif pc == "read":
            written = s.local[i] + (f"todo-{c}",)
            nxt = s._replace(file=written, pc=_set(s.pc, i, "done"), local=_set(s.local, i, None))
            if mode == "locked":
                nxt = nxt._replace(lock=None)
            yield f"{c}: save {list(written)}", nxt


def actions(s: State):
    return list(_steps(s, "locked"))


def no_lost_todo(s: State) -> bool:
    return all(f"todo-{c}" in s.file for c, pc in zip(CLIENTS, s.pc) if pc == "done")


def lock_held_in_critical_section(s: State) -> bool:
    return s.lock is None or s.pc[CLIENTS.index(s.lock)] in ("locked", "read")


invariants = {"no_lost_todo": no_lost_todo, "lock_held_in_critical_section": lock_held_in_critical_section}

mutants = {
    "no_lock": lambda s: list(_steps(s, "no_lock")),
    "release_before_write": lambda s: list(_steps(s, "early_release")),
}


def terminal(s: State) -> bool:
    return all(pc == "done" for pc in s.pc)


def describe(s: State) -> str:
    clients = " ".join(f"{c}:{pc}" for c, pc in zip(CLIENTS, s.pc))
    return f"file={list(s.file)} {clients} lock={s.lock or '-'}"
