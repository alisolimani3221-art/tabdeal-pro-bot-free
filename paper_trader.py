"""Conservative paper-trading engine. It never sends exchange orders."""
import math


def _positive(value, name):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("{} must be positive and finite".format(name))
    return number


def rank_signal(technical):
    """Return a deterministic quality score, or None when there is no setup."""
    if not isinstance(technical, dict):
        return None
    signal = technical.get("signal")
    if signal not in ("BULLISH", "BEARISH"):
        return None
    symbol = str(technical.get("symbol", "")).strip()
    if not symbol:
        return None
    try:
        price = _positive(technical["price"], "price")
        sma20 = _positive(technical["sma20"], "sma20")
        sma50 = _positive(technical["sma50"], "sma50")
        rsi = float(technical["rsi14"])
        atr = _positive(technical["atr14"], "atr14")
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(rsi) or not 0 <= rsi <= 100:
        return None
    volatility_pct = atr / price * 100.0
    if volatility_pct > 2.5:
        return None
    if signal == "BULLISH" and not (price > sma20 > sma50 and 50 <= rsi < 68):
        return None
    if signal == "BEARISH" and not (price < sma20 < sma50 and 32 < rsi <= 50):
        return None
    trend_strength = abs(sma20 - sma50) / price * 100.0
    momentum_strength = abs(rsi - 50.0) / 18.0
    score = trend_strength * 10.0 + momentum_strength - volatility_pct * 0.5
    return {"symbol": symbol, "side": signal,
            "price": price, "atr": atr, "rsi": rsi,
            "volatility_pct": volatility_pct, "score": score}


class PaperTradingEngine:
    """One-position paper trader with fixed fractional risk and no live orders."""

    def __init__(self, capital=8.0, risk_percent=0.5, max_leverage=3,
                 fee_rate=0.0005, slippage_rate=0.0002):
        self.initial_capital = _positive(capital, "capital")
        self.cash = self.initial_capital
        self.risk_percent = float(risk_percent)
        if not 0 < self.risk_percent <= 0.5:
            raise ValueError("paper risk must be above 0 and at most 0.5 percent")
        self.max_leverage = int(max_leverage)
        if not 1 <= self.max_leverage <= 3:
            raise ValueError("max leverage must be between 1 and 3")
        self.fee_rate = _positive(fee_rate, "fee_rate")
        self.slippage_rate = float(slippage_rate)
        if not 0 <= self.slippage_rate <= 0.01:
            raise ValueError("invalid slippage")
        self.position = None
        self.closed_trades = []
        self.scans = 0

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
        """Update the open position and optionally enter the best valid setup."""
        self.scans += 1
        events = []
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
            hit_stop = current <= p["stop"] if p["side"] == "BULLISH" else current >= p["stop"]
            hit_target = current >= p["target"] if p["side"] == "BULLISH" else current <= p["target"]
            if hit_stop:
                events.append(self._close(p["stop"], "STOP"))
                closed_this_scan = True
            elif hit_target:
                events.append(self._close(p["target"], "TARGET"))
                closed_this_scan = True

        # Never close and reopen on the same scan: that can create an
        # immediate churn loop on a signal that has not changed.
        if (not closed_this_scan and self.position is None and valid and self.cash > 0
                and self.cash > self.initial_capital * 0.97):
            choice = valid[0]
            entry = choice["price"] * (1 + self.slippage_rate if choice["side"] == "BULLISH"
                                       else 1 - self.slippage_rate)
            stop_distance = 1.5 * choice["atr"]
            risk_cash = self.cash * self.risk_percent / 100.0
            quantity = risk_cash / stop_distance
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
                if fee < self.cash:
                    self.cash -= fee
                    self.position = {
                        "symbol": choice["symbol"], "side": choice["side"],
                        "entry": entry, "last_price": choice["price"],
                        "stop": stop, "target": target, "quantity": quantity,
                        "notional": notional, "leverage": leverage,
                        "risk_budget": risk_cash, "fees_entry": fee,
                        "score": choice["score"],
                    }
                    events.append({"event": "OPENED", "position": dict(self.position)})
        return {"events": events, "candidates": valid,
                "equity": self.equity, "cash": self.cash,
                "open_position": dict(self.position) if self.position else None,
                "closed_trades": len(self.closed_trades)}
