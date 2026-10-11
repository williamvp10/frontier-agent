"""Worker-local credential failover; never expose keys in model context or logs."""
import hmac

from pydantic import SecretStr


def is_credit_exhausted(status, body):
    if status != 402:
        return False
    error = body.get("error", body) if isinstance(body, dict) else {}
    metadata = error.get("metadata", {}) if isinstance(error, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    # Temporary concurrency reservations and upstream BYOK billing are not key exhaustion.
    return (metadata.get("limit_source") != "openrouter_in_flight_budget"
            and not metadata.get("is_byok") and not metadata.get("provider_name"))


class OpenRouterKeys:
    def __init__(self, primary: SecretStr, backup: SecretStr | None = None):
        self._keys = (primary,)
        if backup and backup.get_secret_value().strip() and not hmac.compare_digest(
            primary.get_secret_value(), backup.get_secret_value()
        ):
            self._keys += (backup,)
        self.active = 0

    def key(self, index):
        return self._keys[index]

    @property
    def has_backup(self):
        return len(self._keys) == 2

    def failover(self, rejected_index):
        if rejected_index != 0 or not self.has_backup:
            return False
        self.active = 1
        return True

    @property
    def slot(self):
        return "backup" if self.active else "primary"
