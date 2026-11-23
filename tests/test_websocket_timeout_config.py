"""Regression guard for WebSocket timeout configuration in the paper runner."""
import ast
from pathlib import Path
import unittest


class WebSocketTimeoutConfigTests(unittest.TestCase):
    def test_live_runner_does_not_set_process_wide_socket_timeout(self):
        source = Path("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefaulttimeout"
        ]
        self.assertEqual(
            calls, [],
            "the paper runner must not impose a process-wide socket timeout",
        )

    def test_websocket_uses_supported_ping_and_watchdog_configuration(self):
        source = Path("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_forever"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
        ]
        self.assertTrue(calls, "paper runner must start its WebSocketApp")
        kwargs = {kw.arg: kw.value for kw in calls[0].keywords}
        self.assertNotIn("timeout", kwargs)
        self.assertEqual(ast.literal_eval(kwargs["ping_interval"]), 20)
        self.assertEqual(ast.literal_eval(kwargs["ping_timeout"]), 10)
        self.assertEqual(ast.literal_eval(kwargs["reconnect"]), 0)
        self.assertIn("sslopt", kwargs)
        self.assertIn("tabdeal_websocket_ssl_options", ast.unparse(kwargs["sslopt"]))


if __name__ == "__main__":
    unittest.main()
