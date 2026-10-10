"""Credentials are handles for transport, never model inputs or trace payloads."""

import hashlib
import hmac
import json
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet


class CredentialCipher:
    def __init__(self, encryption_key: str, destination_hmac_key: str, key_version: str = "v1"):
        self._fernet = Fernet(encryption_key.encode())
        self._hmac_key = destination_hmac_key.encode()
        if len(self._hmac_key) < 32:
            raise ValueError("Destination HMAC key must contain at least 32 characters")
        self.key_version = key_version

    def encrypt(self, *, url: str, password: str) -> bytes:
        return self._fernet.encrypt(json.dumps({"url": url, "password": password}).encode())

    def decrypt(self, payload: bytes) -> dict[str, str]:
        return json.loads(self._fernet.decrypt(payload))

    def destination_key(self, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().encode("idna").decode()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port or 443
        authority = host if port == 443 else f"{host}:{port}"
        # The websocket endpoint is the destination, including its query routing.
        normalized = urlunsplit(("wss", authority, parts.path or "/", parts.query, ""))
        return hmac.new(self._hmac_key, normalized.encode(), hashlib.sha256).hexdigest()
