"""TLS compatibility helpers for the Android Tabdeal REST client."""
import os
import ssl

from requests.adapters import HTTPAdapter


class VerifiedTLS12Adapter(HTTPAdapter):
    """Requests adapter pinned to TLS 1.2 with normal CA verification."""

    def __init__(self, cafile=None, **kwargs):
        self.ssl_context = ssl.create_default_context(cafile=cafile)
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def mount_tabdeal_tls_compat(session, cafile=None):
    """Pin only the Tabdeal API host to TLS 1.2; retain certificate validation."""
    session.mount(
        "https://api1.tabdeal.org/",
        VerifiedTLS12Adapter(cafile=cafile),
    )
    return session


def tabdeal_websocket_ssl_options(cafile):
    """Return websocket-client TLS options using an explicit, verified CA bundle."""
    if not isinstance(cafile, str) or not cafile or not os.path.isfile(cafile):
        raise FileNotFoundError("A valid certifi CA bundle is required for WebSocket TLS")
    return {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": cafile}
