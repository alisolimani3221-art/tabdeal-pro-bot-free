import ast
import pathlib
import unittest


ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
)


def has_arabic(text):
    return any(
        start <= ord(char) <= end
        for char in text
        for start, end in ARABIC_RANGES
    )


class UiFontAssignmentTests(unittest.TestCase):
    def test_latin_only_font_is_never_used_for_persian_text(self):
        source = pathlib.Path("main.py").read_text(encoding="utf-8")
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
                (keyword.value for keyword in node.keywords
                 if keyword.arg == "text"),
                node.args[0] if node.args else None,
            )
            if text_expr is None:
                continue

            literals = [
                child.value for child in ast.walk(text_expr)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
            ]
            if any(has_arabic(value) for value in literals):
                violations.append(
                    "{}:{}".format(node.lineno, ast.unparse(text_expr))
                )

        self.assertEqual(
            violations, [],
            "Persian/mixed text must use the Arabic-capable font: "
            + "; ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
