"""Privacy-safe, read-only context builder for the in-app AI diagnostics."""

APP_DIAGNOSTIC_KEYS = (
    "website", "market_ping", "market_info", "depth_status", "depth_http",
    "depth_content_type", "market_symbol", "depth_diagnostic", "market_error",
    "analysis_error", "summary_error", "ping_error",
)
MARKET_KEYS = (
    "symbol", "snapshot_time", "best_bid", "best_ask", "midpoint_estimate",
    "bids", "asks", "data_kind", "sma20", "sma50", "rsi14", "atr14",
    "signal", "closed_candles",
)
TECHNICAL_KEYS = (
    "symbol", "sma10", "sma20", "sma50", "rsi14", "atr14", "signal",
    "trend", "support", "resistance", "data_kind", "candle_count",
)
NESTED_KEYS = ("price", "quantity", "qty", "size", "amount", "volume", "time")


def _safe_value(value, depth=0):
    """Keep only bounded JSON-like scalar/list data; discard arbitrary nested objects."""
    if depth > 3:
        return None
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth + 1) for item in value[:10]
                if isinstance(item, (str, int, float, bool, type(None), list, tuple))]
    if isinstance(value, dict):
        return {
            str(key)[:40]: _safe_value(item, depth + 1)
            for key, item in list(value.items())[:10]
            if key in NESTED_KEYS
            and isinstance(item, (str, int, float, bool, type(None), list, tuple))
        }
    return None


def _bounded_mapping(value, allowed_keys):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in allowed_keys:
        if key in value:
            safe = _safe_value(value[key])
            if safe is not None:
                result[key] = safe
    return result


def build_ai_diagnostic_context(app):
    """Return only allow-listed app and market diagnostics; never account data or credentials."""
    diagnostics = _bounded_mapping(
        getattr(app, "last_diagnostic_results", {}),
        APP_DIAGNOSTIC_KEYS,
    )
    market = _bounded_mapping(
        getattr(app, "last_market_snapshot", {}),
        MARKET_KEYS,
    )
    technical = _bounded_mapping(
        getattr(app, "latest_technical_analysis", {}),
        TECHNICAL_KEYS,
    )
    return {
        "app_version": "1.45",
        "selected_symbol": str(getattr(app, "current_symbol", "unknown"))[:40],
        "diagnostics": diagnostics,
        "diagnostic_time": str(getattr(app, "last_diagnostic_time", "unknown"))[:80],
        "market_snapshot": market,
        "technical_analysis": technical,
        "worker_status": str(getattr(app, "auto_worker_status", ""))[:240],
        "worker_status_time": str(getattr(app, "auto_worker_status_time", ""))[:80],
        "access_mode": "read_only",
        "restrictions": [
            "no settings changes", "no trading", "no order placement",
            "no order cancellation", "no account or credential access",
        ],
    }
