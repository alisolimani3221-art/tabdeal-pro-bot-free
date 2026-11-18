import hashlib
import hmac
import unittest
from urllib.parse import parse_qs, urlparse

from tabdeal_account import (
    _signed_get, read_futures_account, read_open_orders, read_order_status,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = ""
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}, timeout))
        return self.responses.pop(0)


class TabdealAccountTests(unittest.TestCase):
    def test_signed_get_uses_read_gateway_header_and_valid_hmac_signature(self):
        session = FakeSession([FakeResponse({"ok": True})])
        result = _signed_get("/v3/account", "test-key", "test-secret",
                             params={"recvWindow": 5000}, session=session)
        self.assertEqual(result, {"ok": True})
        url, headers, timeout = session.calls[0]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        supplied_signature = query.pop("signature")[0]
        signed_query = parsed.query.split("&signature=", 1)[0]
        expected = hmac.new(b"test-secret", signed_query.encode("utf-8"),
                            hashlib.sha256).hexdigest()
        self.assertEqual(supplied_signature, expected)
        self.assertEqual(headers["X-MBX-APIKEY"], "test-key")
        self.assertEqual(parsed.path, "/r/fapi/v3/account")
        self.assertEqual(query["recvWindow"], ["5000"])

    def test_signed_get_rejects_unexpected_path_before_network(self):
        session = FakeSession([])
        with self.assertRaises(ValueError):
            _signed_get("/fapi/v3/account", "key", "secret", session=session)
        self.assertEqual(session.calls, [])

    def test_account_reader_returns_wallet_and_only_nonzero_positions(self):
        session = FakeSession([
            FakeResponse({
                "totalWalletBalance": "120.5",
                "availableBalance": "100.0",
                "totalUnrealizedProfit": "2.5",
            }),
            FakeResponse([
                {"symbol": "BTCUSDT", "positionAmt": "0.01",
                 "entryPrice": "60000", "markPrice": "61000",
                 "unRealizedProfit": "10", "leverage": "2",
                 "positionId": 17},
                {"symbol": "ETHUSDT", "positionAmt": "0"},
            ]),
        ])
        result = read_futures_account("test-key", "test-secret", session=session)
        self.assertEqual(result["wallet_balance"], "120.5")
        self.assertEqual(result["available_balance"], "100.0")
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(result["positions"][0]["position_id"], 17)
        self.assertTrue(result["verified"])
        self.assertEqual(result["source"], "authenticated_futures_account")
        self.assertGreater(result["observed_at"], 0)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call[1]["X-MBX-APIKEY"] == "test-key"
                            for call in session.calls))
        self.assertEqual(urlparse(session.calls[0][0]).path, "/r/fapi/v3/account")
        self.assertEqual(urlparse(session.calls[1][0]).path, "/r/fapi/v3/positionRisk")

    def test_signed_get_rejects_missing_credentials_before_network(self):
        session = FakeSession([])
        with self.assertRaises(ValueError):
            _signed_get("/v3/account", "", "secret", session=session)
        self.assertEqual(session.calls, [])

    def test_api_error_response_is_closed_and_raised(self):
        response = FakeResponse({"msg": "permission denied"}, status_code=403)
        session = FakeSession([response])
        with self.assertRaises(RuntimeError):
            _signed_get("/v3/account", "key", "secret", session=session)
        self.assertTrue(response.closed)

    def test_order_status_is_read_from_official_futures_read_gateway(self):
        response = FakeResponse({"orderId": 123, "status": "FILLED"})
        session = FakeSession([response])
        result = read_order_status(
            "key", "secret", "BTC_USDT", 123, session=session
        )
        self.assertEqual(result["status"], "FILLED")
        parsed = urlparse(session.calls[0][0])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/r/fapi/v1/order")
        self.assertEqual(params["symbol"], ["BTC_USDT"])
        self.assertEqual(params["orderId"], ["123"])
        self.assertEqual(session.calls[0][1]["X-MBX-APIKEY"], "key")
        self.assertTrue(response.closed)

    def test_open_orders_are_read_only_and_can_be_scoped(self):
        session = FakeSession([FakeResponse([
            {"orderId": 12, "symbol": "BTC_USDT", "status": "NEW"}
        ])])
        result = read_open_orders(
            "key", "secret", symbol="BTC_USDT", limit=25, session=session
        )
        self.assertEqual(result[0]["orderId"], 12)
        parsed = urlparse(session.calls[0][0])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/r/fapi/v1/openOrders")
        self.assertEqual(params["symbol"], ["BTC_USDT"])
        self.assertEqual(params["limit"], ["25"])

    def test_order_readers_reject_invalid_inputs_before_network(self):
        session = FakeSession([])
        for symbol, order_id in (("BTCUSDT", 1), ("BTC_USDT", 0),
                                 ("BTC_USDT", True)):
            with self.subTest(symbol=symbol, order_id=order_id):
                with self.assertRaises(ValueError):
                    read_order_status("key", "secret", symbol, order_id,
                                      session=session)
        with self.assertRaises(ValueError):
            read_open_orders("key", "secret", symbol="BTCUSDT",
                             session=session)
        with self.assertRaises(ValueError):
            read_open_orders("key", "secret", limit=1001, session=session)
        self.assertEqual(session.calls, [])

    def test_open_orders_rejects_unexpected_response_shape(self):
        session = FakeSession([FakeResponse({"unexpected": "shape"})])
        with self.assertRaises(RuntimeError):
            read_open_orders("key", "secret", session=session)


if __name__ == "__main__":
    unittest.main()
