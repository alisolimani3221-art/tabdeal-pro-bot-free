import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AndroidBackgroundKeepaliveTests(unittest.TestCase):
    def test_native_service_is_declared_in_same_app_process(self):
        spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        manifest = (ROOT / "android_manifest_application_arguments.xml").read_text(encoding="utf-8")
        java = (ROOT / "android_src/org/tabdeal/futuresanalyzer/MarketKeepService.java").read_text(encoding="utf-8")

        self.assertIn("android.add_src = android_src", spec)
        self.assertIn("android.extra_manifest_application_arguments = android_manifest_application_arguments.xml", spec)
        self.assertIn("android:foregroundServiceType=\"dataSync\"", manifest)
        self.assertIn("android:exported=\"false\"", manifest)
        # No android:process override: the native service shares the Kivy app
        # process, so it protects the already-running Python worker.
        self.assertNotIn("android:process", manifest)
        self.assertIn("START_NOT_STICKY", java)
        self.assertIn("FOREGROUND_SERVICE_TYPE_DATA_SYNC", java)
        self.assertIn("PARTIAL_WAKE_LOCK", java)
        self.assertIn("wakeLock.release()", java)

    def test_kivy_pause_keeps_worker_and_resume_avoids_duplicate_worker(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        pause_start = source.index("    def on_pause(self):")
        resume_start = source.index("    def on_resume(self):", pause_start)
        start_start = source.index("    def on_start(self):", resume_start)
        pause = source[pause_start:resume_start]
        resume = source[resume_start:start_start]

        self.assertIn("_start_background_market_service()", pause)
        self.assertNotIn("_stop_auto_worker(", pause)
        self.assertNotIn("_stop_background_market_service()", resume)
        self.assertIn("worker.is_alive()", resume)
        self.assertIn("start_auto_paper_trader()", resume)
        self.assertIn("_start_background_market_service()", source[source.index("    def start_auto_paper_trader"):pause_start])

    def test_required_android_permissions_are_declared(self):
        spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        for permission in (
            "INTERNET", "FOREGROUND_SERVICE",
            "FOREGROUND_SERVICE_DATA_SYNC", "POST_NOTIFICATIONS", "WAKE_LOCK",
        ):
            with self.subTest(permission=permission):
                self.assertIn(permission, spec)


if __name__ == "__main__":
    unittest.main()
