import json
import os
import tempfile
import threading
import unittest

from paper_state_store import PaperStatePersistence


class FakeEngine:
    def __init__(self, value):
        self.value = value

    def export_state(self):
        return {"schema_version": 1, "value": self.value}


class PaperStatePersistenceTests(unittest.TestCase):
    def test_persists_json_atomically_and_creates_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "private", "state")
            store = PaperStatePersistence(directory)
            path = store.persist(FakeEngine(7))
            with open(path, "r", encoding="utf-8") as source:
                self.assertEqual(json.load(source), {"schema_version": 1, "value": 7})
            self.assertEqual(os.listdir(directory), ["paper-engine-state.json"])

    def test_concurrent_writes_never_leave_partial_json_or_shared_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStatePersistence(directory)
            failures = []

            def write(value):
                try:
                    store.persist(FakeEngine(value))
                except Exception as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=write, args=(value,))
                       for value in range(32)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            with open(store.path, "r", encoding="utf-8") as source:
                state = json.load(source)
            self.assertIn(state["value"], range(32))
            self.assertEqual(os.listdir(directory), ["paper-engine-state.json"])

    def test_rejects_missing_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                PaperStatePersistence(directory).persist(None)


if __name__ == "__main__":
    unittest.main()
