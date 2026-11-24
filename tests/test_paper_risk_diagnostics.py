import unittest

from paper_risk_diagnostics import build_paper_risk_diagnostic_lines


class PaperRiskDiagnosticTests(unittest.TestCase):
    def test_reports_daily_and_cumulative_floors_without_mutating_state(self):
        state = {
            "initial_capital": 1000.0,
            "cash": 988.0,
            "max_loss_percent": 3.0,
            "daily_loss_percent": 1.0,
            "daily_loss_date": "2026-11-25",
            "daily_start_equity": 1000.0,
            "halted": True,
            "halt_reason": "DAILY_MAX_LOSS",
            "halt_detail": "UTC daily paper loss limit remains active",
            "position": None,
            "closed_trades": [],
        }
        before = dict(state)
        rendered = "\\n".join(build_paper_risk_diagnostic_lines(state))
        self.assertIn("کف مجاز وجه نقد روزانه: 990", rendered)
        self.assertIn("وضعیت سقف زیان روزانه: فعال", rendered)
        self.assertIn("کف مجاز زیان تجمعی: 970", rendered)
        self.assertIn("وضعیت سقف زیان تجمعی: در محدوده مجاز", rendered)
        self.assertIn("تاریخ مبنای زیان روزانه UTC: 2026-11-25", rendered)
        self.assertEqual(state, before)

    def test_does_not_infer_cumulative_status_from_cash_with_open_position(self):
        state = {
            "initial_capital": 1000.0,
            "cash": 900.0,
            "max_loss_percent": 3.0,
            "daily_loss_percent": 1.0,
            "daily_start_equity": 1000.0,
            "position": {"side": "BULLISH"},
        }
        rendered = "\\n".join(build_paper_risk_diagnostic_lines(state))
        self.assertIn("به‌علت موقعیت باز", rendered)

    def test_invalid_or_missing_values_are_reported_without_crashing(self):
        rendered = "\\n".join(build_paper_risk_diagnostic_lines({
            "cash": float("nan"),
            "daily_loss_percent": True,
            "initial_capital": "secret",
            "max_loss_percent": -1,
        }))
        self.assertIn("داده ریسک ناقص/نامعتبر است", rendered)
        self.assertNotIn("secret", rendered)

    def test_invalid_state_shape_is_handled(self):
        self.assertIn("معتبر نیست", build_paper_risk_diagnostic_lines(None)[0])


if __name__ == "__main__":
    unittest.main()
