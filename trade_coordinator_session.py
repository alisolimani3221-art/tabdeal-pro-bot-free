"""Locked persistence boundary for prepared-only trade coordinator state.

This session coordinates TradeStateStore and TradeCoordinator. It has no
exchange adapter, credentials, network client, or order-submission capability.
Every operation reloads state while holding the process lock, applies a
local-only coordinator transition, and durably saves the resulting snapshot.
"""
from trade_coordinator import TradeCoordinator
from trade_state_store import StateStoreError, TradeStateStore


class TradeCoordinatorSession:
    """Serialize prepared-only coordinator transitions across app processes."""

    def __init__(self, state_path, max_open_positions=1,
                 max_notional_usdt=None):
        self.store = TradeStateStore(state_path)
        self.max_open_positions = max_open_positions
        self.max_notional_usdt = max_notional_usdt
        self._failed = False

        # Initialization is itself a transaction: no state file is created
        # unless the exclusive process lock is held successfully.
        with self.store.exclusive():
            state = self.store.load()
            if state is None:
                coordinator = TradeCoordinator(
                    max_open_positions=max_open_positions,
                    max_notional_usdt=max_notional_usdt,
                )
                self.store.save(coordinator.export_state())
            else:
                TradeCoordinator.restore_state(
                    state, max_open_positions=max_open_positions,
                    max_notional_usdt=max_notional_usdt,
                )

    def _apply(self, operation, *args, **kwargs):
        if self._failed:
            raise RuntimeError("coordinator session is fail-closed after a persistence error")

        try:
            with self.store.exclusive():
                state = self.store.load()
                if state is None:
                    raise StateStoreError("coordinator state disappeared")
                coordinator = TradeCoordinator.restore_state(
                    state, max_open_positions=self.max_open_positions,
                    max_notional_usdt=self.max_notional_usdt,
                )
                result = operation(coordinator, *args, **kwargs)
                self.store.save(coordinator.export_state())
                return result
        except StateStoreError:
            self._failed = True
            raise

    @staticmethod
    def _prepare(coordinator, **kwargs):
        return coordinator.prepare_entry(**kwargs)

    def prepare_entry(self, **kwargs):
        """Persist a validated PREPARED_ONLY intent; never submit an order."""
        return self._apply(self._prepare, **kwargs)

    @staticmethod
    def _release(coordinator, idempotency_key):
        return coordinator.release_prepared_intent(idempotency_key)

    def release_prepared_intent(self, idempotency_key):
        """Release a local prepared intent; no exchange action is performed."""
        return self._apply(self._release, idempotency_key)

    @staticmethod
    def _rearm(coordinator):
        return coordinator.rearm_after_operator_stop()

    def rearm_after_operator_stop(self):
        """Explicitly re-arm only an operator-stopped, empty prepared-only session."""
        return self._apply(self._rearm)

    @staticmethod
    def _stop(coordinator, reason):
        return coordinator.emergency_stop(reason)

    def emergency_stop(self, reason="operator emergency stop"):
        """Persist a local emergency halt for all future prepared intents."""
        return self._apply(self._stop, reason)

    def snapshot(self):
        """Return the latest validated state while holding the process lock."""
        if self._failed:
            raise RuntimeError("coordinator session is fail-closed after a persistence error")
        with self.store.exclusive():
            state = self.store.load()
            if state is None:
                raise StateStoreError("coordinator state disappeared")
            coordinator = TradeCoordinator.restore_state(
                state, max_open_positions=self.max_open_positions,
                max_notional_usdt=self.max_notional_usdt,
            )
            return coordinator.export_state()
