"""Regression tests for runtime Persian/Latin font routing in the Kivy UI."""
import ast
import re
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "main.py"
TEXT = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(TEXT)


class FontRuntimeSelectionTests(unittest.TestCase):
    def test_latin_font_is_registered_and_selected_for_ascii(self):
        self.assertIn('name="LatinNoto"', TEXT)
        self.assertIn('NUMERIC_FONT_NAME = "LatinNoto"', TEXT)
        self.assertNotIn("NUMERIC_FONT_NAME = FONT_NAME", TEXT)

    def test_font_selector_uses_arabic_only_for_arabic_script(self):
        match = re.search(
            r"def font_for_text\(text\):(?P<body>.*?)(?=\n\ndef )",
            TEXT,
            re.S,
        )
        self.assertIsNotNone(match)
        self.assertIn("contains_arabic(text)", match.group("body"))
        self.assertIn("else NUMERIC_FONT_NAME", match.group("body"))

    def test_mixed_text_routes_ascii_runs_to_latin_font(self):
        self.assertIn('"[font={}]{}[/font]".format(NUMERIC_FONT_NAME, run)', TEXT)
        self.assertIn('replace("[", "&bl;")', TEXT)
        self.assertIn('replace("]", "&br;")', TEXT)
        self.assertIn('r"[\\x20-\\x7e]+"', TEXT)

    def test_every_label_or_button_using_fa_enables_markup(self):
        offenders = []
        for node in ast.walk(TREE):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            if name not in {"Label", "Button"}:
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            text_value = keywords.get("text")
            uses_fa = (
                isinstance(text_value, ast.Call)
                and isinstance(text_value.func, ast.Name)
                and text_value.func.id == "fa"
            )
            if uses_fa and not (
                isinstance(keywords.get("markup"), ast.Constant)
                and keywords["markup"].value is True
            ):
                offenders.append(node.lineno)
        self.assertEqual([], offenders, "Kivy markup missing at lines: {}".format(offenders))


if __name__ == "__main__":
    unittest.main()
