"""Per-symbol candle isolation and shared one-position paper-scan coordination.

No network or order-submission code lives here. Each allowed symbol owns an
independent candle builder, last-trade clock, and technical snapshot. The
shared paper engine receives only fresh, symbol-tagged snapshots.
"""
import math
import re
import threading
import time

from market_candles import TradeCandleBuilder
from multi_timeframe import (TRADE_TIMEFRAMES_MINUTES,
                             aggregate_closed_timeframes)


_SYMBOL_RE = re.compile(r"[A-Z0-9]+_USDT")


class MultiSymbolPaperCoordinator:
    def __init__(self, symbols, analyze, prepare_snapshot,
                 builder_factory=TradeCandleBuilder, max_snapshot_age=30.0,
                 analyze_with_context=None):
        if not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= 20:
            raise ValueError("symbols must contain 1-20 USDT futures markets")
        normalized = []
        for symbol in symbols:
            if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
                raise ValueError("invalid exchange-native futures symbol")
            if symbol in normalized:
                raise ValueError("duplicate market symbol")
            normalized.append(symbol)
        if not callable(analyze) or not callable(prepare_snapshot):
            raise ValueError("analysis and snapshot functions must be callable")
        if analyze_with_context is not None and not callable(analyze_with_context):
            raise ValueError("contextual analyzer must be callable")
        if isinstance(max_snapshot_age, bool):
            raise ValueError("max_snapshot_age must be finite and positive")
        try:
            age = float(max_snapshot_age)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("max_snapshot_age must be finite and positive")
        if not math.isfinite(age) or not 1.0 <= age <= 120.0:
            raise ValueError("max_snapshot_age must be between 1 and 120 seconds")
        self.symbols = tuple(normalized)
        self.analyze = analyze
        self.analyze_with_context = analyze_with_context
        self.prepare_snapshot = prepare_snapshot
        self.max_snapshot_age = age
        self.lock = threading.RLock()
        self.states = {}
        for symbol in self.symbols:
            self.states[symbol] = {
                "builder": builder_factory(symbol),
                "last_trade_monotonic": None,
                "last_price": None,
                "latest_technical": None,
                "last_analysis_signature": None,
                "last_analysis_error": None,
                "accepted_trades": 0,
                "rejected_trades": 0,
                "pending_tick": None,
            }

    def on_trade(self, trade, received_at=None):
        """Route one already parsed/fresh trade to exactly one symbol builder."""
        if not isinstance(trade, dict) or trade.get("ok") is False:
            return {"accepted": False, "reason": "invalid_trade"}
        symbol = trade.get("symbol")
        if not isinstance(symbol, str):
            return {"accepted": False, "reason": "missing_symbol"}
        symbol = symbol.upper()
        if symbol not in self.states:
            return {"accepted": False, "reason": "symbol_not_subscribed"}
        try:
            now = time.monotonic() if received_at is None else float(received_at)
        except (TypeError, ValueError, OverflowError):
            return {"accepted": False, "reason": "invalid_receive_time"}
        if not math.isfinite(now) or now < 0:
            return {"accepted": False, "reason": "invalid_receive_time"}

        with self.lock:
            state = self.states[symbol]
            # Validate the mark before mutating the candle builder. Otherwise a
            # malformed price could be rejected here after it had already altered
            # the candle stream, contaminating later technical snapshots.
            try:
                price = float(trade["price"])
            except (KeyError, TypeError, ValueError, OverflowError):
                state["rejected_trades"] += 1
                return {"accepted": False, "symbol": symbol, "reason": "invalid_price"}
            if not math.isfinite(price) or price <= 0:
                state["rejected_trades"] += 1
                return {"accepted": False, "symbol": symbol, "reason": "invalid_price"}
            try:
                quantity = float(trade["quantity"])
                timestamp = trade["timestamp_ms"]
                sequence = trade.get("sequence")
            except (KeyError, TypeError, ValueError, OverflowError):
                state["rejected_trades"] += 1
                return {"accepted": False, "symbol": symbol, "reason": "invalid_trade_fields"}
            if (not math.isfinite(quantity) or quantity <= 0
                    or isinstance(timestamp, bool) or not isinstance(timestamp, int)
                    or timestamp <= 0 or timestamp > 10_000_000_000_000
                    or (sequence is not None and (
                        isinstance(sequence, bool) or not isinstance(sequence, int)
                        or sequence < 0))):
                state["rejected_trades"] += 1
                return {"accepted": False, "symbol": symbol, "reason": "invalid_trade_fields"}
            normalized_trade = dict(trade)
            normalized_trade["symbol"] = symbol
            update = state["builder"].add_trade(normalized_trade)
            if not update.get("accepted"):
                state["rejected_trades"] += 1
                return {"accepted": False, "symbol": symbol,
                        "reason": update.get("reason", "builder_rejected")}

            state["accepted_trades"] += 1
            state["last_trade_monotonic"] = now
            state["last_price"] = price
            # Only this exact event may reach tick-level position reconciliation.
            # The pending slot is consumed once, preventing duplicate callbacks
            # from applying the same mark/exit twice.
            state["pending_tick"] = {
                "symbol": symbol,
                "price": price,
                "quantity": quantity,
                "timestamp_ms": timestamp,
                "sequence": trade.get("sequence"),
            }
            if update.get("gap_detected"):
                # No pre-gap technical snapshot or cached indicator signature
                # may remain eligible after a continuity break.
                state["latest_technical"] = None
                state["last_analysis_signature"] = None
            if not state["builder"].is_ready:
                return {"accepted": True, "symbol": symbol,
                        "ready": False, "gap_detected": bool(update.get("gap_detected"))}
            try:
                candles = state["builder"].candles_for_analysis()
                closed = state["builder"].closed
                if not closed:
                    raise ValueError("no closed candles available for technical snapshot")
                # Indicators depend only on completed bars. Cache the expensive
                # three-timeframe calculation until at least one new timeframe
                # bar closes; still refresh the live mark on every accepted tick.
                last_closed_open = int(closed[-1][0])
                builder_interval_ms = getattr(state["builder"], "interval_ms", 60_000)
                if (isinstance(builder_interval_ms, bool)
                        or not isinstance(builder_interval_ms, int)
                        or builder_interval_ms <= 0):
                    raise ValueError("builder candle interval is invalid")
                signature = tuple(
                    (last_closed_open + builder_interval_ms)
                    // (minutes * builder_interval_ms)
                    for minutes in TRADE_TIMEFRAMES_MINUTES
                )
                if (state["latest_technical"] is not None
                        and state["last_analysis_signature"] == signature):
                    technical = dict(state["latest_technical"])
                else:
                    if self.analyze_with_context is not None:
                        technical = self.analyze_with_context(candles, timestamp)
                    else:
                        technical = self.analyze(candles)
                    if not isinstance(technical, dict):
                        raise ValueError("analysis result is not a dictionary")
                    technical = dict(technical)
                    state["last_analysis_signature"] = signature
                technical["symbol"] = symbol
                technical = self.prepare_snapshot(technical, state["last_price"])
                if not isinstance(technical, dict) or technical.get("symbol") != symbol:
                    raise ValueError("snapshot preparation changed or removed symbol identity")
                technical["candle_open_time"] = last_closed_open
                state["latest_technical"] = dict(technical)
                state["last_analysis_error"] = None
            except Exception as exc:
                state["latest_technical"] = None
                state["last_analysis_error"] = "{}: {}".format(
                    type(exc).__name__, str(exc).replace("\n", " ")[:160]
                )
                return {"accepted": True, "symbol": symbol, "ready": True,
                        "analysis_error": state["last_analysis_error"]}
            return {"accepted": True, "symbol": symbol, "ready": True,
                    "gap_detected": bool(update.get("gap_detected"))}

    def reconcile_tick(self, engine, trade, gap_detected=False):
        """Reconcile a position only from a complete, validated market event."""
        if not isinstance(trade, dict) or trade.get("ok") is not True:
            return None
        symbol = trade.get("symbol")
        if not isinstance(symbol, str):
            return None
        symbol = symbol.upper()
        if symbol not in self.states or not isinstance(gap_detected, bool):
            return None

        # This method can close a position or permanently halt the paper
        # engine. Revalidate the complete normalized event at this boundary
        # rather than trusting that every caller passed through on_trade().
        price = trade.get("price")
        quantity = trade.get("quantity")
        timestamp = trade.get("timestamp_ms")
        if isinstance(price, bool) or isinstance(quantity, bool):
            return None
        try:
            price = float(price)
            quantity = float(quantity)
        except (TypeError, ValueError, OverflowError):
            return None
        if (not math.isfinite(price) or price <= 0
                or not math.isfinite(quantity) or quantity <= 0):
            return None
        if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
                or timestamp <= 0 or timestamp > 10_000_000_000_000):
            return None
        sequence = trade.get("sequence")
        if sequence is not None and (
                isinstance(sequence, bool) or not isinstance(sequence, int)
                or sequence < 0):
            return None

        with self.lock:
            state = self.states[symbol]
            pending = state.get("pending_tick")
            if not isinstance(pending, dict):
                return None
            event_key = (symbol, price, quantity, timestamp, sequence)
            pending_key = (
                pending.get("symbol"), pending.get("price"),
                pending.get("quantity"), pending.get("timestamp_ms"),
                pending.get("sequence"),
            )
            if event_key != pending_key:
                return None
            # Consume before mutating the shared engine. A repeated callback,
            # even after an exception, cannot replay this tick.
            state["pending_tick"] = None

        if gap_detected:
            return engine.halt_for_data_gap(symbol, price)
        return engine.process_tick(symbol, price)

    def candidate_snapshots(self, now=None):
        """Return one fresh snapshot per symbol, never cross-route or reuse stale data."""
        try:
            current = time.monotonic() if now is None else float(now)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("now must be finite and nonnegative")
        if not math.isfinite(current) or current < 0:
            raise ValueError("now must be finite and nonnegative")
        with self.lock:
            candidates = []
            for symbol in self.symbols:
                state = self.states[symbol]
                snapshot = state["latest_technical"]
                received = state["last_trade_monotonic"]
                if snapshot is None or received is None:
                    continue
                age = current - received
                if 0 <= age <= self.max_snapshot_age and snapshot.get("symbol") == symbol:
                    candidates.append(dict(snapshot))
            return candidates

    def process_scan(self, engine, now=None):
        """Pass the complete fresh candidate set to the shared one-position engine."""
        return engine.process_scan(self.candidate_snapshots(now=now))

    def finalize_due(self, watermark_ms, grace_ms=30_000):
        """Finalize due candles for all subscribed markets under the shared lock."""
        if (isinstance(watermark_ms, bool) or not isinstance(watermark_ms, int)
                or watermark_ms <= 0):
            raise ValueError("watermark_ms must be a positive integer")
        if (isinstance(grace_ms, bool) or not isinstance(grace_ms, int)
                or grace_ms < 0):
            raise ValueError("grace_ms must be a nonnegative integer")
        results = {}
        with self.lock:
            for symbol, state in self.states.items():
                result = state["builder"].finalize_due(watermark_ms, grace_ms)
                results[symbol] = result
                if result.get("finalized"):
                    # Old technical snapshots no longer describe a forming candle.
                    # A fresh symbol trade must rebuild them before scan eligibility.
                    state["latest_technical"] = None
                    state["last_analysis_signature"] = None
                    state["pending_tick"] = None
        return results

    def reset_after_reconnect(self):
        """Reset each builder independently and invalidate all prior fresh snapshots."""
        with self.lock:
            for state in self.states.values():
                state["builder"].reset_after_reconnect()
                state["last_trade_monotonic"] = None
                state["last_price"] = None
                state["latest_technical"] = None
                state["last_analysis_signature"] = None
                state["pending_tick"] = None

    def status(self, now=None):
        try:
            current = time.monotonic() if now is None else float(now)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("now must be finite and nonnegative")
        if not math.isfinite(current) or current < 0:
            raise ValueError("now must be finite and nonnegative")
        with self.lock:
            result = {}
            for symbol in self.symbols:
                state = self.states[symbol]
                builder_status = state["builder"].status()
                received = state["last_trade_monotonic"]
                age = None if received is None else current - received
                snapshot = state["latest_technical"]
                timeframe = (
                    snapshot.get("timeframe_analysis")
                    if isinstance(snapshot, dict) else None
                )
                timeframe = timeframe if isinstance(timeframe, dict) else {}
                # Warm-up progress must come from the builder itself, not
                # from latest_technical. The latter is intentionally absent
                # until the one-minute builder is ready, so deriving progress
                # from it made a partially warmed stream misleadingly report
                # 0/23, 0/2 and 0/1.
                closed_rows = list(getattr(state["builder"], "closed", ()))
                interval_ms = getattr(state["builder"], "interval_ms", 60_000)
                timeframe_counts = {
                    str(minutes): 0 for minutes in TRADE_TIMEFRAMES_MINUTES
                }
                timeframe_counts["1"] = len(closed_rows)
                if closed_rows:
                    try:
                        last_open_time = int(closed_rows[-1][0])
                        if (isinstance(interval_ms, bool)
                                or not isinstance(interval_ms, int)
                                or interval_ms <= 0):
                            raise ValueError("invalid candle interval")
                        aggregated = aggregate_closed_timeframes(
                            closed_rows, last_open_time + interval_ms
                        )
                        timeframe_counts["5"] = len(aggregated[5])
                        timeframe_counts["15"] = len(aggregated[15])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        # Bad provenance never increases readiness. The raw
                        # one-minute count remains visible for diagnosis while
                        # higher-timeframe counts fail closed at zero.
                        timeframe_counts["5"] = 0
                        timeframe_counts["15"] = 0
                snapshot_fresh = bool(
                    snapshot is not None and age is not None
                    and 0 <= age <= self.max_snapshot_age
                )
                timeframe_ready = timeframe.get("ready") is True
                timeframe_agreement = timeframe.get("agreement") is True
                timeframe_entry_trigger = timeframe.get("entry_trigger") is True
                result[symbol] = {
                    "accepted_trades": state["accepted_trades"],
                    "rejected_trades": state["rejected_trades"],
                    "last_trade_age_seconds": age,
                    "snapshot_fresh": snapshot_fresh,
                    "multi_timeframe_ready": timeframe_ready,
                    "multi_timeframe_agreement": timeframe_agreement,
                    "one_minute_entry_trigger": timeframe_entry_trigger,
                    "timeframe_closed_candles": timeframe_counts,
                    "entry_ready": bool(
                        snapshot_fresh and timeframe_ready and timeframe_agreement
                        and timeframe_entry_trigger
                    ),
                    "last_analysis_error": state["last_analysis_error"],
                    "builder": builder_status,
                }
            return result
