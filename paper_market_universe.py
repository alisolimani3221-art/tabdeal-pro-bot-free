"""Multi-market signal snapshots for the paper-only trading engine.

The exchange broadcast is market-wide. This module keeps independent, gap-aware
one-minute candle builders per active USDT futures symbol, while exposing only
fresh technical snapshots to the single-position paper engine.
"""
import math
import re
import threading
import time

from market_candles import TradeCandleBuilder
from paper_trader import prepare_live_market_snapshot
from risk_analysis import analyze_klines


class PaperMarketUniverse:
    """Build fresh technical candidates for every configured active symbol."""

    def __init__(self, symbols, max_symbols=120):
        if not isinstance(symbols, (list, tuple, set)):
            raise ValueError("symbols must be a list, tuple, or set")
        if isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or max_symbols < 1:
            raise ValueError("max_symbols must be a positive integer")
        normalized = []
        seen = set()
        for value in symbols:
            if not isinstance(value, str):
                continue
            symbol = value.strip().upper()
            if not re.fullmatch(r"[A-Z0-9]+_USDT", symbol) or symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
        normalized.sort(key=lambda value: (value != "BTC_USDT", value))
        if not normalized:
            raise ValueError("no valid active USDT futures symbols")
        if len(normalized) > max_symbols:
            raise ValueError("active symbol count exceeds configured safety limit")
        self.builders = {symbol: TradeCandleBuilder(symbol) for symbol in normalized}
        self._technical = {}
        self._analyzed_candle_time = {}
        self._latest_prices = {}
        self._last_trade_at = {}
        self._lock = threading.RLock()

    @property
    def symbols(self):
        return tuple(self.builders)

    def reset_after_reconnect(self):
        """Invalidate live candidates and require each symbol to prove continuity."""
        with self._lock:
            for builder in self.builders.values():
                builder.reset_after_reconnect()
            self._technical.clear()
            self._latest_prices.clear()
            self._last_trade_at.clear()

    def add_trade(self, trade, now=None):
        """Route one validated public trade to its own builder and refresh analysis."""
        if not isinstance(trade, dict):
            return {"accepted": False, "reason": "invalid_trade"}
        symbol = trade.get("symbol")
        if not isinstance(symbol, str):
            return {"accepted": False, "reason": "invalid_symbol"}
        symbol = symbol.upper()
        if now is None:
            now = time.monotonic()
        if isinstance(now, bool):
            raise ValueError("monotonic timestamp must be finite")
        try:
            now = float(now)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("monotonic timestamp must be finite")
        if not math.isfinite(now) or now < 0:
            raise ValueError("monotonic timestamp must be finite")
        with self._lock:
            builder = self.builders.get(symbol)
            if builder is None:
                return {"accepted": False, "reason": "unsupported_symbol"}
            update = builder.add_trade(trade)
            if not update.get("accepted"):
                return update
            self._latest_prices[symbol] = float(trade["price"])
            self._last_trade_at[symbol] = now
            if update.get("gap_detected"):
                self._technical.pop(symbol, None)
                self._analyzed_candle_time.pop(symbol, None)

            new_analysis = False
            if builder.is_ready and builder.closed:
                candle_time = int(builder.closed[-1][0])
                if self._analyzed_candle_time.get(symbol) != candle_time:
                    technical = analyze_klines(builder.candles_for_analysis())
                    technical["symbol"] = symbol
                    technical["candle_open_time"] = candle_time
                    self._technical[symbol] = technical
                    self._analyzed_candle_time[symbol] = candle_time
                    new_analysis = True
            return {
                "accepted": True,
                "symbol": symbol,
                "ready": symbol in self._technical,
                "new_analysis": new_analysis,
                "gap_detected": bool(update.get("gap_detected")),
                "closed_candles": int(update.get("closed_candles", 0)),
            }

    def snapshot_for_symbol(self, symbol, now=None, max_age_seconds=30.0):
        """Return one fresh live-mark snapshot, or None when its feed is stale."""
        if now is None:
            now = time.monotonic()
        if isinstance(now, bool) or isinstance(max_age_seconds, bool):
            raise ValueError("freshness values must be finite numbers")
        try:
            now = float(now)
            max_age = float(max_age_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("freshness values must be finite numbers")
        if not math.isfinite(now) or not math.isfinite(max_age) or now < 0 or max_age <= 0:
            raise ValueError("freshness values must be finite numbers")
        with self._lock:
            symbol = str(symbol).upper()
            technical = self._technical.get(symbol)
            received_at = self._last_trade_at.get(symbol)
            price = self._latest_prices.get(symbol)
            if technical is None or received_at is None or price is None:
                return None
            if now - received_at > max_age:
                return None
            return prepare_live_market_snapshot(technical, price)

    def candidates(self, now=None, max_age_seconds=30.0):
        """Return all warmed-up symbols with a recent validated trade."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            symbols = tuple(self._technical)
        result = []
        for symbol in symbols:
            snapshot = self.snapshot_for_symbol(
                symbol, now=now, max_age_seconds=max_age_seconds
            )
            if snapshot is not None:
                result.append(snapshot)
        result.sort(key=lambda item: item["symbol"])
        return result

    def status(self):
        with self._lock:
            states = {symbol: builder.status()
                      for symbol, builder in self.builders.items()}
            return {
                "symbol_count": len(self.builders),
                "ready_count": sum(1 for symbol in self._technical
                                   if symbol in self._last_trade_at),
                "symbols": states,
            }
