"""Single-instance runtime guard for the Android app.

The lock lives under Kivy's app-private user_data_dir. It is held from
application on_start until on_stop, and is independent from any exchange API.
"""
import os

from trade_state_lock import StateLockError, TradeStateLock


class AppRuntimeGuard:
    def __init__(self, user_data_dir):
        if not isinstance(user_data_dir, str) or not user_data_dir.strip():
            raise ValueError("app-private data directory is required")
        self.directory = os.path.abspath(user_data_dir)
        self.lock = TradeStateLock(os.path.join(self.directory, "app-runtime"))
        self.error = None
        self.started = False

    def start(self):
        if self.started:
            return True
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
            self.lock.acquire()
            self.started = True
            self.error = None
            return True
        except (OSError, StateLockError) as exc:
            self.error = "{}: {}".format(type(exc).__name__, str(exc)[:160])
            self.started = False
            return False

    def stop(self):
        if not self.started:
            return False
        self.lock.release()
        self.started = False
        return True

    @property
    def held(self):
        return self.started and self.lock.held
