import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AndroidBackgroundKeepaliveTests(unittest.TestCase):
    def test_native_service_is_declared_in_same_app_process(self):
        spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        manifest = (ROOT / "android_manifest_application_arguments.xml").read_text(encoding="utf-8")
        java = (ROOT / "android_src/org/tabdeal/futuresanalyzer/MarketKeepService.java").read_text(encoding="utf-8")

        self.assertIn("android.add_src = android_src", spec)
        self.assertIn("p4a.branch = 32994bea648eddf6808477ac91cd74632287a7fe", spec)
        self.assertIn("--extra-manifest-application-xml", spec)
        self.assertNotIn("android.extra_manifest_application_arguments =", spec)
        self.assertIn("org.tabdeal.futuresanalyzer.MarketKeepService", spec)
        self.assertIn("android:foregroundServiceType=\"specialUse\"", manifest)
        self.assertIn("PROPERTY_SPECIAL_USE_FGS_SUBTYPE", manifest)
        self.assertIn("Continuous user-requested read-only futures market monitoring", manifest)
        self.assertIn("android:exported=\"false\"", manifest)
        # No android:process override: the native service shares the Kivy app
        # process, so it protects the already-running Python worker.
        self.assertNotIn("android:process", manifest)
        self.assertIn("START_NOT_STICKY", java)
        self.assertIn("FOREGROUND_SERVICE_TYPE_SPECIAL_USE", java)
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

    def test_closing_ui_keeps_active_market_worker_and_foreground_service(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        stop_start = source.index("    def on_stop(self):")
        stop_end = source.find("\n\nif __name__ == \"__main__\":", stop_start)
        if stop_end < 0:
            stop_end = len(source)
        stop = source[stop_start:stop_end]
        preserve = stop[:stop.index("        self._stop_background_market_service()")]

        self.assertIn("keep_monitoring", stop)
        self.assertIn("self._start_background_market_service()", preserve)
        self.assertIn("return", preserve)
        self.assertNotIn("self._stop_auto_worker(", preserve)
        self.assertIn("self._persist_paper_engine_state(engine)", preserve)

        start_start = source.index("    def on_start(self):")
        start_end = source.index("\n    def ", start_start + 10)
        start = source[start_start:start_end]
        self.assertIn("existing_worker.is_alive()", start)
        self.assertIn("if existing_worker is None or not existing_worker.is_alive():", start)

    def test_required_android_permissions_are_declared(self):
        spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        for permission in (
            "INTERNET", "FOREGROUND_SERVICE",
            "FOREGROUND_SERVICE_SPECIAL_USE", "POST_NOTIFICATIONS", "WAKE_LOCK",
        ):
            with self.subTest(permission=permission):
                self.assertIn(permission, spec)


if __name__ == "__main__":
    unittest.main()
