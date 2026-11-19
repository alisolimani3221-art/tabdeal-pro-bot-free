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
        if max_notional_usdt is None:
            self.max_notional_usdt = None
        else:
            self.max_notional_usdt = format(
                _finite_positive(max_notional_usdt, "max_notional_usdt"), "f"
            )
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
            # Pending local intents reserve capacity just like open positions.
            # Count the validated exchange snapshot plus our own reservations.
            active_position_count = 0
            for position in open_positions:
                amount = position.get("positionAmt", position.get("amount"))
                if Decimal(str(amount)) != 0:
                    active_position_count += 1
            if active_position_count + len(self._intents) >= self.max_open_positions:
                raise RuntimeError("open position limit reached including pending intents")
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
        required_state_keys = {"schema_version", "halted", "halt_reason", "intents"}
        if (
            not isinstance(state, dict)
            or set(state) != required_state_keys
            or type(state.get("schema_version")) is not int
            or state.get("schema_version") != 1
        ):
            raise ValueError("unsupported coordinator state (incomplete or invalid)")
        halted = state.get("halted")
        halt_reason = state.get("halt_reason")
        intents = state.get("intents")
        if (
            type(halted) is not bool
            or not isinstance(intents, dict)
            or (halted and (not isinstance(halt_reason, str) or not halt_reason.strip()))
            or (not halted and halt_reason is not None)
        ):
            raise ValueError("invalid coordinator recovery metadata")
        coordinator = cls(max_open_positions, max_notional_usdt)
        for key, intent in intents.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(intent, dict):
                coordinator.emergency_stop("restart found malformed persisted intent")
                break
            required = {
                "idempotency_key", "symbol", "side", "quantity",
                "reference_price", "notional_usdt", "stop_loss",
                "take_profit", "state", "live_order_submitted",
                "execution_enabled",
            }
            if (
                set(intent) != required
                or intent.get("idempotency_key") != key
                or intent.get("state") != "PREPARED_ONLY"
                or intent.get("live_order_submitted") is not False
                or intent.get("execution_enabled") is not False
            ):
                coordinator.emergency_stop("restart found an ambiguous execution state")
                break
            symbol = intent.get("symbol")
            side = intent.get("side")
            if (
                not isinstance(symbol, str) or not symbol.strip()
                or side not in ("BUY", "SELL")
                or symbol in coordinator._active_symbols
                or len(coordinator._intents) >= coordinator.max_open_positions
            ):
                coordinator.emergency_stop("restart found conflicting pending intents")
                break
            try:
                quantity = _finite_positive(intent.get("quantity"), "quantity")
                entry = _finite_positive(intent.get("reference_price"), "reference_price")
                notional = _finite_positive(intent.get("notional_usdt"), "notional_usdt")
                sl = _finite_positive(intent.get("stop_loss"), "stop_loss")
                tp = _finite_positive(intent.get("take_profit"), "take_profit")
                if notional != quantity * entry:
                    raise ValueError("persisted notional mismatch")
                if coordinator.max_notional_usdt is not None and notional > Decimal(coordinator.max_notional_usdt):
                    raise ValueError("persisted notional exceeds configured cap")
                if side == "BUY" and not sl < entry < tp:
                    raise ValueError("persisted BUY protection is invalid")
                if side == "SELL" and not tp < entry < sl:
                    raise ValueError("persisted SELL protection is invalid")
            except (ValueError, TypeError):
                coordinator.emergency_stop("restart found invalid persisted intent values")
                break
            coordinator._active_symbols.add(symbol)
            coordinator._intents[key] = copy.deepcopy(intent)
        if state.get("halted"):
            coordinator.emergency_stop(state.get("halt_reason") or "persisted emergency stop")
        return coordinator
