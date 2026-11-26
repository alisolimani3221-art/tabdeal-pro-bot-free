"""Privacy-safe, read-only diagnostics context for the in-app Groq assistant."""

import time

APP_VERSION = "1.56"

APP_DIAGNOSTIC_KEYS = (
    "website", "market_ping", "market_info", "depth_status", "depth_http",
    "depth_content_type", "market_symbol", "symbol_verified", "depth_diagnostic",
    "market_error", "market_info_error", "analysis_error", "summary_error",
    "ping_error", "bids", "asks", "best_bid", "best_ask", "mid_price",
    "market_price_source", "symbol_count", "orderbook_poll_last_success",
    "orderbook_poll_updates", "orderbook_poll_error",
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
HEALTH_KEYS = (
    "worker_phase", "websocket_connected", "retry_attempt", "reconnect_count",
    "received_messages", "valid_market_trades", "control_messages", "selected_symbol_trade_count",
    "candle_count", "builder_ready", "analysis_required_candles",
    "candle_interval_seconds", "reconnect_partial_buckets_discarded",
    "reconnect_history_clear_count", "reconnect_last_reset_reason",
    "duplicate_sequence_rejections", "older_sequence_rejections",
    "out_of_order_rejections", "last_stream_error",
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


def _engine_health(app, worker_alive):
    """Classify the engine from socket, selected-symbol, warm-up and scan evidence."""
    lock = getattr(app, "auto_health_lock", None)
    source = getattr(app, "auto_health", {})
    if lock is not None:
        with lock:
            source = dict(source) if isinstance(source, dict) else {}
    else:
        source = dict(source) if isinstance(source, dict) else {}
    result = _bounded_mapping(source, HEALTH_KEYS)
    now = time.monotonic()
    for source_key, output_key in (
        ("last_any_market_trade_at", "last_any_market_trade_age_seconds"),
        ("last_selected_symbol_trade_at", "selected_symbol_trade_age_seconds"),
        ("last_scan_at", "last_scan_age_seconds"),
        ("connected_at", "connected_age_seconds"),
    ):
        stamp = source.get(source_key)
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            age = now - float(stamp)
            result[output_key] = round(max(0.0, age), 1) if age >= 0 else None
        else:
            result[output_key] = None

    selected_age = result["selected_symbol_trade_age_seconds"]
    scan_age = result["last_scan_age_seconds"]
    phase = result.get("worker_phase")
    # on_open initially labels the socket as waiting for its first trade. Once
    # selected-symbol ticks arrive, derive the visible phase from actual builder
    # telemetry so the diagnostic cannot remain stuck on that startup label.
    if (phase == "connected_waiting_for_trade" and result.get("websocket_connected")
            and selected_age is not None and selected_age <= 30
            and result.get("thread_alive", True)):
        closed_count = result.get("candle_count", 0)
        if result.get("builder_ready"):
            phase = "analysis_ready_waiting_for_scan"
        elif isinstance(closed_count, int) and closed_count >= 5:
            phase = "preliminary_monitoring"
        elif isinstance(closed_count, int) and closed_count >= 0:
            phase = "collecting_selected_symbol_candles"
        result["worker_phase"] = phase
    if phase in ("blocked_by_safety", "blocked_by_persisted_state"):
        state = "BLOCKED_BY_SAFETY"
    elif not worker_alive:
        state = "WORKER_STOPPED"
    elif not result.get("websocket_connected"):
        state = "CONNECTING_OR_DISCONNECTED"
    elif selected_age is None:
        state = "WAITING_FOR_SELECTED_SYMBOL_TRADE"
    elif selected_age > 30:
        state = "SELECTED_SYMBOL_STALE"
    elif not result.get("builder_ready"):
        state = "WARMING_UP"
    elif scan_age is None:
        state = "WAITING_FOR_FIRST_SCAN"
    elif scan_age > 30:
        state = "SCAN_STALE"
    else:
        state = "ACTIVE"
    result["health_state"] = state
    result["thread_alive"] = bool(worker_alive)
    return result


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
    book_status = "parsed" if bids and asks else "not_available"

    return {
        "app_version": APP_VERSION,
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
            "health": _engine_health(app, worker_alive),
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


def build_engine_health_report_lines(app):
    """Return bounded, privacy-safe engine telemetry for the on-device report."""
    health = build_ai_diagnostic_context(app)["engine"]["health"]

    def value(key, default="not recorded"):
        item = health.get(key)
        return default if item is None or item == "" else str(item)

    def age(key):
        item = health.get(key)
        return "not recorded" if item is None else "{:.1f}".format(float(item))

    return [
        "سلامت جریان معاملات: " + value("health_state", "UNKNOWN"),
        "مرحله کاری موتور: " + value("worker_phase"),
        "اتصال WebSocket: " + value("websocket_connected", "false"),
        "رشته معامله‌گر زنده: " + value("thread_alive", "false"),
        "تلاش اتصال مجدد متوالی: " + value("retry_attempt", "0"),
        "اتصال‌های مجدد در این اجرا: " + value("reconnect_count", "0"),
        "پیام‌های دریافتی: " + value("received_messages", "0"),
        "پیام‌های کنترلی/غیرمعاملاتی: " + value("control_messages", "0"),
        "معاملات معتبر همه نمادها: " + value("valid_market_trades", "0"),
        "معاملات معتبر نماد انتخابی: " + value("selected_symbol_trade_count", "0"),
        "عمر آخرین معامله نماد انتخابی (ثانیه): " + age("selected_symbol_trade_age_seconds"),
        "کندل‌های بسته: " + value("candle_count", "0"),
        "حذف کندل ناقص پس از اتصال مجدد: " + value("reconnect_partial_buckets_discarded", "0"),
        "پاک‌سازی تاریخچه کندل: " + value("reconnect_history_clear_count", "0"),
        "آخرین علت بازنشانی کندل: " + value("reconnect_last_reset_reason"),
        "رد به‌علت sequence تکراری: " + value("duplicate_sequence_rejections", "0"),
        "رد به‌علت sequence قدیمی: " + value("older_sequence_rejections", "0"),
        "رد به‌علت ترتیب زمانی: " + value("out_of_order_rejections", "0"),
        "کندل‌های باقی‌مانده تا تحلیل: " + str(max(
            0, int(health.get("analysis_required_candles", 23) or 23)
            - int(health.get("candle_count", 0) or 0)
        )),
        "بازه هر کندل (ثانیه): " + value("candle_interval_seconds", "60"),
        "آماده‌بودن کندل‌ساز: " + value("builder_ready", "false"),
        "عمر آخرین اسکن موتور (ثانیه): " + (
            age("last_scan_age_seconds")
            if health.get("last_scan_age_seconds") is not None
            else (
                "هنوز اجرا نشده؛ در انتظار تکمیل کندل‌های معتبر"
                if health.get("health_state") == "WARMING_UP"
                else (
                    "کندل‌ساز آماده است؛ در انتظار نخستین تصویر تحلیلی و اسکن"
                    if health.get("health_state") == "WAITING_FOR_FIRST_SCAN"
                    else "اسکن هنوز ثبت نشده یا دادهٔ زمان‌بندی در دسترس نیست"
                )
            )
        ),
        "عمر اتصال WebSocket (ثانیه): " + age("connected_age_seconds"),
        "آخرین خطای جریان: " + value("last_stream_error", "ندارد")[:240],
    ]
