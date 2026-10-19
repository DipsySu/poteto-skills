import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from todo.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_persistence_and_ids(self):
        self.assertEqual(self.store.add(" first ")["title"], "first")
        self.assertEqual(Store(self.temp.name).add("second")["id"], 2)
        self.assertEqual(len(self.store.list_all()), 2)

    def test_forward_transitions(self):
        row = self.store.add("task")
        self.assertEqual(self.store.transition(row["id"], "done")["status"], "done")
        self.assertEqual(self.store.transition(row["id"], "archived")["status"], "archived")

    def test_cannot_skip_done(self):
        row = self.store.add("task")
        with self.assertRaises(ValueError):
            self.store.transition(row["id"], "archived")
        self.assertEqual(self.store.list_all()[0]["status"], "open")

    def test_cannot_complete_twice(self):
        row = self.store.add("task")
        self.store.transition(row["id"], "done")
        with self.assertRaises(ValueError):
            self.store.transition(row["id"], "done")

    def test_archived_is_terminal(self):
        row = self.store.add("task")
        self.store.transition(row["id"], "done")
        self.store.transition(row["id"], "archived")
        with self.assertRaises(ValueError):
            self.store.transition(row["id"], "done")

    def test_validation_preserves_file(self):
        self.store.add("task")
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add(" ")
        with self.assertRaises(KeyError):
            self.store.transition(99, "done")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(Path(self.temp.name).glob(".todos-*")), [])

    def test_cli_uses_shared_data(self):
        def cli(*args, ok=True):
            result = subprocess.run([sys.executable, "-m", "todo", *args, "--data", self.temp.name],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
            return json.loads(result.stdout) if ok else None
        row = cli("add", "from CLI")
        cli("rm", str(row["id"]), ok=False)
        self.assertEqual(cli("done", str(row["id"]))["status"], "done")
        self.assertEqual(cli("rm", str(row["id"]))["status"], "archived")
        self.assertEqual(cli("list"), self.store.list_all())
