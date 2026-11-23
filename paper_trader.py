"""Conservative paper-trading engine. It never sends exchange orders."""
import math
import re
import threading


PAPER_SCAN_INTERVAL_SECONDS = 5.0


def paper_scan_due(now, last_scan):
    """Return whether a fresh paper scan is due on the monotonic clock."""
    if isinstance(now, bool) or isinstance(last_scan, bool):
        raise ValueError("scan timestamps must be finite numbers")
    try:
        current = float(now)
        previous = float(last_scan)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("scan timestamps must be finite numbers")
    if (not math.isfinite(current) or not math.isfinite(previous)
            or current < 0 or previous < 0):
        raise ValueError("scan timestamps must be finite nonnegative numbers")
    # A zero last-scan value means the first available fresh snapshot is due.
    return previous == 0.0 or current - previous >= PAPER_SCAN_INTERVAL_SECONDS


def _positive(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError("{} must be a positive finite number".format(name))
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("{} must be positive and finite".format(name))
    return number


def prepare_live_market_snapshot(technical, live_price):
    """Use the latest validated trade as the live mark/entry, not a stale close.

    Indicators and signal remain derived from closed candles. The old candle's
    OHLC range is cleared because its extremes may have occurred before entry;
    tick-level stop/target checks are handled separately by process_tick().
    """
    if not isinstance(technical, dict):
        raise ValueError("technical snapshot must be a dictionary")
    price = _positive(live_price, "live price")
    snapshot = dict(technical)
    snapshot["price"] = price
    snapshot["candle_open"] = price
    snapshot["candle_high"] = price
    snapshot["candle_low"] = price
    return snapshot


def rank_signal(technical):
    """Return a deterministic quality score, or None when there is no setup."""
    if not isinstance(technical, dict):
        return None
    signal = technical.get("signal")
    if signal not in ("BULLISH", "BEARISH"):
        return None
    # A setup is eligible only when it carries the minimum closed-candle
    # provenance produced by analyze_klines. Hand-built/stale indicator
    # dictionaries must not be enough to open even a paper position.
    closed_candles = technical.get("closed_candles")
    if type(closed_candles) is not int or closed_candles < 20:
        return None
    candle_open_time = technical.get("candle_open_time")
    if (isinstance(candle_open_time, bool) or
            not isinstance(candle_open_time, int) or candle_open_time <= 0):
        return None
    symbol = str(technical.get("symbol", "")).strip()
    normalized_symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9]+_?USDT", normalized_symbol):
        return None
    try:
        price = _positive(technical["price"], "price")
        sma10 = _positive(technical["sma10"], "sma10")
        sma20 = _positive(technical["sma20"], "sma20")
        if isinstance(technical["rsi14"], bool):
            return None
        rsi = float(technical["rsi14"])
        atr = _positive(technical["atr14"], "atr14")
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(rsi) or not 0 <= rsi <= 100:
        return None
    volatility_pct = atr / price * 100.0
    if not math.isfinite(volatility_pct) or volatility_pct > 2.0:
        return None
    if signal == "BULLISH" and not (price > sma10 > sma20 and 50 <= rsi < 68):
        return None
    if signal == "BEARISH" and not (price < sma10 < sma20 and 32 < rsi <= 50):
        return None
    trend_strength = abs(sma10 - sma20) / price * 100.0
    momentum_strength = abs(rsi - 50.0) / 18.0
    score = trend_strength * 10.0 + momentum_strength - volatility_pct * 0.5
    if not math.isfinite(score):
        return None
    return {"symbol": normalized_symbol, "side": signal,
            "price": price, "atr": atr, "rsi": rsi,
            "candle_open_time": candle_open_time,
            "volatility_pct": volatility_pct, "score": score}


