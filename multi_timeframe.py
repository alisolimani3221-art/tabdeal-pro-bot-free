"""Strict analysis of 1m, 5m and 15m closed futures candles.

Only complete, exchange-epoch-aligned 5m/15m bars are aggregated from the
validated 1m stream. The 5m/15m signals establish bias; the 1m close is only
an entry trigger and its categorical signal never overrides the higher bias.
"""
import math

MINUTE_MS = 60_000
TRADE_TIMEFRAMES_MINUTES = (1, 5, 15)
MIN_CLOSED_CANDLES_BY_TIMEFRAME = {"1": 23, "5": 3, "15": 3}
AGGREGATED_TIMEFRAMES_MINUTES = (5, 15)


def aggregate_closed_timeframes(candles, now_ms):
    """Return complete epoch-aligned 5m/15m OHLCV bars from contiguous 1m rows."""
    if not isinstance(candles, (list, tuple)):
        raise ValueError("one-minute candles must be a list")
    if isinstance(now_ms, bool):
        raise ValueError("server time must be a finite integer")
    try:
        now = int(now_ms)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("server time must be a finite integer")
    if now <= 0 or str(now) != str(now_ms).strip():
        raise ValueError("server time must be a positive integer")

    rows = []
    previous = None
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ValueError("invalid one-minute OHLCV row")
        try:
            ts = int(row[0])
            o, h, low, close, volume = (float(row[i]) for i in range(1, 6))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("one-minute OHLCV values must be numeric")
        if isinstance(row[0], bool) or ts < 0 or ts % MINUTE_MS:
            raise ValueError("minute timestamp must be epoch-minute aligned")
        if not all(math.isfinite(x) for x in (o, h, low, close, volume)):
            raise ValueError("OHLCV values must be finite")
        if min(o, h, low, close) <= 0 or volume < 0:
            raise ValueError("OHLC prices must be positive and volume nonnegative")
        if h < low or not low <= o <= h or not low <= close <= h:
            raise ValueError("inconsistent one-minute OHLC range")
        if previous is not None and ts - previous != MINUTE_MS:
            raise ValueError("one-minute history contains a gap or duplicate")
        previous = ts
        rows.append((ts, o, h, low, close, volume))

    result = {}
    for minutes in AGGREGATED_TIMEFRAMES_MINUTES:
        width = minutes * MINUTE_MS
        buckets = {}
        for row in rows:
            ts = row[0]
            bucket_start = (ts // width) * width
            bucket_end = bucket_start + width
            if bucket_end > now:
                continue
            buckets.setdefault(bucket_start, []).append(row)
        complete = []
        for start in sorted(buckets):
            members = buckets[start]
            if len(members) != minutes:
                continue
            if any(members[i][0] != start + i * MINUTE_MS
                   for i in range(minutes)):
                continue
            complete.append([
                start, members[0][1],
                max(item[2] for item in members),
                min(item[3] for item in members),
                members[-1][4], sum(item[5] for item in members),
            ])
        result[minutes] = complete
    return result


def _trend_direction(rows):
    """Infer trend from three closed-bar closes, not the latest candle color.

    A latest red body can still belong to a rising sequence, and vice versa.
    A net move plus a confirming latest close avoids treating one
    counter-trend candle body as a full reversal.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) < 3:
        return "NEUTRAL"
    closes = []
    for row in rows[-3:]:
        try:
            value = float(row[4])
        except (TypeError, ValueError, IndexError, OverflowError):
            return "NEUTRAL"
        if not math.isfinite(value) or value <= 0:
            return "NEUTRAL"
        closes.append(value)
    first, middle, last = closes
    if last > first and last >= middle:
        return "BULLISH"
    if last < first and last <= middle:
        return "BEARISH"
    return "NEUTRAL"


def analyze_multi_timeframe(candles, now_ms, analyzer):
    """Use multi-bar higher-timeframe close structure, not candle body color."""
    if not callable(analyzer):
        raise ValueError("analyzer must be callable")
    if not isinstance(candles, (list, tuple)):
        raise ValueError("one-minute candles must be a list")

    one_minute = analyzer(candles)
    if not isinstance(one_minute, dict):
        raise ValueError("one-minute analyzer returned an invalid result")

    aggregated = aggregate_closed_timeframes(candles, now_ms)
    counts = {"1": one_minute.get("closed_candles", 0)}
    signals = {"1": one_minute.get("signal", "NEUTRAL")}
    atr_by_timeframe = {}
    risk_range_by_timeframe = {}
    closes = {}
    required_counts = MIN_CLOSED_CANDLES_BY_TIMEFRAME
    if type(one_minute.get("closed_candles")) is int and one_minute["closed_candles"] >= 23:
        try:
            one_minute_atr = float(one_minute.get("atr14"))
            if math.isfinite(one_minute_atr) and one_minute_atr > 0:
                atr_by_timeframe["1"] = one_minute_atr
        except (TypeError, ValueError, OverflowError):
            pass

    for minutes in AGGREGATED_TIMEFRAMES_MINUTES:
        rows = aggregated[minutes]
        counts[str(minutes)] = len(rows)
        required = required_counts[str(minutes)]
        if len(rows) < required:
            continue
        selected = rows[-required:]
        # Trend follows the sequence of closed prices, not candle body color.
        # A red 15m body may still sit inside a rising 15m price structure.
        signals[str(minutes)] = _trend_direction(selected)
        closes[str(minutes)] = int(rows[-1][0])

    # A full 5m ATR(14) is not available from the warm-up window.
    # Use the largest true range of the two latest completed bars as a
    # conservative stop-distance proxy instead.
    five_rows = aggregated[5]
    if len(five_rows) >= 2:
        recent = five_rows[-2:]
        ranges = []
        previous_close = recent[0][4]
        for index, row in enumerate(recent):
            high, low = row[2], row[3]
            true_range = high - low
            if index > 0:
                true_range = max(
                    true_range, abs(high - previous_close),
                    abs(low - previous_close),
                )
            ranges.append(true_range)
            previous_close = row[4]
        risk_range = max(ranges)
        if math.isfinite(risk_range) and risk_range > 0:
            risk_range_by_timeframe["5"] = risk_range

    ready = (
        type(counts.get("1")) is int and counts["1"] >= 23
        and all(counts.get(str(minutes), 0) >= required_counts[str(minutes)]
                for minutes in AGGREGATED_TIMEFRAMES_MINUTES)
    )
    bias_directions = [signals.get("5"), signals.get("15")]
    bias_agreement = bool(
        ready and bias_directions[0] in ("BULLISH", "BEARISH")
        and bias_directions[1] == bias_directions[0]
    )
    bias = bias_directions[0] if bias_agreement else None

    # The noisy 1m signal label is deliberately NOT used as the trend filter.
    # Entry timing requires a closed 1m close beyond SMA10 with RSI on the
    # matching side of 50, while avoiding extreme momentum/chasing.
    entry_direction = None
    try:
        price = float(one_minute["price"])
        sma10 = float(one_minute["sma10"])
        rsi = float(one_minute["rsi14"])
        if all(math.isfinite(value) for value in (price, sma10, rsi)):
            if price > sma10 and 50.0 < rsi < 70.0:
                entry_direction = "BULLISH"
            elif price < sma10 and 30.0 < rsi < 50.0:
                entry_direction = "BEARISH"
    except (KeyError, TypeError, ValueError, OverflowError):
        entry_direction = None

    entry_trigger = bool(bias_agreement and entry_direction == bias)
    base = dict(one_minute)
    result = dict(base)
    result["signal"] = bias if entry_trigger else "NEUTRAL"
    result["timeframe_analysis"] = {
        "ready": ready,
        "agreement": bias_agreement,
        "direction": bias,
        "entry_trigger": entry_trigger,
        "entry_trigger_direction": entry_direction,
        "required_minutes": list(TRADE_TIMEFRAMES_MINUTES),
        "closed_candles": counts,
        "signals": signals,
        "atr14_by_timeframe": atr_by_timeframe,
        "risk_range_by_timeframe": risk_range_by_timeframe,
        "last_closed_open_times": closes,
    }
    return result
