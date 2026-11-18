"""Fail-closed coordinator for preparing (not submitting) live futures intents.

This layer joins risk sizing, exchange-filter preflight, idempotency and
restart recovery. It intentionally has no network client and cannot place
orders. Live execution remains a separate, disabled integration step.
"""
import copy
import threading
from decimal import Decimal, InvalidOperation

from trade_preflight import validate_order_preflight


def _finite_positive(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError("{} must be finite and positive".format(name))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("{} must be finite and positive".format(name))
    if not number.is_finite() or number <= 0:
        raise ValueError("{} must be finite and positive".format(name))
    return number


class TradeCoordinator:
    def __init__(self, max_open_positions=1, max_notional_usdt=None):
        if isinstance(max_open_positions, bool) or not isinstance(max_open_positions, int) or max_open_positions < 1:
            raise ValueError("max_open_positions must be a positive integer")
        self.max_open_positions = max_open_positions
        self.max_notional_usdt = max_notional_usdt
        self._lock = threading.RLock()
        self._halted = False
        self._halt_reason = None
        self._intents = {}
        self._active_symbols = set()

    @property
    def halted(self):
        return self._halted

    def emergency_stop(self, reason="operator emergency stop"):
        with self._lock:
            self._halted = True
            self._halt_reason = str(reason)[:160]
            return {"halted": True, "reason": self._halt_reason,
                    "live_order_submitted": False}

    def prepare_entry(self, *, idempotency_key, symbol, side, quantity,
                      reference_price, symbol_info, open_positions,
                      stop_loss, take_profit, account_snapshot_age_seconds,
                      max_snapshot_age_seconds=10):
        """Validate and reserve one intent; never submit an exchange order."""
        with self._lock:
            if self._halted:
                raise RuntimeError("coordinator is halted: {}".format(self._halt_reason))
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("idempotency_key is required")
            key = idempotency_key.strip()
            if key in self._intents:
                raise RuntimeError("duplicate idempotency key")

            if isinstance(account_snapshot_age_seconds, bool):
                raise ValueError("account snapshot age is invalid")
            try:
                age = Decimal(str(account_snapshot_age_seconds))
                max_age = Decimal(str(max_snapshot_age_seconds))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError("account snapshot age is invalid")
            if (not age.is_finite() or not max_age.is_finite()
                    or age < 0 or max_age <= 0):
                raise ValueError("account snapshot age is invalid")
            if age > max_age:
                raise RuntimeError("account snapshot is stale")

            if stop_loss is None or take_profit is None:
                raise RuntimeError("both protective stop-loss and take-profit are required")
            if not isinstance(symbol, str) or symbol in self._active_symbols:
                raise RuntimeError("symbol already has a pending intent")

            plan = validate_order_preflight(
                symbol=symbol, side=side, quantity=quantity,
                reference_price=reference_price, symbol_info=symbol_info,
                open_positions=open_positions,
                max_open_positions=self.max_open_positions,
                max_notional_usdt=self.max_notional_usdt,
            )
            entry = _finite_positive(plan["reference_price"], "reference_price")
            sl = _finite_positive(stop_loss, "stop_loss")
            tp = _finite_positive(take_profit, "take_profit")
            if plan["side"] == "BUY" and not sl < entry < tp:
                raise ValueError("BUY stop/target must bracket entry correctly")
            if plan["side"] == "SELL" and not tp < entry < sl:
                raise ValueError("SELL stop/target must bracket entry correctly")

            intent = {
                "idempotency_key": key,
                "symbol": plan["symbol"],
                "side": plan["side"],
                "quantity": plan["quantity"],
                "reference_price": plan["reference_price"],
                "notional_usdt": plan["notional_usdt"],
                "stop_loss": format(sl, "f"),
                "take_profit": format(tp, "f"),
                "state": "PREPARED_ONLY",
                "live_order_submitted": False,
                "execution_enabled": False,
            }
            self._intents[key] = intent
            self._active_symbols.add(symbol)
            return copy.deepcopy(intent)

    def release_prepared_intent(self, idempotency_key):
        """Release only a locally prepared intent; there is no exchange action."""
        with self._lock:
            intent = self._intents.get(idempotency_key)
            if not intent:
                return False
            if intent["state"] != "PREPARED_ONLY":
                self.emergency_stop("non-prepared intent encountered during release")
                raise RuntimeError("intent state is not safe to release")
            self._active_symbols.discard(intent["symbol"])
            del self._intents[idempotency_key]
            return True

    def export_state(self):
        with self._lock:
            return {
                "schema_version": 1,
                "halted": self._halted,
                "halt_reason": self._halt_reason,
                "intents": copy.deepcopy(self._intents),
            }

    @classmethod
    def restore_state(cls, state, max_open_positions=1, max_notional_usdt=None):
        """Restore local state; halt if a prior run may have submitted an order."""
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("unsupported coordinator state")
        coordinator = cls(max_open_positions, max_notional_usdt)
        intents = state.get("intents", {})
        if not isinstance(intents, dict):
            raise ValueError("invalid coordinator intent state")
        for key, intent in intents.items():
            if not isinstance(key, str) or not isinstance(intent, dict):
                raise ValueError("invalid persisted intent")
            if intent.get("state") != "PREPARED_ONLY" or intent.get("live_order_submitted") is not False:
                coordinator.emergency_stop("restart found an ambiguous execution state")
                break
            symbol = intent.get("symbol")
            if not isinstance(symbol, str) or symbol in coordinator._active_symbols:
                coordinator.emergency_stop("restart found conflicting pending intents")
                break
            coordinator._active_symbols.add(symbol)
            coordinator._intents[key] = copy.deepcopy(intent)
        if state.get("halted"):
            coordinator.emergency_stop(state.get("halt_reason") or "persisted emergency stop")
        return coordinator
