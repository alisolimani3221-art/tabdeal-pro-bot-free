import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
)


def has_arabic(text):
    return any(start <= ord(char) <= end
               for char in text for start, end in ARABIC_RANGES)


class UiFontAssignmentTests(unittest.TestCase):
    def test_latin_only_font_is_never_used_for_persian_text(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            uses_latin_font = any(
                keyword.arg in ("font", "font_name")
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "NUMERIC_FONT_NAME"
                for keyword in node.keywords
            )
            if not uses_latin_font:
                continue
            text_expr = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "text"),
                node.args[0] if node.args else None,
            )
            if text_expr is None:
                continue
            literals = [child.value for child in ast.walk(text_expr)
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)]
            if any(has_arabic(value) for value in literals):
                violations.append("{}:{}".format(node.lineno, ast.unparse(text_expr)))
        self.assertEqual(violations, [], "Persian text assigned to Latin-only font")

    def test_runtime_default_font_is_arabic_capable_for_all_widgets(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('name="PersianNoto"', source)
        self.assertIn('name="Roboto"', source)
        self.assertIn("NUMERIC_FONT_NAME = FONT_NAME", source)
        self.assertIn("def font_for_text(text):", source)

    def test_plain_persian_text_uses_arabic_shaping_helper(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def contains_arabic(text):", source)
        plain = source[source.index("def fa_plain(text):"):source.index("def fa(text):")]
        self.assertIn("if not contains_arabic(value):", plain)

    def test_font_workflow_checks_real_arabic_and_latin_glyphs(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn('"\\u0600"', workflow)
        self.assertIn('"\\u06ff"', workflow)
        self.assertIn("missing_arabic", workflow)
        self.assertIn("missing_latin", workflow)
        self.assertIn("NotoSansArabic-Regular.ttf", workflow)
        self.assertIn("NotoSans-Regular.ttf", workflow)

    def test_workflow_concurrency_is_scoped_per_branch(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("tabdeal-free-apk-build-${{ github.ref }}", workflow)

    def test_font_workflow_verifies_rtl_libraries_inside_apk(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("vendor/arabic_reshaper/__init__[.]py", workflow)
        self.assertIn("vendor/bidi/algorithm[.]py", workflow)

    def test_font_workflow_verifies_both_fonts_inside_apk(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("Verify both runtime fonts and RTL libraries are packaged in APK", workflow)
        self.assertIn("assets/private.tar", workflow)
        self.assertIn("Upload APK", workflow)


if __name__ == "__main__":
    unittest.main()
