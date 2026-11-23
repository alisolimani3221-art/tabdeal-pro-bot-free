"""Foreground, read-only live last-trade price feed for the selected Tabdeal futures symbol.

This feed updates display-only market price state. It never creates candles,
signals, account requests, or exchange orders.
"""
import re
import ssl
import threading
import time

import certifi

from trade_stream_probe import BROADCAST_URL, parse_trade_message


_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")


class LiveMarketPriceFeed:
    """Reconnectable WebSocket reader that publishes validated last-trade prices."""

    def __init__(self, symbol, on_price, on_status=None, reconnect_delay=3.0):
        self._validate_symbol(symbol)
        if not callable(on_price):
            raise ValueError("on_price callback is required")
        if not (0.5 <= float(reconnect_delay) <= 60):
            raise ValueError("reconnect_delay must be between 0.5 and 60 seconds")
        self.symbol = symbol
        self.on_price = on_price
        self.on_status = on_status if callable(on_status) else (lambda *_: None)
        self.reconnect_delay = float(reconnect_delay)
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self._lock = threading.RLock()

    @staticmethod
    def _validate_symbol(symbol):
        if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must be an exchange-native USDT futures symbol")
        return symbol

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="TabdealLivePriceFeed", daemon=True
            )
            self._thread.start()
            return True

    def set_symbol(self, symbol):
        self._validate_symbol(symbol)
        with self._lock:
            if symbol == self.symbol:
                return False
            self.symbol = symbol
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        return True

    def stop(self, timeout=3.0):
        self._stop.set()
        with self._lock:
            ws = self._ws
            thread = self._thread
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        return thread is None or not thread.is_alive()

    def _run(self):
        try:
            import websocket
        except Exception as exc:
            self.on_status("feed_unavailable: {}: {}".format(type(exc).__name__, str(exc)[:120]))
            return

        while not self._stop.is_set():
            with self._lock:
                symbol = self.symbol

            def on_open(ws):
                ws.send(symbol)
                self.on_status("connected:" + symbol)

            def on_message(ws, message):
                parsed = parse_trade_message(message, expected_symbol=symbol)
                if parsed.get("ok") and not self._stop.is_set():
                    self.on_price(symbol, parsed["price"], parsed["timestamp_ms"])

            def on_error(ws, error):
                if not self._stop.is_set():
                    self.on_status("reconnecting: {}: {}".format(type(error).__name__, str(error)[:100]))

            app = websocket.WebSocketApp(
                BROADCAST_URL, on_open=on_open, on_message=on_message,
                on_error=on_error,
            )
            with self._lock:
                self._ws = app
            try:
                app.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()},
                )
            except Exception as exc:
                if not self._stop.is_set():
                    self.on_status("reconnecting: {}: {}".format(type(exc).__name__, str(exc)[:100]))
            finally:
                with self._lock:
                    if self._ws is app:
                        self._ws = None
            if not self._stop.is_set():
                self.on_status("disconnected: reconnecting")
                self._stop.wait(self.reconnect_delay)
