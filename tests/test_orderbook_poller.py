import unittest
from pathlib import Path
from unittest.mock import Mock

from orderbook_poller import fetch_order_book_snapshot


class OrderBookPollerTests(unittest.TestCase):
    def make_session(self, response):
        session = Mock()
        session.get.return_value = response
        return session

    def test_app_schedules_independent_two_second_polling_without_overlap(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Clock.schedule_interval(\n                self._schedule_order_book_poll, 2.0", source)
        self.assertIn("self.orderbook_poll_lock.acquire(False)", source)
        self.assertIn("self.orderbook_poll_stop.set()", source)
        self.assertIn("fetch_order_book_snapshot(", source)

    def test_fetches_one_valid_depth_snapshot_and_closes_response(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "bids": [["100", "2"], ["99", "3"]],
            "asks": [["101", "4"], ["102", "5"]],
        }
        session = self.make_session(response)

        result = fetch_order_book_snapshot(
            session, "https://api.example/fapi/v1", "BTC_USDT", "/ca.pem"
        )

        session.get.assert_called_once()
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://api.example/fapi/v1/depth")
        self.assertEqual(kwargs["params"], {"symbol": "BTC_USDT", "limit": 5})
        self.assertEqual(kwargs["verify"], "/ca.pem")
        self.assertEqual(kwargs["timeout"], (3.05, 6.0))
        self.assertEqual(result["best_bid"], 100.0)
        self.assertEqual(result["best_ask"], 101.0)
        self.assertEqual(result["mid_price"], 100.5)
        self.assertEqual(result["bids"][0], (100.0, 2.0))
        self.assertTrue(result["received_at"])
        response.close.assert_called_once()

    def test_rejects_unverified_symbol_format_before_network(self):
        session = Mock()
        with self.assertRaisesRegex(ValueError, "invalid futures symbol"):
            fetch_order_book_snapshot(
                session, "https://api.example/fapi/v1", "BTCUSDT", "/ca.pem"
            )
        session.get.assert_not_called()

    def test_rejects_non_200_and_closes_response(self):
        response = Mock()
        response.status_code = 429
        response.text = "rate limited"
        session = self.make_session(response)
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            fetch_order_book_snapshot(
                session, "https://api.example/fapi/v1", "BTC_USDT", "/ca.pem"
            )
        response.close.assert_called_once()

    def test_rejects_malformed_book_and_closes_response(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"bids": [], "asks": []}
        session = self.make_session(response)
        with self.assertRaises(ValueError):
            fetch_order_book_snapshot(
                session, "https://api.example/fapi/v1", "BTC_USDT", "/ca.pem"
            )
        response.close.assert_called_once()

    def test_rejects_invalid_json_and_closes_response(self):
        response = Mock()
        response.status_code = 200
        response.json.side_effect = ValueError("bad json")
        session = self.make_session(response)
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            fetch_order_book_snapshot(
                session, "https://api.example/fapi/v1", "BTC_USDT", "/ca.pem"
            )
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
