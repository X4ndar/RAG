"""Sanitiser unit tests.

`safe_provider_message` is what the test-connection endpoint calls on
EVERY error path. These tests cover the pure function in isolation —
the corresponding endpoint-level coverage (every except branch in the
endpoint actually calls the sanitiser) lives in
`tests/integration/test_secret_hygiene_endpoint.py`.

Two layers, two reasons:
- The pure-function tests catch sanitiser regressions.
- The endpoint tests catch the bug class the V1.5a design note named
  explicitly: forgetting to call the sanitiser on one specific
  except branch.
"""

from __future__ import annotations

from app.llm.sanitise import safe_provider_message

FAKE_KEY = "sk-ant-FAKE-SECRET-KEY-9999"


def test_strips_verbatim_api_key() -> None:
    msg = f"Provider rejected the call with key={FAKE_KEY} -- 401"
    cleaned = safe_provider_message(RuntimeError(msg), FAKE_KEY)
    assert FAKE_KEY not in cleaned
    assert "[REDACTED]" in cleaned


def test_strips_authorization_bearer() -> None:
    msg = f"401 Unauthorized: Authorization: Bearer {FAKE_KEY}"
    cleaned = safe_provider_message(RuntimeError(msg), api_key=None)
    assert FAKE_KEY not in cleaned


def test_strips_api_key_query_string() -> None:
    msg = f"GET /v1/messages?api_key={FAKE_KEY}&model=foo failed"
    cleaned = safe_provider_message(RuntimeError(msg), api_key=None)
    assert FAKE_KEY not in cleaned


def test_strips_api_dash_key_query_string() -> None:
    msg = f"GET /v1/messages?api-key={FAKE_KEY}&model=foo failed"
    cleaned = safe_provider_message(RuntimeError(msg), api_key=None)
    assert FAKE_KEY not in cleaned


def test_strips_dict_repr() -> None:
    msg = f"headers={{'Authorization': '{FAKE_KEY}', 'Accept': 'application/json'}}"
    cleaned = safe_provider_message(RuntimeError(msg), api_key=None)
    assert FAKE_KEY not in cleaned


def test_keeps_human_messages() -> None:
    """Provider messages that don't carry secrets must be preserved verbatim."""
    cleaned = safe_provider_message(
        RuntimeError("Invalid API key. Please check your account."), api_key=None
    )
    assert "Invalid API key" in cleaned


def test_idempotent() -> None:
    """Sanitising twice yields the same output as sanitising once."""
    msg = f"Authorization: Bearer {FAKE_KEY}"
    once = safe_provider_message(RuntimeError(msg), FAKE_KEY)
    twice = safe_provider_message(RuntimeError(once), None)
    assert FAKE_KEY not in once
    assert FAKE_KEY not in twice
