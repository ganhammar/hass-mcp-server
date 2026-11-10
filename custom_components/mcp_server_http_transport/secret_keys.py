"""Which keys name a secret, shared by the audit trail and the tools that echo values."""

from typing import Any

# Keys whose value is masked wherever it would be logged or echoed, matched against the
# lowercased key at any depth: fragments as substrings, names exactly. The exact
# names are the short keys locks and alarm panels take their codes under, which
# are too common as substrings (zip_code, pin_count) to match that way.
_SECRET_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "passcode",
    "pincode",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)
_SECRET_KEY_NAMES = frozenset({"code", "pin"})
SECRET_MASK = "***"


def is_secret_key(key: Any) -> bool:
    """Return True when a key names a value that must not be logged or echoed."""
    lowered = str(key).lower()
    if lowered in _SECRET_KEY_NAMES:
        return True
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)
