"""Privacy-safe, read-only diagnostics context for the in-app Groq assistant."""

APP_DIAGNOSTIC_KEYS = (
    "website", "market_ping", "market_info", "depth_status", "depth_http",
    "depth_content_type", "market_symbol", "symbol_verified", "depth_diagnostic",
    "market_error", "market_info_error", "analysis_error", "summary_error",
    "ping_error", "bids", "asks", "best_bid", "best_ask", "mid_price",
    "market_price_source", "symbol_count",
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
    """Keep bounded JSON-like values only; discard arbitrary objects and secrets."""
    if depth > 3:
        return None
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [
            _safe_value(item, depth + 1)
            for item in value[:10]
            if isinstance(item, (str, int, float, bool, type(None), list, tuple))
        ]
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


def _safe_status(value, limit=240):
    if value is None:
        return ""
    return str(value)[:limit]


def build_ai_diagnostic_context(app):
    """Build a read-only snapshot; never include account data, API keys or secrets."""
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

    worker_thread = getattr(app, "auto_trader_thread", None)
    worker_alive = bool(worker_thread and worker_thread.is_alive())
    runtime_ready = bool(getattr(app, "runtime_lock_ready", False))
    bids = diagnostics.get("bids", [])
    asks = diagnostics.get("asks", [])
    book_status = (
        "parsed" if bids and asks else
        "not_available"
    )

    return {
        "app_version": _safe_status(getattr(app, "app_version", "unknown"), 40),
        "selected_symbol": _safe_status(getattr(app, "current_symbol", "unknown"), 40),
        "diagnostics": diagnostics,
        "order_book": {
            "status": book_status,
            "bid_levels": len(bids) if isinstance(bids, list) else 0,
            "ask_levels": len(asks) if isinstance(asks, list) else 0,
            "bids": _safe_value(bids),
            "asks": _safe_value(asks),
        },
        "diagnostic_time": _safe_status(
            getattr(app, "last_diagnostic_time", "unknown"), 80
        ),
        "market_snapshot": market,
        "technical_analysis": technical,
        "engine": {
            "thread_alive": worker_alive,
            "worker_status": _safe_status(
                getattr(app, "auto_worker_status", "not_reported")
            ),
            "worker_status_time": _safe_status(
                getattr(app, "auto_worker_status_time", ""), 80
            ),
            "runtime_guard_ready": runtime_ready,
            "runtime_guard_error": _safe_status(
                getattr(app, "runtime_lock_error", "")
            ),
            "coordinator_session_ready": bool(
                getattr(app, "coordinator_session", None)
            ),
            "coordinator_session_error": _safe_status(
                getattr(app, "coordinator_session_error", "")
            ),
        },
        "interface": {
            "persian_font_loaded": bool(getattr(app, "ai_font_loaded", False)),
            "font_load_error": _safe_status(getattr(app, "ai_font_error", "")),
        },
        "access_mode": "read_only",
        "data_scope": (
            "Only the current in-memory diagnostic snapshot, public market/order-book "
            "data, engine health flags and interface status are supplied. "
            "No source files, private app storage, account data, balances, positions, "
            "API credentials or secrets are read or transmitted."
        ),
        "restrictions": [
            "no settings changes", "no trading", "no order placement",
            "no order cancellation", "no account or credential access",
            "no file-system browsing",
        ],
    }
