"""Secrets: encryption of stored tokens, API keys, random values and the HTTP security headers.

Adapted from labdex (labdex/security.py), relicensed into mortar (formerly bitsandbricks) under MIT by its author.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

KEY_PREFIX = "bnbo_"


class Vault:
    """Encrypts tokens and secrets at rest (Fernet: AES-128-CBC + HMAC-SHA256) with a key derived from the
    operator's secret key. A different secret key makes every stored token unreadable: users sign in again."""

    def __init__(self, secret_key: str):
        raw = hashlib.sha256(b"bnb-operator-vault:" + secret_key.encode()).digest()
        self._f = Fernet(base64.urlsafe_b64encode(raw))

    def encrypt(self, value: str) -> str:
        return self._f.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, stored: str) -> str:
        if not stored:
            return ""
        try:
            return self._f.decrypt(stored.encode()).decode()
        except InvalidToken:
            return ""


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_api_key() -> tuple[str, str, str]:
    """(full key shown once, lookup prefix, hash to store)."""
    prefix = secrets.token_hex(4)
    full = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return full, prefix, sha256(full)


def key_prefix(full: str) -> str | None:
    """The lookup prefix of an API key, or None if it doesn't look like ours."""
    if not full.startswith(KEY_PREFIX):
        return None
    parts = full[len(KEY_PREFIX):].split("_", 1)
    return parts[0] if len(parts) == 2 and len(parts[0]) == 8 else None


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def user_code() -> str:
    """A code people can type: 8 letters from an alphabet without look-alikes, shown as XXXX-XXXX."""
    alphabet = "BCDFGHJKLMNPQRSTVWXZ"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"


def csp(nonce: str = "") -> str:
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://avatars.githubusercontent.com https://secure.gravatar.com https://gitlab.com",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def headers(secure: bool) -> dict[str, str]:
    h = {
        "Content-Security-Policy": csp(),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    }
    if secure:
        h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return h
