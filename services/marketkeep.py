"""Sticky Android foreground collector for validated Tabdeal market candles.

The service owns only the public read-only market feed while the Kivy UI is
backgrounded. It never reads account credentials or submits exchange orders.
"""
import json
import os
import re
import threading
import time

from candle_archive import record_closed_candles
from market_candles import TradeCandleBuilder
from trade_stream_probe import parse_trade_message, is_trade_timestamp_fresh
from trade_stream_watchdog import reconnect_backoff_seconds
from android_tls import tabdeal_websocket_ssl_options
import certifi

BROADCAST_URL = "wss://api1.tabdeal.org/special_margin/broadcast/"
SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")
SILENCE_TIMEOUT_SECONDS = 60.0


def parse_service_config(raw, private_dir):
    """Validate the explicit app-private service arguments before file access."""
    if not isinstance(raw, str) or len(raw) > 4096:
        raise ValueError("invalid service argument")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("service argument is not valid JSON")
    if not isinstance(value, dict):
        raise ValueError("service argument must be an object")
    symbol = value.get("symbol")
    if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("invalid futures symbol")
    archive_path = value.get("archive_path")
    if not isinstance(archive_path, str) or not archive_path:
        raise ValueError("missing candle archive path")
    if not isinstance(private_dir, str) or not private_dir:
        raise ValueError("Android private directory is unavailable")
    private_root = os.path.realpath(private_dir)
    archive_real = os.path.realpath(archive_path)
    try:
        inside = os.path.commonpath((private_root, archive_real)) == private_root
    except (ValueError, OSError):
        inside = False
    if not inside:
        raise ValueError("candle archive must stay inside app-private storage")
    return {"symbol": symbol, "archive_path": archive_real}


def _persist(builder, archive_path):
    rows = list(builder.closed)
    if rows:
        return record_closed_candles(
            archive_path, builder.symbol, rows, interval_ms=builder.interval_ms
        )
    return 0


def run_collector(config, stop_event=None, websocket_module=None):
    """Run a reconnecting, validated, read-only market collector."""
    if stop_event is None:
        stop_event = threading.Event()
    if websocket_module is None:
        import websocket as websocket_module

    symbol = config["symbol"]
    archive_path = config["archive_path"]
    builder = TradeCandleBuilder(symbol, max_candles=2000)
    retry_attempt = 0

    while not stop_event.is_set():
        state = {"last_valid": None, "opened_at": None, "ws": None}

        def on_open(ws):
            state["ws"] = ws
            state["opened_at"] = time.monotonic()
            state["last_valid"] = time.monotonic()
            ws.send(symbol)

        def on_message(ws, message):
            parsed = parse_trade_message(message)
            if not parsed.get("ok") or parsed.get("symbol") != symbol:
                return
            if not is_trade_timestamp_fresh(parsed.get("timestamp_ms")):
                return
            result = builder.add_trade(parsed)
            if result.get("accepted"):
                state["last_valid"] = time.monotonic()
                try:
                    _persist(builder, archive_path)
                except Exception as exc:
                    print("MarketKeep archive write failed: {}: {}".format(
                        type(exc).__name__, str(exc)[:120]
                    ))

        def on_error(ws, error):
            print("MarketKeep WebSocket error: {}".format(type(error).__name__))

        app = websocket_module.WebSocketApp(
            BROADCAST_URL, on_open=on_open, on_message=on_message,
            on_error=on_error,
        )
        state["ws"] = app

        def watchdog():
            while not stop_event.wait(5.0):
                last_valid = state["last_valid"]
                if (last_valid is not None
                        and time.monotonic() - last_valid >= SILENCE_TIMEOUT_SECONDS):
                    try:
                        app.close()
                    except Exception:
                        pass
                    return

        monitor = threading.Thread(target=watchdog, name="MarketKeepWatchdog", daemon=True)
        monitor.start()
        try:
            app.run_forever(
                ping_interval=20, ping_timeout=10, reconnect=0,
                sslopt=tabdeal_websocket_ssl_options(certifi.where()),
            )
        except Exception as exc:
            print("MarketKeep stream failed: {}: {}".format(
                type(exc).__name__, str(exc)[:120]
            ))
        finally:
            try:
                _persist(builder, archive_path)
            except Exception as exc:
                print("MarketKeep final archive write failed: {}".format(type(exc).__name__))
            monitor.join(timeout=1.0)

        opened_at = state["opened_at"]
        if opened_at is not None and time.monotonic() - opened_at >= 60.0:
            retry_attempt = 0
        retry_attempt += 1
        stop_event.wait(reconnect_backoff_seconds(retry_attempt))


def main():
    from jnius import autoclass

    # python-for-android's sticky service can be restarted by Android after
    # process pressure; keep its own restart behavior enabled as a second guard.
    try:
        autoclass("org.kivy.android.PythonService").mService.setAutoRestartService(True)
    except Exception:
        pass

    raw = os.environ.get("PYTHON_SERVICE_ARGUMENT", "")
    private_dir = os.environ.get("ANDROID_PRIVATE", "")
    config = parse_service_config(raw, private_dir)
    run_collector(config)


if __name__ == "__main__":
    main()
