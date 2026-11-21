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
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(self.module)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertIn("openai_assistant", imports)
        self.assertIn("open_openai_assistant", {
            node.name for node in ast.walk(self.module)
            if isinstance(node, ast.FunctionDef)
        })

    def test_assistant_output_is_plain_text_and_shaped_once(self):
        self.assertIn("markup=False", self.method_source)
        self.assertIn("assistant_rtl_text(value)", self.method_source)
        self.assertNotIn('result.text = fa(', self.method_source)
        self.assertNotIn('setattr(result, "text", fa(value))', self.method_source)

    def test_api_key_is_cleared_when_popup_closes(self):
        self.assertIn('key_input.text = ""', self.method_source)
        self.assertIn('popup.bind(on_dismiss=', self.method_source)
        self.assertIn('password=True', self.method_source)

    def test_assistant_remains_read_only(self):
        self.assertIn('"access_mode": "read_only"', self.method_source)
        self.assertIn('"no trading"', self.method_source)
        self.assertIn('"no order placement"', self.method_source)


if __name__ == "__main__":
    unittest.main()
