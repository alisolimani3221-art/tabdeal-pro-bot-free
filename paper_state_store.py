"""Thread-safe atomic persistence for the local paper-trading state."""
import json
import os
import tempfile
import threading


class PaperStatePersistence:
    """Serialize snapshots and replace the state file without shared temp names."""

    def __init__(self, directory, filename="paper-engine-state.json"):
        self.directory = os.path.abspath(directory)
        self.path = os.path.join(self.directory, filename)
        self._lock = threading.RLock()

    def persist(self, engine):
        if engine is None or not callable(getattr(engine, "export_state", None)):
            raise ValueError("a valid paper engine is required")
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            snapshot = engine.export_state()
            fd, temporary_path = tempfile.mkstemp(
                prefix=".paper-engine-state-", suffix=".tmp", dir=self.directory
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as state_file:
                    json.dump(snapshot, state_file, ensure_ascii=False,
                              allow_nan=False, separators=(",", ":"))
                    state_file.flush()
                    os.fsync(state_file.fileno())
                os.replace(temporary_path, self.path)
            except Exception:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
                raise
        return self.path
