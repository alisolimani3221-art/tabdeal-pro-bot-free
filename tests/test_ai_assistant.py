import ast
import unittest
from pathlib import Path


class OpenAIAssistantUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("main.py").read_text(encoding="utf-8")
        cls.module = ast.parse(cls.source, filename="main.py")
        cls.method = next(
            node for node in ast.walk(cls.module)
            if isinstance(node, ast.FunctionDef)
            and node.name == "open_openai_assistant"
        )
        cls.method_source = ast.get_source_segment(cls.source, cls.method)

    def test_openai_assistant_is_wired_into_android_app(self):
        imported_modules = {
            node.module.split(".")[0]
            for node in ast.walk(self.module)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(self.module)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn("openai_assistant", imported_modules)
        self.assertIn("open_openai_assistant", {
            node.name for node in ast.walk(self.module)
            if isinstance(node, ast.FunctionDef)
        })

    def test_assistant_output_is_plain_text_and_uses_native_rtl(self):
        self.assertIn("markup=False", self.method_source)
        self.assertNotIn("assistant_rtl_text(", self.method_source)
        self.assertIn('result.text = "\\n\\n".join(lines)', self.method_source)
        self.assertIn("chat_history", self.method_source)
        self.assertIn("conversation_history=history_snapshot", self.method_source)
        self.assertIn('title=fa_plain("گفت‌وگو با هوش مصنوعی Groq")', self.method_source)
        self.assertNotIn('result.text = fa(', self.method_source)
        self.assertNotIn('setattr(result, "text", fa(value))', self.method_source)

    def test_persian_assistant_result_uses_rtl_alignment_and_scrollable_height(self):
        self.assertIn('halign="right"', self.method_source)
        self.assertIn('base_direction="rtl", text_language="ar"', self.method_source)
        self.assertIn('base_direction="rtl", text_language="ar", halign="right"', self.method_source)
        self.assertIn("result.bind(texture_size=", self.method_source)
        self.assertIn("result_scroll = ScrollView(", self.method_source)
        self.assertIn("result_scroll.add_widget(result)", self.method_source)

    def test_assistant_action_row_is_ordered_for_rtl(self):
        self.assertLess(
            self.method_source.index("controls.add_widget(close_button)"),
            self.method_source.index("controls.add_widget(send_button)")
        )

    def test_api_key_is_cleared_when_popup_closes(self):
        self.assertIn('key_input.text = ""', self.method_source)
        self.assertIn('popup.bind(on_dismiss=', self.method_source)
        self.assertIn('password=True', self.method_source)

    def test_assistant_uses_privacy_safe_read_only_context(self):
        self.assertIn("build_ai_diagnostic_context(self)", self.method_source)
        self.assertIn("def render_chat()", self.method_source)
        self.assertIn('label = "شما" if item["role"] == "user" else "Groq"', self.method_source)
        helper = Path("ai_diagnostics.py").read_text(encoding="utf-8")
        self.assertIn('"access_mode": "read_only"', helper)
        self.assertIn('"no trading"', helper)
        self.assertIn('"no order placement"', helper)
        self.assertNotIn("account_summary", self.method_source)
        self.assertNotIn("CredentialStore", self.method_source)


if __name__ == "__main__":
    unittest.main()
