"""Independent read-only public trade stream for live-price display.

This component has no reference to the paper engine, persisted trading state,
credentials, order APIs, candle builders, or trade coordinator. Its only output
is a validated public-market price callback for UI display.
"""
import re
import threading
import time

from trade_stream_probe import (
    BROADCAST_URL,
    is_trade_timestamp_fresh,
    parse_trade_message,
)
from android_tls import tabdeal_websocket_ssl_options
import certifi


_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")


def validate_readonly_symbol(value):
    """Return a canonical exchange-native symbol or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("market symbol must be a string")
    symbol = value.strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("market symbol is not a valid USDT futures contract")
    return symbol


def parse_readonly_trade(message, expected_symbol, now_ms=None):
    """Parse only a fresh, validated trade for the currently selected symbol."""
    symbol = validate_readonly_symbol(expected_symbol)
    parsed = parse_trade_message(message, expected_symbol=symbol)
    if not parsed.get("ok"):
        return None
    if not is_trade_timestamp_fresh(parsed.get("timestamp_ms"), now_ms=now_ms):
        return None
    return parsed


class ReadOnlyLivePriceStream:
    """Reconnectable WebSocket consumer that can only publish validated prices."""

    def __init__(self, symbol_provider, on_price, reconnect_min=2.0,
                 reconnect_max=15.0):
        if not callable(symbol_provider) or not callable(on_price):
            raise ValueError("symbol_provider and on_price must be callable")
        self._symbol_provider = symbol_provider
        self._on_price = on_price
        self._reconnect_min = max(0.5, float(reconnect_min))
        self._reconnect_max = max(self._reconnect_min, float(reconnect_max))
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._thread = None
        self._ws = None
        self.last_error = None
        self.messages = 0
        self.valid_trades = 0
        self.last_trade_at = None

    def start(self):
        """Start once; repeated calls do not create duplicate sockets."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="readonly-live-price", daemon=True
            )
            self._thread.start()
            return True

    def refresh_symbol(self):
        """Close the old subscription so the worker reconnects for the new symbol."""
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def stop(self, timeout=2.0):
        """Stop the socket and worker without touching any trading state."""
        self._stop_event.set()
        self.refresh_symbol()
        with self._lock:
            thread = self._thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=max(0.0, float(timeout)))
        return thread is None or not thread.is_alive()

    def _current_symbol(self):
        return validate_readonly_symbol(self._symbol_provider())

    def _run(self):
        retry = 0
        while not self._stop_event.is_set():
            try:
                symbol = self._current_symbol()
                import websocket

                def on_open(ws):
                    if self._stop_event.is_set() or self._current_symbol() != symbol:
                        ws.close()
                        return
                    # Public broadcast subscription: plain-text symbol only.
                    ws.send(symbol)

                def on_message(ws, message):
                    self.messages += 1
                    if self._stop_event.is_set() or self._current_symbol() != symbol:
                        return
                    parsed = parse_readonly_trade(message, symbol)
                    if parsed is None:
                        return
                    self.valid_trades += 1
                    self.last_trade_at = time.monotonic()
                    try:
                        self._on_price(
                            symbol, float(parsed["price"]),
                            int(parsed["timestamp_ms"]),
                        )
                    except Exception as callback_exc:
                        self.last_error = "price_callback:" + type(callback_exc).__name__

                def on_error(ws, error):
                    self.last_error = "{}: {}".format(
                        type(error).__name__, str(error)[:160]
                    )

                app = websocket.WebSocketApp(
                    BROADCAST_URL,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                )
                with self._lock:
                    if self._stop_event.is_set():
                        break
                    self._ws = app
                app.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    reconnect=0,
                    sslopt=tabdeal_websocket_ssl_options(certifi.where()),
                )
                retry = 0 if self._stop_event.is_set() else retry + 1
            except Exception as exc:
                self.last_error = "{}: {}".format(
                    type(exc).__name__, str(exc)[:160]
                )
                retry += 1
            finally:
                with self._lock:
                    if self._ws is not None:
                        self._ws = None
            if not self._stop_event.is_set():
                delay = min(self._reconnect_max,
                            self._reconnect_min * (2 ** min(retry, 4)))
                self._stop_event.wait(delay)
