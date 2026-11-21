import ast
import unittest
from pathlib import Path


class ChatGPTDiagnosticsShareTests(unittest.TestCase):
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
        cls.helper = Path("chatgpt_share.py").read_text(encoding="utf-8")

    def test_chatgpt_share_button_is_wired(self):
        self.assertIn("open_openai_assistant", self.source)
        self.assertIn("ارسال گزارش عیب‌یابی به ChatGPT", self.source)
        self.assertIn("share_diagnostics_to_chatgpt(report)", self.method_source)

    def test_report_is_read_only_and_excludes_credentials(self):
        self.assertIn("Access mode: read-only", self.method_source)
        self.assertIn("API keys, API secrets", self.method_source)
        self.assertIn("balances, positions, orders", self.method_source)
        self.assertNotIn("CredentialStore", self.method_source)
        self.assertNotIn("api_secret", self.method_source.lower())

    def test_share_uses_android_chooser_and_user_confirmation(self):
        self.assertIn("Intent.ACTION_SEND", self.helper)
        self.assertIn("Intent.createChooser", self.helper)
        self.assertIn("activity.startActivity(chooser)", self.helper)
        self.assertIn("user chooses ChatGPT", self.helper)

    def test_manual_api_key_popup_is_removed(self):
        self.assertNotIn("کلید API اوپن‌ای‌آی", self.method_source)
        self.assertNotIn("key_input", self.method_source)
        self.assertNotIn("analyze_with_openai", self.method_source)

    def test_report_is_bounded_and_line_separated(self):
        self.assertIn('[:240]', self.method_source)
        self.assertIn('"\\n".join(lines)', self.method_source)


if __name__ == "__main__":
    unittest.main()
