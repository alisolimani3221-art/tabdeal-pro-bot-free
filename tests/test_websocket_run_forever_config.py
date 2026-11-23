"""Regression guard for WebSocket live-socket timeout configuration."""
import ast
from pathlib import Path
import unittest


class WebSocketRunForeverConfigTests(unittest.TestCase):
    def test_live_socket_has_no_short_read_timeout(self):
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
        self.assertTrue(calls, "paper trade worker must run a WebSocketApp")
        kwargs = {kw.arg: kw.value for kw in calls[0].keywords}
        self.assertNotIn("timeout", kwargs,
                         "run_forever timeout is a live socket read timeout")
        self.assertEqual(ast.literal_eval(kwargs["ping_interval"]), 20)
        self.assertEqual(ast.literal_eval(kwargs["ping_timeout"]), 10)
        self.assertEqual(ast.literal_eval(kwargs["reconnect"]), 0)


if __name__ == "__main__":
    unittest.main()
