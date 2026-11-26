"""Strict analysis of 1m, 5m and 15m closed futures candles.

Only complete, exchange-epoch-aligned 5m/15m bars are aggregated from the
validated 1m stream. Entry requires all three timeframe signals to agree.
"""
import math

MINUTE_MS = 60_000
TRADE_TIMEFRAMES_MINUTES = (1, 5, 15)
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


def analyze_multi_timeframe(candles, now_ms, analyzer):
    """Require directional agreement from the closed 1m, 5m and 15m signals."""
    if not callable(analyzer):
        raise ValueError("analyzer must be callable")
    if not isinstance(candles, (list, tuple)):
        raise ValueError("one-minute candles must be a list")

    # The 1m analyzer excludes the final (possibly forming) candle itself.
    one_minute = analyzer(candles)
    if not isinstance(one_minute, dict):
        raise ValueError("one-minute analyzer returned an invalid result")

    aggregated = aggregate_closed_timeframes(candles, now_ms)
    analyses = {"1": dict(one_minute)}
    counts = {"1": one_minute.get("closed_candles", 0)}
    signals = {"1": one_minute.get("signal", "NEUTRAL")}
    closes = {}
    for minutes in AGGREGATED_TIMEFRAMES_MINUTES:
        rows = aggregated[minutes]
        counts[str(minutes)] = len(rows)
        if len(rows) < 23:
            continue
        interval_ms = minutes * MINUTE_MS
        last = rows[-1]
        # analyze_klines excludes its final row; this sentinel makes all
        # validated, fully closed aggregate rows eligible for indicators.
        sentinel_price = last[4]
        sentinel = [
            last[0] + interval_ms,
            sentinel_price, sentinel_price, sentinel_price,
            sentinel_price, 0.0,
        ]
        technical = analyzer(rows + [sentinel], interval_ms=interval_ms)
        if not isinstance(technical, dict):
            raise ValueError("timeframe analyzer returned an invalid result")
        analyses[str(minutes)] = dict(technical)
        signals[str(minutes)] = technical.get("signal", "NEUTRAL")
        closes[str(minutes)] = int(last[0])

    base = dict(one_minute)
    ready = (
        type(counts.get("1")) is int and counts["1"] >= 23
        and all(str(minutes) in analyses for minutes in AGGREGATED_TIMEFRAMES_MINUTES)
    )
    directions = [signals.get(str(minutes)) for minutes in TRADE_TIMEFRAMES_MINUTES]
    agreement = bool(
        ready and directions[0] in ("BULLISH", "BEARISH")
        and all(direction == directions[0] for direction in directions)
    )
    result = dict(base)
    result["signal"] = directions[0] if agreement else "NEUTRAL"
    result["timeframe_analysis"] = {
        "ready": ready,
        "agreement": agreement,
        "direction": directions[0] if agreement else None,
        "required_minutes": list(TRADE_TIMEFRAMES_MINUTES),
        "closed_candles": counts,
        "signals": signals,
        "last_closed_open_times": closes,
    }
    return result
