"""Crash-safe local persistence for fail-closed trade coordinator snapshots.

This module stores local JSON state only. It never submits exchange requests.
A corrupt or unreadable existing state raises StateStoreError; callers must
halt trading rather than silently starting with an empty state.
"""
import json
import os
import stat
import tempfile
import threading

from trade_state_lock import TradeStateLock


class StateStoreError(RuntimeError):
    """Persisted coordinator state could not be safely read or written."""


def _reject_constant(value):
    raise ValueError("non-finite JSON number: {}".format(value))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


class TradeStateStore:
    MAX_STATE_BYTES = 1024 * 1024

    def __init__(self, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("state path is required")
        self.path = os.path.abspath(path)
        self.directory = os.path.dirname(self.path)
        self._lock = threading.RLock()
        self._process_lock = TradeStateLock(self.path)

    def exclusive(self):
        """Hold the cross-process lock across a complete state transaction."""
        return self._process_lock

    def load(self):
        """Return None only when no state file exists; never mask corruption."""
        with self.exclusive(), self._lock:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise StateStoreError("cannot safely open trade state") from exc
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise StateStoreError("trade state path is not a regular file")
                if info.st_size > self.MAX_STATE_BYTES:
                    raise StateStoreError("trade state file exceeds size limit")
                with os.fdopen(fd, "rb") as handle:
                    fd = -1
                    payload = handle.read(self.MAX_STATE_BYTES + 1)
                if len(payload) > self.MAX_STATE_BYTES:
                    raise StateStoreError("trade state file exceeds size limit")
                raw = payload.decode("utf-8", errors="strict")
                state = json.loads(
                    raw, parse_constant=_reject_constant,
                    object_pairs_hook=_unique_object
                )
            except StateStoreError:
                raise
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                raise StateStoreError("trade state is unreadable or corrupt") from exc
            finally:
                if fd >= 0:
                    os.close(fd)
            if (not isinstance(state, dict)
                    or type(state.get("schema_version")) is not int
                    or state.get("schema_version") != 1):
                raise StateStoreError("trade state schema is invalid")
            return state

    def save(self, state):
        """Atomically replace state; fsync file and directory before returning."""
        if (not isinstance(state, dict)
                or type(state.get("schema_version")) is not int
                or state.get("schema_version") != 1):
            raise ValueError("unsupported coordinator state")
        try:
            payload = json.dumps(
                state, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise StateStoreError("trade state cannot be serialized") from exc
        if len(payload) > self.MAX_STATE_BYTES:
            raise StateStoreError("trade state exceeds size limit")

        with self.exclusive(), self._lock:
            temp_path = None
            try:
                os.makedirs(self.directory, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(
                    prefix=".trade-state-", suffix=".tmp",
                    dir=self.directory
                )
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, self.path)
                    temp_path = None
                    dir_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        # If the directory entry cannot be made durable, report
                        # failure so callers halt instead of assuming persistence.
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
            except (OSError, TypeError, ValueError) as exc:
                raise StateStoreError("could not persist trade state") from exc
            finally:
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