def diagnose_signal(technical):
    """Explain why a technical candidate is or is not eligible for paper entry."""
    if not isinstance(technical, dict):
        return "داده تحلیل نامعتبر است"
    signal = technical.get("signal")
    if signal == "NEUTRAL":
        return "سیگنال خنثی است؛ روند و مومنتوم هم‌جهت نیستند"
    if signal not in ("BULLISH", "BEARISH"):
        return "جهت سیگنال قابل‌شناسایی نیست"
    count = technical.get("closed_candles")
    if type(count) is not int or count < 20:
        return "داده کافی نیست: حداقل ۲۰ کندل بسته‌شده لازم است"
    candle_open_time = technical.get("candle_open_time")
    if (isinstance(candle_open_time, bool) or
            not isinstance(candle_open_time, int) or candle_open_time <= 0):
        return "زمان بازشدن کندل معتبر نیست؛ ورود مسدود شد"
    symbol = str(technical.get("symbol", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+_?USDT", symbol):
        return "نماد معاملاتی نامعتبر است"
    try:
        price = _positive(technical["price"], "price")
        sma10 = _positive(technical["sma10"], "sma10")
        sma20 = _positive(technical["sma20"], "sma20")
        rsi = float(technical["rsi14"])
        atr = _positive(technical["atr14"], "atr14")
    except (KeyError, TypeError, ValueError, OverflowError):
        return "یکی از شاخص‌های قیمت، میانگین، RSI یا ATR نامعتبر است"
    if not math.isfinite(rsi) or not 0 <= rsi <= 100:
        return "مقدار RSI نامعتبر است"
    volatility = atr / price * 100.0
    if not math.isfinite(volatility) or volatility > 2.0:
        return "نوسان بیش از حد مجاز ۲٪ است"
    if signal == "BULLISH":
        if not price > sma10 > sma20:
            return "ورود خرید رد شد: قیمت باید بالاتر از SMA10 و SMA10 بالاتر از SMA20 باشد"
        if not 50 <= rsi < 68:
            return "ورود خرید رد شد: RSI باید از ۵۰ تا کمتر از ۶۸ باشد"
    else:
        if not price < sma10 < sma20:
            return "ورود فروش رد شد: قیمت باید پایین‌تر از SMA10 و SMA10 پایین‌تر از SMA20 باشد"
        if not 32 < rsi <= 50:
            return "ورود فروش رد شد: RSI باید بیشتر از ۳۲ و حداکثر ۵۰ باشد"
    return "شرایط سیگنال تأیید شده؛ ورود آزمایشی مجاز است"


class PaperTradingEngine:
    """One-position paper trader with fixed fractional risk and no live orders."""

    def __init__(self, capital=8.0, risk_percent=0.5, max_leverage=3,
                 fee_rate=0.0005, slippage_rate=0.0002,
                 max_loss_percent=3.0):
        self.initial_capital = _positive(capital, "capital")
        self.cash = self.initial_capital
        self.risk_percent = float(risk_percent)
        if not 0 < self.risk_percent <= 0.5:
            raise ValueError("paper risk must be above 0 and at most 0.5 percent")
        leverage_value = _positive(max_leverage, "max_leverage")
        if not leverage_value.is_integer() or not 1 <= leverage_value <= 3:
            raise ValueError("max leverage must be an integer between 1 and 3")
        self.max_leverage = int(leverage_value)
        self.fee_rate = _positive(fee_rate, "fee_rate")
        if self.fee_rate > 0.05:
            raise ValueError("fee rate is unrealistically high; maximum is 5 percent")
        self.slippage_rate = float(slippage_rate)
        if not 0 <= self.slippage_rate <= 0.01:
            raise ValueError("invalid slippage")
        self.max_loss_percent = _positive(max_loss_percent, "max_loss_percent")
        if self.max_loss_percent > 25:
            raise ValueError("max loss must be above 0 and at most 25 percent")
        self.position = None
        # Persist the last candle on which an entry was opened. Repeated
        # 15-second scans must not reopen after a stop/target within that candle.
        self.last_entry_candle = None
        self.closed_trades = []
        self.scans = 0
        self._lock = threading.RLock()
        self.halted = False
        self.halt_reason = None

    def export_state(self):
        """Return a JSON-safe snapshot suitable for atomic local persistence."""
        with self._lock:
            return {
                "schema_version": 2,
                "initial_capital": self.initial_capital,
                "cash": self.cash,
                "risk_percent": self.risk_percent,
                "max_leverage": self.max_leverage,
                "fee_rate": self.fee_rate,
                "slippage_rate": self.slippage_rate,
                "max_loss_percent": self.max_loss_percent,
                "position": dict(self.position) if self.position else None,
                "last_entry_candle": (dict(self.last_entry_candle)
                                      if self.last_entry_candle else None),
                "closed_trades": [dict(item) for item in self.closed_trades],
                "scans": self.scans,
                "halted": self.halted,
                "halt_reason": self.halt_reason,
            }

    @classmethod
    def restore_state(cls, state):
        """Restore a strictly validated snapshot; malformed state fails closed."""
        common = {
            "schema_version", "initial_capital", "cash", "risk_percent",
            "max_leverage", "fee_rate", "slippage_rate", "max_loss_percent",
            "position", "closed_trades", "scans", "halted", "halt_reason",
        }
        if not isinstance(state, dict) or type(state.get("schema_version")) is not int:
            raise ValueError("unsupported or malformed paper-engine state")
        version = state["schema_version"]
        expected = common if version == 1 else common | {"last_entry_candle"}
        if version not in (1, 2) or set(state) != expected:
            raise ValueError("unsupported or malformed paper-engine state")

        engine = cls(
            capital=state["initial_capital"],
            risk_percent=state["risk_percent"],
            max_leverage=state["max_leverage"],
            fee_rate=state["fee_rate"],
            slippage_rate=state["slippage_rate"],
            max_loss_percent=state["max_loss_percent"],
        )

        def finite(value, name, allow_zero=False, allow_negative=False):
            if isinstance(value, bool):
                raise ValueError("{} is invalid".format(name))
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("{} is invalid".format(name))
            if not math.isfinite(number):
                raise ValueError("{} is invalid".format(name))
            if not allow_negative and (number < 0 if allow_zero else number <= 0):
                raise ValueError("{} is invalid".format(name))
            return number

        engine.cash = finite(state["cash"], "cash", allow_zero=True)
        scans = state["scans"]
        if type(scans) is not int or scans < 0:
            raise ValueError("paper-engine scan count is invalid")
        engine.scans = scans
        if type(state["halted"]) is not bool:
            raise ValueError("paper-engine halt flag is invalid")
        engine.halted = state["halted"]
        reason = state["halt_reason"]
        if ((engine.halted and (not isinstance(reason, str) or not reason.strip()))
                or (not engine.halted and reason is not None)):
            raise ValueError("paper-engine halt metadata is inconsistent")
        engine.halt_reason = reason

        last_entry = state.get("last_entry_candle")
        if last_entry is not None:
            if (not isinstance(last_entry, dict)
                    or set(last_entry) != {"symbol", "open_time"}
                    or not isinstance(last_entry["symbol"], str)
                    or not re.fullmatch(r"[A-Z0-9]+_?USDT", last_entry["symbol"])
                    or isinstance(last_entry["open_time"], bool)
                    or not isinstance(last_entry["open_time"], int)
                    or last_entry["open_time"] <= 0):
                raise ValueError("paper-engine last-entry candle is invalid")
            engine.last_entry_candle = dict(last_entry)

        position = state["position"]
        if position is not None:
            required = {
                "symbol", "side", "entry", "last_price", "stop", "target",
                "quantity", "notional", "leverage", "risk_budget",
                "risk_limit", "fees_entry", "score",
            }
            if not isinstance(position, dict) or set(position) != required:
                raise ValueError("paper-engine position is malformed")
            if (not isinstance(position["symbol"], str)
                    or not re.fullmatch(r"[A-Z0-9]+_?USDT", position["symbol"])
                    or position["side"] not in ("BULLISH", "BEARISH")):
                raise ValueError("paper-engine position identity is invalid")
            for name in ("entry", "last_price", "stop", "target", "quantity",
                         "notional", "risk_budget", "risk_limit", "fees_entry"):
                finite(position[name], "position " + name,
                       allow_zero=(name in ("risk_budget", "risk_limit", "fees_entry")))
            finite(position["score"], "position score", allow_negative=True)
            leverage = position["leverage"]
            if type(leverage) is not int or not 1 <= leverage <= engine.max_leverage:
                raise ValueError("paper-engine position leverage is invalid")
            # Validate relationships between persisted financial fields, not
            # only that each number is individually finite and positive.
            expected_notional = position["entry"] * position["quantity"]
            expected_entry_fee = expected_notional * engine.fee_rate
            if not math.isclose(position["notional"], expected_notional,
                                rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("paper-engine position notional is inconsistent")
            if not math.isclose(position["fees_entry"], expected_entry_fee,
                                rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("paper-engine entry fee is inconsistent")
            tolerance = lambda value: max(1e-12, abs(value) * 1e-9)
            if position["risk_budget"] > position["risk_limit"] + tolerance(position["risk_limit"]):
                raise ValueError("paper-engine position exceeds its risk limit")
            # Recompute the configured fractional-risk budget from pre-entry
            # cash (entry fee was deducted immediately after sizing).
            expected_risk_limit = (
                engine.cash + position["fees_entry"]
            ) * engine.risk_percent / 100.0
            if not math.isclose(position["risk_limit"], expected_risk_limit,
                                rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("paper-engine risk limit is inconsistent")
            stop_distance = abs(position["entry"] - position["stop"])
            if position["side"] == "BULLISH":
                stop_fill = (position["entry"] - stop_distance) * (1 - engine.slippage_rate)
            else:
                stop_fill = (position["entry"] + stop_distance) * (1 + engine.slippage_rate)
            expected_risk_budget = position["quantity"] * (
                abs(position["entry"] - stop_fill)
                + (position["entry"] + stop_fill) * engine.fee_rate
            )
            if not math.isclose(position["risk_budget"], expected_risk_budget,
                                rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("paper-engine risk budget is inconsistent")
            if position["notional"] / leverage > engine.cash + tolerance(engine.cash):
                raise ValueError("paper-engine restored margin exceeds available cash")
            if position["notional"] / leverage > (
                    engine.cash + position["fees_entry"]
            ) * 0.25 + tolerance(engine.cash):
                raise ValueError("paper-engine restored margin exceeds its 25 percent cap")
            if position["side"] == "BULLISH":
                if not position["stop"] < position["entry"] < position["target"]:
                    raise ValueError("paper-engine long protection is invalid")
            elif not position["target"] < position["entry"] < position["stop"]:
                raise ValueError("paper-engine short protection is invalid")
            engine.position = dict(position)

        trades = state["closed_trades"]
        if not isinstance(trades, list) or len(trades) > 10000:
            raise ValueError("paper-engine trade history is invalid")
        for trade in trades:
            if not isinstance(trade, dict):
                raise ValueError("paper-engine trade record is invalid")
            for name in ("entry", "exit", "quantity", "net_pnl"):
                if name not in trade:
                    raise ValueError("paper-engine trade record is incomplete")
                finite(trade[name], "trade " + name, allow_zero=(name == "net_pnl"), allow_negative=(name == "net_pnl"))
            if (trade.get("side") not in ("BULLISH", "BEARISH")
                    or not isinstance(trade.get("symbol"), str)
                    or not isinstance(trade.get("reason"), str)):
                raise ValueError("paper-engine trade metadata is invalid")
        engine.closed_trades = [dict(item) for item in trades]
        if engine.halted and engine.position is not None:
            raise ValueError("halted paper engine cannot retain an open position")
        return engine

    @property
    def equity(self):
        if not self.position:
            return self.cash
        p = self.position
        mark = p["last_price"]
        raw = (mark - p["entry"]) * p["quantity"]
        if p["side"] == "BEARISH":
            raw = -raw
        exit_fee = mark * p["quantity"] * self.fee_rate
        return self.cash + raw - exit_fee

    def _close(self, price, reason):
        p = self.position
        exit_price = price * (1 - self.slippage_rate if p["side"] == "BULLISH"
                              else 1 + self.slippage_rate)
        gross = ((exit_price - p["entry"]) * p["quantity"] *
                 (1 if p["side"] == "BULLISH" else -1))
        exit_fee = exit_price * p["quantity"] * self.fee_rate
        net = gross - exit_fee
        self.cash += net
        trade = dict(p)
        trade.update({"exit": exit_price, "gross_pnl": gross,
                      "fees_exit": exit_fee,
                      "net_pnl": gross - p["fees_entry"] - exit_fee,
                      "reason": reason})
        self.closed_trades.append(trade)
        self.position = None
        return {"event": "CLOSED", "trade": trade}

    def process_scan(self, candidates):
        """Thread-safe scan; serializes against the emergency stop."""
        with self._lock:
            return self._process_scan(candidates)

    def emergency_close(self):
        """Close any simulated position at its latest mark and permanently stop this engine."""
        with self._lock:
            self.halted = True
            self.halt_reason = "EMERGENCY_STOP"
            if self.position is None:
                return {"event": "STOPPED", "trade": None}
            return self._close(self.position["last_price"], "EMERGENCY_STOP")

    def process_tick(self, symbol, price):
        """Reconcile an open paper position against each validated live trade tick."""
        with self._lock:
            if self.halted or self.position is None:
                return None
            if not isinstance(symbol, str) or symbol.upper() != self.position["symbol"]:
                return None
            latest = _positive(price, "tick price")
            p = self.position
            p["last_price"] = latest
            if p["side"] == "BULLISH":
                if latest <= p["stop"]:
                    event = self._close(min(p["stop"], latest), "STOP_TICK")
                elif latest >= p["target"]:
                    event = self._close(p["target"], "TARGET_TICK")
                else:
                    return None
            else:
                if latest >= p["stop"]:
                    event = self._close(max(p["stop"], latest), "STOP_TICK")
                elif latest <= p["target"]:
                    event = self._close(p["target"], "TARGET_TICK")
                else:
                    return None

            loss_floor = self.initial_capital * (1.0 - self.max_loss_percent / 100.0)
            if self.cash <= loss_floor:
                self.halted = True
                self.halt_reason = "MAX_LOSS"
            return {
                "event": event["event"],
                "trade": event["trade"],
                "halted": self.halted,
                "halt_reason": self.halt_reason,
                "cash": self.cash,
                "equity": self.equity,
            }

    def _process_scan(self, candidates):
        """Update the open position and optionally enter the best valid setup."""
        if self.halted:
            return {"events": [], "candidates": [], "equity": self.equity,
                    "cash": self.cash, "open_position": None,
                    "closed_trades": len(self.closed_trades), "halted": True,
                    "halt_reason": self.halt_reason}
        self.scans += 1
        events = []
        if not isinstance(candidates, (list, tuple)):
            candidates = []
        valid = [x for x in (rank_signal(c) for c in candidates) if x]
        valid.sort(key=lambda x: x["score"], reverse=True)

        closed_this_scan = False
        if self.position:
            p = self.position
            # Mark the open position using its latest candle close even if its
            # current technical signal has turned neutral or reversed.
            mark = next((c for c in candidates
                         if isinstance(c, dict) and c.get("symbol") == p["symbol"]), None)
            if mark:
                try:
                    latest = _positive(mark.get("price"), "mark price")
                    p["last_price"] = latest
                except (TypeError, ValueError, OverflowError):
                    pass
            current = p["last_price"]
            # Use the full closed candle range, not only its close. If both
            # stop and target were touched in one candle, assume the stop was
            # hit first because OHLC data cannot reveal the intrabar path.
            bar = mark if isinstance(mark, dict) else {}
            try:
                bar_open = _positive(bar.get("candle_open", current), "candle open")
                bar_high = _positive(bar.get("candle_high", current), "candle high")
                bar_low = _positive(bar.get("candle_low", current), "candle low")
                if not bar_low <= min(bar_open, current) <= max(bar_open, current) <= bar_high:
                    raise ValueError("inconsistent closed candle range")
            except (TypeError, ValueError, OverflowError):
                bar_open = bar_high = bar_low = current
            if p["side"] == "BULLISH":
                hit_stop = bar_low <= p["stop"]
                hit_target = bar_high >= p["target"]
                stop_fill = min(p["stop"], bar_open)
            else:
                hit_stop = bar_high >= p["stop"]
                hit_target = bar_low <= p["target"]
                stop_fill = max(p["stop"], bar_open)
            if hit_stop:
                events.append(self._close(stop_fill, "STOP"))
                closed_this_scan = True
            elif hit_target:
                events.append(self._close(p["target"], "TARGET"))
                closed_this_scan = True

        # Enforce a hard cumulative-loss stop using marked equity, including
        # estimated exit fees. If breached, flatten the paper position and
        # permanently halt this engine instead of merely suppressing entries.
        loss_floor = self.initial_capital * (1.0 - self.max_loss_percent / 100.0)
        if self.equity <= loss_floor:
            if self.position is not None:
                events.append(self._close(self.position["last_price"], "MAX_LOSS"))
            self.halted = True
            self.halt_reason = "MAX_LOSS"
            closed_this_scan = True

        # Never close and reopen on the same scan: that can create an
        # immediate churn loop on a signal that has not changed.
        if (not self.halted and not closed_this_scan and self.position is None
                and valid and self.cash > 0 and self.cash > loss_floor):
            choice = next((candidate for candidate in valid
                           if self.last_entry_candle != {
                               "symbol": candidate["symbol"],
                               "open_time": candidate["candle_open_time"],
                           }), None)
            if choice is None:
                return {"events": events, "candidates": valid,
                        "equity": self.equity, "cash": self.cash,
                        "open_position": None, "closed_trades": len(self.closed_trades),
                        "halted": self.halted, "halt_reason": self.halt_reason}
            entry = choice["price"] * (1 + self.slippage_rate if choice["side"] == "BULLISH"
                                       else 1 - self.slippage_rate)
            stop_distance = 1.5 * choice["atr"]
            risk_cash = self.cash * self.risk_percent / 100.0
            # Size for the full estimated stop loss, including entry/exit
            # slippage and both-side fees, rather than price distance alone.
            stop_fill = (entry - stop_distance) * (1 - self.slippage_rate) if choice["side"] == "BULLISH" else (entry + stop_distance) * (1 + self.slippage_rate)
            risk_per_unit = abs(entry - stop_fill) + (entry + stop_fill) * self.fee_rate
            if risk_per_unit <= 0 or not math.isfinite(risk_per_unit):
                return {"events": events, "candidates": valid,
                        "equity": self.equity, "cash": self.cash,
                        "open_position": None, "closed_trades": len(self.closed_trades)}
            quantity = risk_cash / risk_per_unit
            leverage = 1
            if choice["volatility_pct"] <= 1.0:
                leverage = min(3, self.max_leverage)
            elif choice["volatility_pct"] <= 2.0:
                leverage = min(2, self.max_leverage)
            notional = quantity * entry
            max_margin = self.cash * 0.25
            if notional / leverage > max_margin:
                quantity = max_margin * leverage / entry
                notional = quantity * entry
            if quantity > 0 and notional > 0 and notional / leverage <= self.cash:
                stop = entry - stop_distance if choice["side"] == "BULLISH" else entry + stop_distance
                target = entry + 3 * choice["atr"] if choice["side"] == "BULLISH" else entry - 3 * choice["atr"]
                fee = notional * self.fee_rate
                if fee < self.cash and notional / leverage <= self.cash - fee:
                    self.cash -= fee
                    self.position = {
                        "symbol": choice["symbol"], "side": choice["side"],
                        "entry": entry, "last_price": choice["price"],
                        "stop": stop, "target": target, "quantity": quantity,
                        "notional": notional, "leverage": leverage,
                        "risk_budget": quantity * risk_per_unit,
                        "risk_limit": risk_cash, "fees_entry": fee,
                        "score": choice["score"],
                    }
                    self.last_entry_candle = {
                        "symbol": choice["symbol"],
                        "open_time": choice["candle_open_time"],
                    }
                    events.append({"event": "OPENED", "position": dict(self.position)})
        return {"events": events, "candidates": valid,
                "equity": self.equity, "cash": self.cash,
                "open_position": dict(self.position) if self.position else None,
                "closed_trades": len(self.closed_trades),
                "halted": self.halted, "halt_reason": self.halt_reason}
