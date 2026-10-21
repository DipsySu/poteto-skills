from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
import todo.store
from pathlib import Path
import tempfile
_temp = tempfile.TemporaryDirectory()
store = todo.store.Store(_temp.name)

def add(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a nonempty string")
    rows = []
    row = {"id": max((r["id"] for r in rows), default=0) + 1,
           "title": title.strip(), "status": "open"}
    rows.append(row)
    return row
def check(title):
    assert add(title)["title"] == title.strip()
properties = {"copy": (sampled_from([" title "]), check)}
