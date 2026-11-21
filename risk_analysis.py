"""Read-only futures indicators and conservative position-risk calculations."""

import math


CANDLE_INTERVAL_MS = 60_000


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError("{} must be a finite number".format(name))
    raw = str(value).strip()
    raw = raw.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))
    raw = raw.replace("٬", "").replace(",", "").replace("،", "").replace("٫", ".")
    number = float(raw)
    if not math.isfinite(number):
        raise ValueError("{} must be a finite number".format(name))
    return number


def analyze_klines(candles):
    """Calculate SMA10/SMA20, Wilder RSI14 and Wilder ATR14.

    The final candle is excluded because it may still be forming. Input uses
    Binance-compatible [open_time, open, high, low, close, volume, ...] rows.
    """
    if not isinstance(candles, (list, tuple)):
        raise ValueError("candles must be a list")
    closed = list(candles[:-1])
    if len(closed) < 20:
        raise ValueError("at least 21 candles are required")

    opens, highs, lows, closes, timestamps = [], [], [], [], []
    for candle in closed:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            raise ValueError("invalid candle format")
        timestamp = _finite(candle[0], "open time")
        volume = _finite(candle[5], "volume")
        if timestamp < 0 or volume < 0:
            raise ValueError("candle timestamp and volume must be nonnegative")
        open_price = _finite(candle[1], "open")
        high = _finite(candle[2], "high")
        low = _finite(candle[3], "low")
        close = _finite(candle[4], "close")
        if min(open_price, high, low, close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError("inconsistent OHLC range")
        timestamps.append(timestamp)
        opens.append(open_price)
        highs.append(high)
        lows.append(low)
        closes.append(close)

    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("candle timestamps must be strictly increasing")
    if any(current - previous != CANDLE_INTERVAL_MS
           for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("one-minute candle history must be gap-free")

    sma10 = sum(closes[-10:]) / 10.0
    sma20 = sum(closes[-20:]) / 20.0

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    initial_gains = [max(change, 0.0) for change in changes[:14]]
    initial_losses = [max(-change, 0.0) for change in changes[:14]]
    avg_gain = sum(initial_gains) / 14.0
    avg_loss = sum(initial_losses) / 14.0
    for change in changes[14:]:
        avg_gain = (avg_gain * 13.0 + max(change, 0.0)) / 14.0
        avg_loss = (avg_loss * 13.0 + max(-change, 0.0)) / 14.0

    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0
    elif avg_loss == 0:
        rsi = 100.0
    elif avg_gain == 0:
        rsi = 0.0
    else:
        rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    true_ranges = []
    for i in range(1, len(closed)):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr14 = sum(true_ranges[:14]) / 14.0
    for true_range in true_ranges[14:]:
        atr14 = (atr14 * 13.0 + true_range) / 14.0
    price = closes[-1]

    if price > sma10 > sma20 and 50.0 <= rsi < 68.0:
        signal = "BULLISH"
    elif price < sma10 < sma20 and 32.0 < rsi <= 50.0:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "price": price,
        "candle_open": opens[-1],
        "candle_high": highs[-1],
        "candle_low": lows[-1],
        "sma10": sma10,
        "sma20": sma20,
        "rsi14": rsi,
        "atr14": atr14,
        "signal": signal,
        "closed_candles": len(closed),
    }


def calculate_position_risk(capital, risk_percent, entry_price, stop_percent,
                            leverage=1.0, reward_risk=2.0):
    """Return a risk-capped linear-USDT position estimate (fees excluded)."""
    capital = _finite(capital, "capital")
    risk_percent = _finite(risk_percent, "risk_percent")
    entry_price = _finite(entry_price, "entry_price")
    stop_percent = _finite(stop_percent, "stop_percent")
    leverage = _finite(leverage, "leverage")
    reward_risk = _finite(reward_risk, "reward_risk")

    if capital <= 0 or entry_price <= 0:
        raise ValueError("capital and entry price must be positive")
    if not 0 < risk_percent <= 5:
        raise ValueError("risk percent must be greater than 0 and at most 5")
    if not 0 < stop_percent < 100:
        raise ValueError("stop percent must be greater than 0 and below 100")
    if not leverage.is_integer() or not 1 <= leverage <= 3:
        raise ValueError("leverage must be an integer between 1 and 3")
    if not 0 < reward_risk <= 10:
        raise ValueError("reward/risk must be greater than 0 and at most 10")

    risk_limit = capital * risk_percent / 100.0
    stop_distance = entry_price * stop_percent / 100.0
    max_notional = capital * leverage
    if not all(math.isfinite(value) and value > 0 for value in
               (risk_limit, stop_distance, max_notional)):
        raise ValueError("risk inputs exceed the supported numeric range")
    # First size from the risk budget, then cap notional to available
    # collateral times leverage. Recompute actual risk after the cap.
    desired_quantity = risk_limit / stop_distance
    desired_notional = desired_quantity * entry_price
    notional = min(desired_notional, max_notional)
    quantity = notional / entry_price
    risk_amount = quantity * stop_distance
    margin_estimate = notional / leverage
    target_distance = stop_distance * reward_risk
    target_price_short = entry_price - target_distance
    computed = (desired_quantity, desired_notional, notional, quantity,
                risk_amount, margin_estimate, target_distance, target_price_short)
    if not all(math.isfinite(value) for value in computed):
        raise ValueError("risk calculation exceeds the supported numeric range")
    if (quantity <= 0 or notional <= 0 or margin_estimate <= 0
            or entry_price - stop_distance <= 0 or target_price_short <= 0):
        raise ValueError("stop and target prices must remain positive")

    return {
        "risk_amount": risk_amount,
        "risk_limit": risk_limit,
        "stop_price_long": entry_price - stop_distance,
        "stop_price_short": entry_price + stop_distance,
        "target_price_long": entry_price + target_distance,
        "target_price_short": target_price_short,
        "quantity": quantity,
        "notional": notional,
        "margin_estimate": margin_estimate,
        "leverage": leverage,
        "liquidation_warning": leverage >= 3,
    }


def recommend_leverage(technical, max_leverage=3):
    """Choose conservative leverage from ATR volatility; never places orders."""
    if not isinstance(technical, dict):
        raise ValueError("technical analysis is required")
    signal = technical.get("signal", "NEUTRAL")
    price = _finite(technical.get("price"), "price")
    atr = _finite(technical.get("atr14"), "atr14")
    cap = int(_finite(max_leverage, "max_leverage"))
    if (price <= 0 or atr <= 0 or cap < 1 or cap > 3
            or not _finite(max_leverage, "max_leverage").is_integer()):
        raise ValueError("invalid price, ATR, or leverage cap")
    volatility_pct = (atr / price) * 100.0
    if not math.isfinite(volatility_pct):
        raise ValueError("volatility is outside the supported numeric range")
    if signal not in ("BULLISH", "BEARISH"):
        return {"leverage": 1, "volatility_pct": volatility_pct,
                "reason": "سیگنال قطعی نیست؛ ورود انجام نشود",
                "trade_allowed": False}
    if volatility_pct > 2.0:
        return {"leverage": 1, "volatility_pct": volatility_pct,
                "reason": "نوسان زیاد؛ ورود انجام نشود",
                "trade_allowed": False}
    if volatility_pct <= 1.0:
        leverage, reason = min(3, cap), "نوسان پایین؛ اهرم محدود"
    else:
        leverage, reason = min(2, cap), "نوسان متوسط؛ اهرم کمتر"
    return {"leverage": leverage, "volatility_pct": volatility_pct,
            "reason": reason, "trade_allowed": True}
