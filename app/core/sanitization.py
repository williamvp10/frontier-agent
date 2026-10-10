import re
from collections.abc import Mapping

_SECRET_KEYS = {"password", "token", "authorization", "api_key", "url", "dsn", "image", "base64"}
_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"}


def sanitize(value, secrets: tuple[str, ...] = ()):
    """Defence in depth; event producers must still use allowlisted payloads."""
    if isinstance(value, Mapping):
        return {
            str(k): sanitize(v, secrets)
            for k, v in value.items()
            if (str(k) in _USAGE_KEYS and isinstance(v, int) and not isinstance(v, bool) and v >= 0)
            or not any(part in str(k).lower() for part in _SECRET_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(
            r"(?:https?|wss?|postgresql(?:\+psycopg)?|redis)://\S+", "[redacted-url]", value
        )
        return re.sub(r"data:image/[^;]+;base64,\S+", "[redacted-image]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported]"
