"""Tests for strict TLS configuration shared by REST and WebSocket clients."""
import os
import ssl
import tempfile
import unittest

from android_tls import tabdeal_websocket_ssl_options


class AndroidWebSocketTLSTests(unittest.TestCase):
    def test_websocket_uses_explicit_ca_bundle_and_requires_verification(self):
        with tempfile.NamedTemporaryFile() as bundle:
            options = tabdeal_websocket_ssl_options(bundle.name)
        self.assertEqual(options["cert_reqs"], ssl.CERT_REQUIRED)
        self.assertTrue(os.path.isfile(bundle.name) is False)
        self.assertEqual(options["ca_certs"], bundle.name)

    def test_missing_ca_bundle_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            tabdeal_websocket_ssl_options("/definitely/missing/tabdeal-ca.pem")

    def test_empty_ca_bundle_path_fails_closed(self):
        for value in ("", None, 123):
            with self.subTest(value=value):
                with self.assertRaises(FileNotFoundError):
                    tabdeal_websocket_ssl_options(value)


if __name__ == "__main__":
    unittest.main()
