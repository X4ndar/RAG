"""At-rest encryption for tenant LLM API keys.

Single chokepoint design. `_load_fernet()` is the **only** code that reads
`settings.llm_config_master_key` and the **only** place that raises
`MasterKeyMissingError`. `encrypt()`, `decrypt()`, and any caller dispatch all
flow through it. A FastAPI exception handler in `app.main` maps the
exception to **503** once for the whole API surface — endpoints don't
duplicate the 503 logic.

Algorithm: `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256, URL-safe
base64-encoded 32-byte key). Per the V1.5a design note, V1.5a uses the
master key directly with no per-tenant HKDF derivation. Rotation
(`LLM_CONFIG_MASTER_KEY_FALLBACK`) is a V1.5b commit.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class MasterKeyMissingError(RuntimeError):
    """`LLM_CONFIG_MASTER_KEY` is unset or malformed.

    Single source of truth: `_load_fernet()` is the only function that
    raises this. Every other module sees the exception through normal
    propagation; the FastAPI handler turns it into a 503.
    """


class DecryptionFailedError(RuntimeError):
    """Fernet refused to decrypt the token.

    Either the row was written under a different master key, or the
    ciphertext was tampered with. Surfaces as a 500 because there is no
    safe user-actionable response — operator intervention required.
    """


def _load_fernet() -> Fernet:
    """The one place that reads the master key. Raises `MasterKeyMissingError`
    if the env var is unset or not a valid Fernet key (wrong length,
    wrong alphabet).

    Re-loaded on each call so a redeployed container with a freshly
    rotated key picks it up without a process restart. The cost is one
    base64 decode per call (~1 µs); not worth caching.
    """
    raw = get_settings().llm_config_master_key
    if not raw:
        raise MasterKeyMissingError(
            "LLM_CONFIG_MASTER_KEY is not set; "
            "the LLM configuration service is unavailable."
        )
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise MasterKeyMissingError(
            "LLM_CONFIG_MASTER_KEY is malformed; "
            "expected a 32-byte URL-safe base64 string."
        ) from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a plaintext string under the master key. Returns the Fernet
    token as bytes (suitable for `bytea` storage).
    """
    return _load_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    """Decrypt a Fernet token. Raises `DecryptionFailedError` on bad token.

    `MasterKeyMissingError` propagates from `_load_fernet()` if the env is
    unset; we don't catch it here because the API-level handler is the
    right place to map it to a status code.
    """
    fernet = _load_fernet()
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionFailedError(
            "Fernet rejected the stored token. Master key may have rotated; "
            "operator intervention required."
        ) from exc


def last4(plaintext: str) -> str:
    """Return the last four characters of a plaintext key for display.

    Empty string in, empty string out (the ollama-no-auth case). Strings
    shorter than four characters return the whole string.
    """
    return plaintext[-4:] if plaintext else ""
