import ssl
import unittest

from android_tls import VerifiedTLS12Adapter, mount_tabdeal_tls_compat


class FakeSession:
    def __init__(self):
        self.mounts = {}

    def mount(self, prefix, adapter):
        self.mounts[prefix] = adapter


class AndroidTlsTests(unittest.TestCase):
    def test_adapter_is_pinned_to_tls_12_and_uses_verified_context(self):
        adapter = VerifiedTLS12Adapter()
        self.assertEqual(adapter.ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(adapter.ssl_context.maximum_version, ssl.TLSVersion.TLSv1_2)
        self.assertTrue(adapter.ssl_context.verify_mode == ssl.CERT_REQUIRED)
        self.assertTrue(adapter.ssl_context.check_hostname)

    def test_mount_is_limited_to_tabdeal_api_host(self):
        session = FakeSession()
        returned = mount_tabdeal_tls_compat(session)
        self.assertIs(returned, session)
        self.assertEqual(list(session.mounts), ["https://api1.tabdeal.org/"])
        self.assertIsInstance(session.mounts["https://api1.tabdeal.org/"], VerifiedTLS12Adapter)


if __name__ == "__main__":
    unittest.main()
