import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_assistant


class FakeResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "تحلیل محدود داده"}}]}

    def close(self):
        self.closed = True


def load_assistant_rtl_text():
    source = Path("main.py").read_text(encoding="utf-8")
    module = ast.parse(source, filename="main.py")
    node = next(item for item in module.body
                if isinstance(item, ast.FunctionDef) and item.name == "assistant_rtl_text")
    isolated = ast.Module(body=[node], type_ignores=[])
    namespace = {
        "contains_arabic": lambda value: any(0x0600 <= ord(ch) <= 0x06ff for ch in str(value)),
        "arabic_reshaper": SimpleNamespace(reshape=lambda value: "SHAPED:" + value),
        "get_display": lambda value, base_dir=None: "VISUAL:" + value,
    }
    exec(compile(isolated, "main.py", "exec"), namespace)
    return namespace["assistant_rtl_text"]


class OneXAiAssistantTests(unittest.TestCase):
    def test_prompt_whitelists_market_fields(self):
        prompt = ai_assistant.build_prompt(
            "وضعیت چیست؟",
            {"symbol": "BTC_USDT", "bids": [[10, 2]], "api_key": "MUST_NOT_APPEAR"}
        )
        self.assertIn("BTC_USDT", prompt)
        self.assertIn("single order-book snapshot", prompt)
        self.assertNotIn("MUST_NOT_APPEAR", prompt)
        self.assertNotIn("api_key", prompt)

    def test_prompt_includes_explicit_closed_candle_indicators(self):
        prompt = ai_assistant.build_prompt(
            "تحلیل تکنیکال چیست؟",
            {"symbol": "BTC_USDT", "sma20": 100.5, "signal": "BULLISH",
             "closed_candles": 51, "api_secret": "MUST_NOT_APPEAR"}
        )
        self.assertIn("100.5", prompt)
        self.assertIn("BULLISH", prompt)
        self.assertNotIn("MUST_NOT_APPEAR", prompt)

    def test_rejects_empty_question(self):
        with self.assertRaises(ValueError):
            ai_assistant.build_prompt(" ", {"symbol": "BTC_USDT"})

    def test_rejects_non_1xai_key(self):
        with self.assertRaisesRegex(ValueError, "1xai-"):
            ai_assistant.analyze_with_1xai("not-a-1xai-key", "پرسش", {})

    def test_assistant_rtl_display_shapes_persian(self):
        render_text = load_assistant_rtl_text()
        self.assertEqual(render_text("متن فارسی"), "VISUAL:SHAPED:متن فارسی")

    def test_result_label_does_not_apply_second_rtl_reorder(self):
        source = Path("main.py").read_text(encoding="utf-8")
        module = ast.parse(source, filename="main.py")
        method = next(item for item in ast.walk(module)
                      if isinstance(item, ast.FunctionDef) and item.name == "open_ai_assistant")
        result_label = next(node.value for node in ast.walk(method)
                            if isinstance(node, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "result_label" for t in node.targets)
                            and isinstance(node.value, ast.Call))
        direction = next(k.value.value for k in result_label.keywords
                         if k.arg == "base_direction" and isinstance(k.value, ast.Constant))
        self.assertEqual(direction, "ltr")

    def test_assistant_rtl_preserves_latin_only_text(self):
        self.assertEqual(load_assistant_rtl_text()("BTC_USDT 123"), "BTC_USDT 123")

    @patch("ai_assistant.requests.post")
    def test_key_is_sent_as_bearer_header_not_url_or_body(self, post):
        post.return_value = FakeResponse()
        answer = ai_assistant.analyze_with_1xai(
            "1xai-test-secret", "این داده چه می‌گوید؟", {"symbol": "BTC_USDT"}
        )
        self.assertEqual(answer, "تحلیل محدود داده")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://1xai.ir/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer 1xai-test-secret")
        self.assertNotIn("1xai-test-secret", args[0])
        self.assertNotIn("1xai-test-secret", str(kwargs["json"]))
        self.assertEqual(kwargs["json"]["model"], "gemini-2.5-flash")

    @patch("ai_assistant.requests.post")
    def test_insufficient_balance_error_does_not_echo_key(self, post):
        response = FakeResponse()
        response.status_code = 402
        post.return_value = response
        with self.assertRaisesRegex(RuntimeError, "موجودی کیف پول"):
            ai_assistant.analyze_with_1xai(
                "1xai-private-value", "پرسش", {"symbol": "BTC_USDT"}
            )


if __name__ == "__main__":
    unittest.main()
