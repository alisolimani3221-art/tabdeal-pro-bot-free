"""Read-only futures indicators and conservative position-risk calculations."""

import math


def _finite(value, name):
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
    """Calculate SMA20/SMA50, RSI14 and ATR14 from standard OHLC candle rows.

    The final candle is excluded because it may still be forming.
    """
    if not isinstance(candles, (list, tuple)):
        raise ValueError("candles must be a list")
    closed = list(candles[:-1])
    if len(closed) < 51:
        raise ValueError("at least 52 candles are required")

    highs, lows, closes = [], [], []
    for candle in closed:
        if not isinstance(candle, (list, tuple)) or len(candle) < 5:
            raise ValueError("invalid candle format")
        highs.append(_finite(candle[2], "high"))
        lows.append(_finite(candle[3], "low"))
        closes.append(_finite(candle[4], "close"))
    if any(h <= 0 or l <= 0 or c <= 0 or h < l or c < l or c > h
           for h, l, c in zip(highs, lows, closes)):
        raise ValueError("invalid OHLC prices: close must be within high/low range")

    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes[-14:]]
    losses = [max(-change, 0.0) for change in changes[-14:]]
    avg_gain = sum(gains) / 14.0
    avg_loss = sum(losses) / 14.0
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
    atr14 = sum(true_ranges[-14:]) / 14.0
    price = closes[-1]

    if price > sma20 > sma50 and 50.0 <= rsi < 68.0:
        signal = "BULLISH"
    elif price < sma20 < sma50 and 32.0 < rsi <= 50.0:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "price": price,
        "sma20": sma20,
        "sma50": sma50,
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
    if not 1 <= leverage <= 3:
        raise ValueError("leverage must be between 1 and 3")
    if not 0 < reward_risk <= 10:
        raise ValueError("reward/risk must be greater than 0 and at most 10")
    if reward_risk * stop_percent >= 100:
        raise ValueError("short target price would be zero or negative")

    risk_amount = capital * risk_percent / 100.0
    stop_distance = entry_price * stop_percent / 100.0
    # Risk sizing must also respect available collateral. A very tight stop
    # can otherwise produce a notional position many times larger than
    # capital * leverage even though the nominal stop-loss budget looks safe.
    risk_limited_quantity = risk_amount / stop_distance
    max_collateral_quantity = (capital * leverage) / entry_price
    quantity = min(risk_limited_quantity, max_collateral_quantity)
    notional = quantity * entry_price
    margin_estimate = notional / leverage
    actual_risk_amount = quantity * stop_distance
    target_distance = stop_distance * reward_risk

    return {
        "risk_amount": actual_risk_amount,
        "risk_limit": risk_amount,
        "stop_price_long": entry_price - stop_distance,
        "stop_price_short": entry_price + stop_distance,
        "target_price_long": entry_price + target_distance,
        "target_price_short": entry_price - target_distance,
        "quantity": quantity,
        "notional": notional,
        "margin_estimate": margin_estimate,
        "leverage": leverage,
        "liquidation_warning": leverage >= 10,
    }

    
def recommend_leverage(technical, max_leverage=3):
    """Choose conservative leverage from ATR volatility; never places orders."""
    if not isinstance(technical, dict):
        raise ValueError("technical analysis is required")
    signal = technical.get("signal", "NEUTRAL")
    price = _finite(technical.get("price"), "price")
    atr = _finite(technical.get("atr14"), "atr14")
    cap = int(_finite(max_leverage, "max_leverage"))
    if price <= 0 or atr < 0 or cap < 1 or cap > 3:
        raise ValueError("invalid price, ATR, or leverage cap")
    volatility_pct = (atr / price) * 100.0
    if atr == 0 or signal not in ("BULLISH", "BEARISH") or volatility_pct > 2.5:
        return {"leverage": 1, "volatility_pct": volatility_pct,
                "reason": "سیگنال یا نوسان شرایط ورود را ندارد",
                "trade_allowed": False}
    if volatility_pct <= 1.0:
        leverage, reason = min(3, cap), "نوسان پایین؛ اهرم محدود"
    elif volatility_pct <= 2.0:
        leverage, reason = min(2, cap), "نوسان متوسط؛ اهرم کمتر"
    else:
        leverage, reason = 1, "نوسان زیاد؛ فقط اهرم 1 یا عدم ورود"
    return {"leverage": leverage, "volatility_pct": volatility_pct,
            "reason": reason, "trade_allowed": True}
