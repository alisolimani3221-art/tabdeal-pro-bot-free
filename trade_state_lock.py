"""Fail-closed single-writer lock for local trade state.

The lock file is deliberately persistent: unlinking it can split contenders
across different inodes. The kernel releases flock ownership when the process
exits, including abnormal termination. Unsupported platforms/filesystems fail
closed; there is no PID-file or in-process-only fallback.
"""
import os
import stat
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised with a mocked import path
    fcntl = None


class StateLockError(RuntimeError):
    """The exclusive trade-state lock could not be safely acquired."""


class TradeStateLock:
    """Hold an OS advisory exclusive lock for a state file's runtime scope."""

    def __init__(self, state_path):
        if not isinstance(state_path, str) or not state_path.strip():
            raise ValueError("state path is required")
        if fcntl is None or not hasattr(fcntl, "flock"):
            raise StateLockError("OS file locking is unavailable")
        self.lock_path = os.path.abspath(state_path) + ".lock"
        self._thread_lock = threading.RLock()
        self._fd = None
        self._depth = 0
        self._owner = None

    def acquire(self):
        self._thread_lock.acquire()
        ident = threading.get_ident()
        if self._depth:
            if self._owner != ident:
                # RLock should make this unreachable, but retain fail-closed
                # behavior if ownership invariants are ever changed.
                self._thread_lock.release()
                raise StateLockError("trade-state lock ownership is inconsistent")
            self._depth += 1
            return self

        fd = None
        try:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StateLockError("trade-state lock is not a private regular file")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise StateLockError("trade-state lock has an unexpected owner")
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                raise StateLockError("another process already owns the trade-state lock") from exc
            self._fd = fd
            self._depth = 1
            self._owner = ident
            return self
        except StateLockError:
            if fd is not None:
                os.close(fd)
            self._thread_lock.release()
            raise
        except (OSError, AttributeError, TypeError) as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._thread_lock.release()
            raise StateLockError("could not safely acquire trade-state lock") from exc

    def release(self):
        if self._depth <= 0 or self._owner != threading.get_ident():
            raise StateLockError("trade-state lock is not owned by this thread")
        self._depth -= 1
        try:
            if self._depth == 0:
                fd, self._fd = self._fd, None
                self._owner = None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            self._thread_lock.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False

    @property
    def held(self):
        return self._depth > 0 and self._owner == threading.get_ident()
