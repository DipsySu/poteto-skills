# Todo ownership sketch

## Usage

The CLI and HTTP handler call one supported mutation boundary.

## Candidate A: Locked repository

```python
class TodoRepository:
    def add(self, title: str) -> dict: ...
    def transition(self, todo_id: int, target: str) -> dict: ...
```

Modules: todo/store.py owns locks and state; callers pass validated inputs.

## Red-flags screen

| Flag | A | B |
|---|---|---|
| Duplicate state ownership | one repository | log plus projection |
| Caller coordination burden | one mutation call | event version and replay |

## Synthesis decision

Choose Candidate A because a single locked repository enforces this small app's mutation boundary with fewer moving parts; retain Candidate B for future audit-log needs.
