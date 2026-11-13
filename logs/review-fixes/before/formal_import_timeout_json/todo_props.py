"""Check the shipped Store's title normalization and persisted round trip."""
from pathlib import Path
import shutil
import tempfile

from gen_check import reject, text
from todo.store import Store

TARGETS = ["todo/store.py:Store.add", "todo/store.py:Store.list_all"]
_temp = tempfile.TemporaryDirectory(prefix="gen-check-todo-")
_data = Path(_temp.name) / "data"


def reset():
    shutil.rmtree(_data, ignore_errors=True)


def round_trip(title):
    if not title.strip():
        reject()
    store = Store(_data)
    returned = store.add(title)
    stored = Store(_data).list_all()
    assert returned["title"] == title.strip()
    assert len(stored) == 1
    assert stored[0]["id"] == returned["id"]
    assert stored[0]["title"] == title.strip()
    assert stored[0]["status"] == "open"


properties = {"round_trip": (text(alphabet=" ab", max_size=20), round_trip)}
examples = {"round_trip": ["  pinned title  "]}
