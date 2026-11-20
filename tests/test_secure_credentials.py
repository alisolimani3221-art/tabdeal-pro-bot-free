import json
import os
import tempfile
import unittest

from secure_credentials import CredentialStore


class TestBackend:
    def encrypt(self, plaintext):
        return b"0123456789ab", bytes(value ^ 0x5A for value in plaintext)

    def decrypt(self, iv, ciphertext):
        if iv != b"0123456789ab":
            raise ValueError("bad IV")
        return bytes(value ^ 0x5A for value in ciphertext)

    def delete_key(self):
        self.deleted = True


class SecureCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "credentials.json")
        self.backend = TestBackend()
        self.store = CredentialStore(self.path, self.backend)

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_no_plaintext_on_disk(self):
        key = "TEST_API_KEY_123"
        secret = "TEST_API_SECRET_456"
        self.store.save(key, secret)
        with open(self.path, encoding="utf-8") as stream:
            raw = stream.read()
        self.assertNotIn(key, raw)
        self.assertNotIn(secret, raw)
        envelope = json.loads(raw)
        self.assertEqual(envelope["algorithm"], "AES-256-GCM")
        self.assertEqual(self.store.load(), (key, secret))


    def test_save_fails_if_saved_credentials_cannot_be_read_back(self):
        class BrokenReadbackBackend(TestBackend):
            def decrypt(self, iv, ciphertext):
                raise ValueError("simulated readback failure")

        broken_store = CredentialStore(self.path, BrokenReadbackBackend())
        with self.assertRaises(RuntimeError):
            broken_store.save("key", "secret")

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.store.load())

    def test_tampered_ciphertext_fails_closed(self):
        self.store.save("key", "secret")
        with open(self.path, encoding="utf-8") as stream:
            envelope = json.load(stream)
        envelope["ciphertext"] = "!!!!"
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(envelope, stream)
        with self.assertRaises(RuntimeError):
            self.store.load()

    def test_delete_removes_file_and_key(self):
        self.store.save("key", "secret")
        self.store.delete()
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(self.backend.deleted)

    def test_empty_credentials_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save("", "secret")

    def test_status_reports_missing_or_saved_without_exposing_values(self):
        self.assertEqual(self.store.check_status(), "MISSING")
        self.store.save("private-key", "private-secret")
        self.assertEqual(self.store.check_status(), "SAVED")

    def test_status_fails_closed_when_keystore_cannot_decrypt(self):
        class BrokenBackend(TestBackend):
            def decrypt(self, iv, ciphertext):
                raise ValueError("keystore unavailable")

        self.store.save("key", "secret")
        broken = CredentialStore(self.path, BrokenBackend())
        with self.assertRaises(RuntimeError):
            broken.check_status()

    unittest.main()
