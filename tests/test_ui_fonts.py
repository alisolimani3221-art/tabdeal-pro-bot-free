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

    def test_persian_bidi_direction_is_explicitly_right_to_left(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('get_display(arabic_reshaper.reshape(value), base_dir="R")', source)

    def test_runtime_diagnostics_exercise_actual_rtl_pipeline(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def rtl_runtime_status():", source)
        self.assertIn('get_display(shaped, base_dir="R")', source)
        self.assertIn("rtl_runtime_status()", source)

    def test_plain_persian_text_uses_arabic_shaping_helper(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def contains_arabic(text):", source)
        plain = source[source.index("def fa_plain(text):"):source.index("def fa(text):")]
        self.assertIn("if not contains_arabic(value):", plain)

    def test_validation_workflow_bundles_both_runtime_fonts(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("NotoSansArabic-Regular.ttf", workflow)
        self.assertIn("NotoSans-Regular.ttf", workflow)
        self.assertIn('test -s NotoSans-Regular.ttf', workflow)
        self.assertIn('test -s NotoSansArabic-Regular.ttf', workflow)

    def test_account_worker_captures_credentials_before_clearing_them(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        app = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "FuturesAnalyzerApp")
        account_method = next(node for node in app.body
                              if isinstance(node, ast.FunctionDef)
                              and node.name == "open_account_connection")
        worker = next(node for node in ast.walk(account_method)
                      if isinstance(node, ast.FunctionDef) and node.name == "worker")
        self.assertEqual([arg.arg for arg in worker.args.args], ["api_key", "api_secret"])
        self.assertEqual(worker.args.defaults, [])
        calls = [node for node in ast.walk(worker)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == "read_futures_account"]
        self.assertEqual(len(calls), 1)
        self.assertEqual([arg.id for arg in calls[0].args
                          if isinstance(arg, ast.Name)],
                         ["api_key", "api_secret"])
        thread_calls = [node for node in ast.walk(account_method)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "Thread"]
        self.assertTrue(any(
            any(keyword.arg == "args"
                and isinstance(keyword.value, ast.Tuple)
                and [item.id for item in keyword.value.elts
                     if isinstance(item, ast.Name)] == ["key", "secret"]
                for keyword in call.keywords)
            for call in thread_calls
        ))

    def test_workflow_concurrency_is_scoped_per_branch(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("tabdeal-free-apk-build-${{ github.head_ref || github.ref }}", workflow)
        self.assertIn("cancel-in-progress: true", workflow)

    def test_font_license_is_bundled_in_validation_workspace(self):
        spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("txt", spec)
        self.assertIn("assets/*.txt", spec)
        self.assertIn('cp "$FONT_LICENSE" assets/FONT-LICENSE.txt', workflow)

    def test_workflow_builds_persistently_signed_paper_only_apk_and_verifies_fonts(self):
        workflow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("Run full regression suite", workflow)
        self.assertIn("Install Buildozer and Android Python packaging tools", workflow)
        self.assertIn("Configure persistent Android signing (required)", workflow)
        self.assertIn("ANDROID_SIGNING_BUNDLE_BASE64", workflow)
        self.assertIn("Build signed release APK (paper-only)", workflow)
        self.assertIn("Verify persistent APK signing certificate", workflow)
        self.assertIn("9a41c32295b7723ee8dfadd4c40ee7dbdca9fb8a6c62e43aabc09c9bef741ae5", workflow)
        self.assertIn("Verify APK output and packaged Persian fonts", workflow)
        self.assertIn("Upload installable APK", workflow)
        self.assertIn("assets/private.tar", workflow)
        self.assertIn("tar -tf /tmp/private.tar", workflow)


if __name__ == "__main__":
    unittest.main()
