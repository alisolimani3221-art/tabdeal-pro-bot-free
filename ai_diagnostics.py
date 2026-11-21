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


def _bounded_mapping(value, allowed_keys, max_value_chars=500):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in allowed_keys:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = str(item)[:max_value_chars] if isinstance(item, str) else item
        elif isinstance(item, (list, tuple)):
            result[key] = [
                (str(entry)[:120] if isinstance(entry, str) else entry)
                for entry in item[:10]
                if isinstance(entry, (str, int, float, bool)) or entry is None
            ]
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
