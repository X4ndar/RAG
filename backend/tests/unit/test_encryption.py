"""Unit tests for the LLM-config encryption module.

Covers the four guarantees from the V1.5a design note:
    * Round-trip: encrypt → decrypt yields the original plaintext.
    * Tamper-resistance: decrypt with a different master key fails loud
      (DecryptionFailedError), no silent garbage.
    * Empty plaintext round-trips cleanly (the ollama-no-auth case).
    * MasterKeyMissingError is raised by the SOLE chokepoint when the env var
      is unset or malformed; nothing else in the module raises it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.llm import encryption
from app.llm.encryption import DecryptionFailedError, MasterKeyMissingError, decrypt, encrypt, last4


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Settings is `@lru_cache(maxsize=1)`; clear it so each test reads
    the env var afresh."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    if key is None:
        monkeypatch.delenv("LLM_CONFIG_MASTER_KEY", raising=False)
    else:
        monkeypatch.setenv("LLM_CONFIG_MASTER_KEY", key)


def test_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, Fernet.generate_key().decode())
    plaintext = "sk-ant-something-secret-9F4d"
    token = encrypt(plaintext)
    assert isinstance(token, bytes)
    assert decrypt(token) == plaintext


def test_round_trip_unicode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, Fernet.generate_key().decode())
    plaintext = "clé-secrète-éàü"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_empty_plaintext_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, Fernet.generate_key().decode())
    assert decrypt(encrypt("")) == ""


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, Fernet.generate_key().decode())
    token = encrypt("real-secret")
    # Rotate to a different key; old token must not silently decode.
    _set_key(monkeypatch, Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(DecryptionFailedError):
        decrypt(token)


def test_missing_env_var_raises_master_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, None)
    with pytest.raises(MasterKeyMissingError):
        encrypt("anything")
    with pytest.raises(MasterKeyMissingError):
        decrypt(b"anything")


def test_malformed_key_raises_master_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "not-a-valid-fernet-key")
    with pytest.raises(MasterKeyMissingError):
        encrypt("anything")


def test_master_key_missing_chokepoint_is_load_fernet() -> None:
    """The module's public functions must raise MasterKeyMissingError only
    through `_load_fernet`. Two raise sites are allowed inside that function
    (one for "unset", one for "malformed"); zero raises are allowed in
    `encrypt`, `decrypt`, or `last4`.

    Endpoints rely on the FastAPI handler to map this exception to 503;
    duplicating the raise across the public surface would defeat that.
    """
    import inspect

    src_load = inspect.getsource(encryption._load_fernet)
    src_encrypt = inspect.getsource(encryption.encrypt)
    src_decrypt = inspect.getsource(encryption.decrypt)
    src_last4 = inspect.getsource(encryption.last4)

    assert "raise MasterKeyMissingError" in src_load
    for name, src in (("encrypt", src_encrypt), ("decrypt", src_decrypt), ("last4", src_last4)):
        assert "raise MasterKeyMissingError" not in src, (
            f"{name}() must not raise MasterKeyMissingError directly; "
            "let _load_fernet be the single chokepoint."
        )


def test_last4_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    assert last4("sk-ant-9F4d") == "9F4d"
    assert last4("") == ""
    assert last4("ab") == "ab"  # shorter than 4 chars → return as-is
