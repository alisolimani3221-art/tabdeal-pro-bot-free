import pathlib
import unittest


class AccountCredentialDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pathlib.Path("main.py").read_text(encoding="utf-8")
        start = cls.source.index("    def open_account_connection(")
        end = cls.source.index("    def open_order_level(", start)
        cls.dialog = cls.source[start:end]

    def test_credentials_are_not_cleared_before_worker_finishes(self):
        start = self.dialog.index("        def start_read(")
        finish = self.dialog.index("            def finish(result):", start)
        start_block = self.dialog[start:finish]
        self.assertNotIn('key_input.text = ""', start_block)
        self.assertNotIn('secret_input.text = ""', start_block)

    def test_credentials_are_cleared_only_after_encrypted_save_success(self):
        finish = self.dialog.index("            def finish(result):")
        worker = self.dialog.index("            worker_thread = threading.Thread(", finish)
        finish_block = self.dialog[finish:worker]
        saved_check = finish_block.index('result[2].startswith("ذخیره امن روی همین دستگاه انجام شد")')
        clear_key = finish_block.index('key_input.text = ""')
        clear_secret = finish_block.index('secret_input.text = ""')
        dismiss = finish_block.index("popup.dismiss()", saved_check)
        self.assertLess(saved_check, clear_key)
        self.assertLess(saved_check, clear_secret)
        self.assertLess(clear_secret, dismiss)

    def test_failed_account_or_storage_keeps_dialog_recoverable(self):
        finish = self.dialog.index("            def finish(result):")
        worker = self.dialog.index("            worker_thread = threading.Thread(", finish)
        finish_block = self.dialog[finish:worker]
        for control in ("key_input", "secret_input", "connect", "delete_saved"):
            self.assertIn(control + ".disabled = False", finish_block)
        self.assertIn("کلیدها هنوز ذخیره نشده‌اند", finish_block)
        self.assertIn("Do not discard the user's credentials", finish_block)
        self.assertIn("status.text = fa(\"اتصال موفق نشد:", finish_block)


if __name__ == "__main__":
    unittest.main()
