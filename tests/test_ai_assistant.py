import ast
import unittest
from pathlib import Path


class RemovedAssistantUiTests(unittest.TestCase):
    def test_standalone_ai_assistant_is_not_wired_into_android_app(self):
        source = Path("main.py").read_text(encoding="utf-8")
        module = ast.parse(source, filename="main.py")
        names = {
            node.name for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(module)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("open_ai_assistant", names)
        self.assertNotIn("ai_assistant", imports)
        self.assertNotIn("دستیار هوش مصنوعی", source)
        self.assertIn("تحلیل هوشمند بازار", source)


if __name__ == "__main__":
    unittest.main()
