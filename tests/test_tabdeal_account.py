import hashlib
import hmac
import unittest
from urllib.parse import parse_qs, urlparse

from tabdeal_account import _signed_get, read_futures_account


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
    def test_signed_get_uses_get_header_and_valid_hmac_signature(self):
        session = FakeSession([FakeResponse({"ok": True})])
        result = _signed_get("/r/fapi/v3/account", "test-key", "test-secret",
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
                 "unRealizedProfit": "10", "leverage": "2"},
                {"symbol": "ETHUSDT", "positionAmt": "0"},
            ]),
        ])
        result = read_futures_account("test-key", "test-secret", session=session)
        self.assertEqual(result["wallet_balance"], "120.5")
        self.assertEqual(result["available_balance"], "100.0")
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call[1]["X-MBX-APIKEY"] == "test-key"
                            for call in session.calls))

    def test_signed_get_rejects_missing_credentials_before_network(self):
        session = FakeSession([])
        with self.assertRaises(ValueError):
            _signed_get("/r/fapi/v3/account", "", "secret", session=session)
        self.assertEqual(session.calls, [])

    def test_api_error_response_is_closed_and_raised(self):
        response = FakeResponse({"msg": "permission denied"}, status_code=403)
        session = FakeSession([response])
        with self.assertRaises(RuntimeError):
            _signed_get("/r/fapi/v3/account", "key", "secret", session=session)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
