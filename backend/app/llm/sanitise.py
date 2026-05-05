"""Strip secrets out of error messages before they reach the UI.

`safe_provider_message(exc, api_key)` is called on **every** error path
of the test-connection endpoint — auth-failed, unreachable, timeout,
catch-all. Forgetting it on one branch is the bug class the unit test
in `tests/unit/test_secret_hygiene.py` watches for.

What we strip:

* Verbatim API key, if it appears in the message (some SDKs include the
  rejected key).
* `Authorization: Bearer <token>` headers (case-insensitive).
* `api_key=...` and `api-key=...` query/form fragments.
* `Authorization` keys in serialised dict reprs (`'Authorization': '...'`).

What we **don't** strip: provider-supplied human-readable messages like
"Invalid API key" or "Rate limit exceeded" — those are exactly the
information the user needs.
"""

from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(?i)\bAuthorization[\s:]*Bearer\s+\S+")
_AUTH_HEADER_RE = re.compile(r"(?i)\bAuthorization[\s:]*\S+")
_API_KEY_QUERY_RE = re.compile(r"(?i)(api[-_]?key)\s*=\s*[^\s&'\"]+")
_AUTH_DICT_RE = re.compile(r"(?i)['\"]Authorization['\"]\s*:\s*['\"][^'\"]+['\"]")

_REDACTED = "[REDACTED]"


def safe_provider_message(exc: BaseException, api_key: str | None = None) -> str:
    """Return a UI-safe rendering of a provider exception.

    Always called from every except branch in the /test endpoint — the
    unit test enforces this contract by exercising every exception type
    we handle and asserting the API key string never survives.
    """
    message = str(exc)

    if api_key:
        message = message.replace(api_key, _REDACTED)

    message = _BEARER_RE.sub(f"Authorization: Bearer {_REDACTED}", message)
    message = _AUTH_DICT_RE.sub(f"'Authorization': '{_REDACTED}'", message)
    message = _API_KEY_QUERY_RE.sub(rf"\1={_REDACTED}", message)
    return _AUTH_HEADER_RE.sub(f"Authorization: {_REDACTED}", message)
