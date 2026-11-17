import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_assistant


class FakeResponse:
    status_code = 200

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "تحلیل محدود داده"}]}}]}

    def close(self):
        self.closed = True


def load_assistant_rtl_text():
    """Load the pure display helper without importing the Android/Kivy app."""
    source = Path("main.py").read_text(encoding="utf-8")
    module = ast.parse(source, filename="main.py")
    node = next(
        item for item in module.body
        if isinstance(item, ast.FunctionDef) and item.name == "assistant_rtl_text"
    )
    isolated = ast.Module(body=[node], type_ignores=[])
    namespace = {
        "contains_arabic": lambda value: any(0x0600 <= ord(ch) <= 0x06ff for ch in str(value)),
        "arabic_reshaper": SimpleNamespace(reshape=lambda value: "SHAPED:" + value),
    }
    exec(compile(isolated, "main.py", "exec"), namespace)
    return namespace["assistant_rtl_text"]


class GeminiAssistantTests(unittest.TestCase):
    def test_prompt_whitelists_market_fields(self):
        prompt = ai_assistant.build_prompt(
            "وضعیت چیست؟",
            {"symbol": "BTC_USDT", "bids": [[10, 2]], "api_key": "MUST_NOT_APPEAR"}
        )
        self.assertIn("BTC_USDT", prompt)
        self.assertIn("single order-book snapshot", prompt)
        self.assertNotIn("MUST_NOT_APPEAR", prompt)
        self.assertNotIn("api_key", prompt)

    def test_rejects_empty_question(self):
        with self.assertRaises(ValueError):
            ai_assistant.build_prompt(" ", {"symbol": "BTC_USDT"})

    def test_assistant_rtl_display_shapes_persian_without_reordering(self):
        render_text = load_assistant_rtl_text()
        self.assertEqual(render_text("متن فارسی"), "VISUAL:SHAPED:متن فارسی")

    def test_assistant_rtl_display_preserves_latin_only_text(self):
        render_text = load_assistant_rtl_text()
        self.assertEqual(render_text("BTC_USDT 123"), "BTC_USDT 123")

    @patch("ai_assistant.requests.post")
    def test_key_is_sent_in_header_not_url(self, post):
        post.return_value = FakeResponse()
        answer = ai_assistant.analyze_with_gemini(
            "test-secret", "این داده چه می‌گوید؟", {"symbol": "BTC_USDT"}
        )
        self.assertEqual(answer, "تحلیل محدود داده")
        args, kwargs = post.call_args
        self.assertNotIn("test-secret", args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-secret")
        self.assertNotIn("test-secret", str(kwargs["json"]))

    @patch("ai_assistant.requests.post")
    def test_unauthorized_error_does_not_echo_key(self, post):
        response = FakeResponse()
        response.status_code = 403
        post.return_value = response
        with self.assertRaisesRegex(RuntimeError, "کلید Gemini پذیرفته نشد"):
            ai_assistant.analyze_with_gemini(
                "private-key-value", "پرسش", {"symbol": "BTC_USDT"}
            )


if __name__ == "__main__":
    unittest.main()
