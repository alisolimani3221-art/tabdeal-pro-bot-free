"""Encrypted, app-private API credential storage backed by Android Keystore.

There is deliberately no plaintext or non-Android fallback. The only caller
should save credentials after a successful read-only account request.
"""
import base64
import json
import os
import tempfile

_ALIAS = "org.tabdeal.futuresanalyzer.readonly.credentials.v1"
_VERSION = 1


def _java_bytes(value):
    """Return a PyJNIus-supported bytearray for Java byte[] parameters."""
    return bytearray(value)


def _python_bytes(value):
    return bytes((int(item) & 0xFF) for item in value)


def _configure_aes_builder(builder):
    """Configure AES-GCM using PyJNIus varargs, not a nested Python list.

    PyJNIus itself packs positional arguments into the Java String... array.
    Passing a list as one argument creates a nested sequence at the JNI
    boundary and can fail with 'Could not construct a Java String[]'.
    """
    builder.setBlockModes("GCM")
    builder.setEncryptionPaddings("NoPadding")
    return builder


class AndroidKeystoreBackend:
    """Small Android Keystore AES-256-GCM adapter; imports Android APIs lazily."""

    def _classes(self):
        try:
            from jnius import autoclass
        except Exception as exc:
            raise RuntimeError("Android Keystore is unavailable in this runtime") from exc
        return autoclass

    def _key(self, create=False):
        autoclass = self._classes()
        KeyStore = autoclass("java.security.KeyStore")
        store = KeyStore.getInstance("AndroidKeyStore")
        store.load(None, None)
        key = store.getKey(_ALIAS, None)
        if key is None and create:
            KeyGenerator = autoclass("javax.crypto.KeyGenerator")
            KeyProperties = autoclass("android.security.keystore.KeyProperties")
            Builder = autoclass("android.security.keystore.KeyGenParameterSpec$Builder")
            purposes = (KeyProperties.PURPOSE_ENCRYPT |
                        KeyProperties.PURPOSE_DECRYPT)
            builder = Builder(_ALIAS, purposes)
            # Pass each vararg as a scalar string. PyJNIus creates String[]
            # from positional varargs; do not pass a nested Python list.
            _configure_aes_builder(builder)
            builder.setKeySize(256)
            builder.setRandomizedEncryptionRequired(True)
            generator = KeyGenerator.getInstance("AES", "AndroidKeyStore")
            generator.init(builder.build())
            generator.generateKey()
            key = store.getKey(_ALIAS, None)
        if key is None:
            raise RuntimeError("The device encryption key is missing")
        return store, key

    def encrypt(self, plaintext):
        autoclass = self._classes()
        Cipher = autoclass("javax.crypto.Cipher")
        cipher = Cipher.getInstance("AES/GCM/NoPadding")
        _store, key = self._key(create=True)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        encrypted = cipher.doFinal(_java_bytes(plaintext))
        return _python_bytes(cipher.getIV()), _python_bytes(encrypted)

    def decrypt(self, iv, ciphertext):
        autoclass = self._classes()
        Cipher = autoclass("javax.crypto.Cipher")
        GCMParameterSpec = autoclass("javax.crypto.spec.GCMParameterSpec")
        cipher = Cipher.getInstance("AES/GCM/NoPadding")
        _store, key = self._key(create=False)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, _java_bytes(iv)))
        return _python_bytes(cipher.doFinal(_java_bytes(ciphertext)))

    def delete_key(self):
        autoclass = self._classes()
        KeyStore = autoclass("java.security.KeyStore")
        store = KeyStore.getInstance("AndroidKeyStore")
        store.load(None, None)
        if store.containsAlias(_ALIAS):
            store.deleteEntry(_ALIAS)


class CredentialStore:
    """Stores only authenticated ciphertext in the app's private data directory."""

    def __init__(self, path, backend=None):
        self.path = path
        self.backend = backend or AndroidKeystoreBackend()

    def save(self, api_key, api_secret):
        if not api_key or not api_secret:
            raise ValueError("Both API credentials are required")
        plaintext = json.dumps(
            {"api_key": str(api_key), "api_secret": str(api_secret)},
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        iv, ciphertext = self.backend.encrypt(plaintext)
        envelope = {
            "version": _VERSION,
            "algorithm": "AES-256-GCM",
            "iv": base64.b64encode(iv).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            # A successful write is not enough: verify that the exact file can
            # be read back and authenticated before the UI reports persistence.
            if self.load() != (str(api_key), str(api_secret)):
                raise RuntimeError("Credential persistence verification failed")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                envelope = json.load(stream)
            if envelope.get("version") != _VERSION or envelope.get("algorithm") != "AES-256-GCM":
                raise ValueError("Unsupported encrypted credential format")
            iv = base64.b64decode(envelope["iv"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            data = json.loads(self.backend.decrypt(iv, ciphertext).decode("utf-8"))
            key, secret = data["api_key"], data["api_secret"]
            if not isinstance(key, str) or not key or not isinstance(secret, str) or not secret:
                raise ValueError("Invalid decrypted credentials")
            return key, secret
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise RuntimeError("Saved credentials could not be safely decrypted") from exc

    def check_status(self):
        """Check encrypted credential availability without returning secret values."""
        return "SAVED" if self.load() is not None else "MISSING"

    def delete(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        self.backend.delete_key()
