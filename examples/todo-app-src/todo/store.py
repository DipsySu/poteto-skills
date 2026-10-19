"""Own JSON persistence, cross-process locking, and status transitions."""
import fcntl
import json
import os
from pathlib import Path
import tempfile

TRANSITIONS = {"open": "done", "done": "archived"}


class Store:
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / "todos.json"

    def _transaction(self, change=None):
        # A separate lock inode survives atomic replacement of the JSON file.
        with (self.data / "todos.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = json.loads(self.path.read_text()) if self.path.exists() else []
            result = change(rows) if change else rows
            if change:
                fd, name = tempfile.mkstemp(prefix=".todos-", dir=self.data)
                try:
                    with os.fdopen(fd, "w") as out:
                        json.dump(rows, out, sort_keys=True)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(name, self.path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            return result

    def list_all(self):
        return self._transaction()

    def add(self, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a nonempty string")

        def change(rows):
            row = {"id": max((r["id"] for r in rows), default=0) + 1,
                   "title": title.strip(), "status": "open"}
            rows.append(row)
            return row
        return self._transaction(change)

    def transition(self, todo_id, target):
        def change(rows):
            row = next((r for r in rows if r["id"] == todo_id), None)
            if row is None:
                raise KeyError(todo_id)
            if TRANSITIONS.get(row["status"]) != target:
                raise ValueError(f"cannot move {row['status']} to {target}")
            row["status"] = target
            return row
        return self._transaction(change)
