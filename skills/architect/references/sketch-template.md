# Sketch: <the change, one line>

Grounding: <the files and flows you read, with paths; the constraint each one imposes>.

## Usage

Write the call sites first. The types below are derived from these.

```python
todo = todos.create(title="Buy milk")            # caller never touches the file
todos.transition(todo.id, Status.DONE)           # illegal moves raise IllegalTransition
```

## Candidate A: <shape in one line, e.g. "store owns the file, CLI and HTTP call it">

**Shape:** <ownership, module boundaries, data flow in two sentences>.

```python
class TodoStore:
    def create(self, title: str) -> Todo: raise NotImplementedError
    def transition(self, todo_id: int, to: Status) -> Todo: raise NotImplementedError
```

Modules: `todo/store.py` (owns `todos.json`), `todo/cli.py`, `todo/server.py`.

## Candidate B: <a different whole shape, not a tweak of A>

**Shape:** <e.g. "event log; state is a fold over events">.

```python
class Event: ...
def append(event: Event) -> None: raise NotImplementedError
def fold(events: list[Event]) -> dict[int, Todo]: raise NotImplementedError
```

Modules: `todo/events.py`, `todo/projection.py`.

## Red-flags screen

Rate every candidate against `references/design-red-flags.md`. Use `ok` or a short reason it fails.

| Flag | A | B |
|---|---|---|
| Two ways to do one thing | ok | ok |
| State with more than one writer | ok | ok: log is append-only |
| Callers must know internal rules | ok | fails: callers must fold before reading |
| Shallow module / pass-through layer | ok | ok |

## Synthesis decision

Candidate A, because it hides the file format behind the smallest public surface and needs no new concept. It borrows B's explicit `Status` transition table. B is rejected: every reader would need the fold, which is a rule callers must know.

## Open questions

- <question>: <how you will answer it, e.g. a prototype or a measurement>.
