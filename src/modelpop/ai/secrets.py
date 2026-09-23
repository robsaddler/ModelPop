"""Where API keys live.

Keys go in the operating system's credential store, never in a config file, a
log line or the repository. The user brings their own key; losing it for them,
or leaking it into a crash report, would be a poor way to repay that.

The store is deliberately dull. It stores, fetches and deletes, and it never
returns a key in anything that might be printed.
"""

from __future__ import annotations

import contextlib
import os
from typing import Protocol, runtime_checkable

__all__ = ["EnvironmentSecretStore", "KeyringSecretStore", "SecretStore", "default_store"]

_SERVICE = "ModelPop"


@runtime_checkable
class SecretStore(Protocol):
    """Somewhere to keep an API key."""

    def get(self, name: str) -> str | None:
        """Fetch a secret, or ``None`` when it is not set."""
        ...

    def set(self, name: str, value: str) -> None:
        """Store a secret."""
        ...

    def delete(self, name: str) -> None:
        """Remove a secret. Deleting something absent is not an error."""
        ...

    def describe(self) -> str:
        """Where secrets are being kept, for the settings panel."""
        ...


class KeyringSecretStore:
    """The OS credential store: Credential Manager, Keychain, Secret Service."""

    def __init__(self, service: str = _SERVICE) -> None:
        """Create a store under a service name."""
        self._service = service

    def get(self, name: str) -> str | None:
        """Fetch a secret from the credential store."""
        import keyring

        try:
            return keyring.get_password(self._service, name)
        except Exception:
            return None

    def set(self, name: str, value: str) -> None:
        """Store a secret.

        Raises:
            RuntimeError: if the credential store refuses. The caller should
                tell the user rather than silently continue without a key.
        """
        import keyring

        try:
            keyring.set_password(self._service, name, value)
        except Exception as exc:
            raise RuntimeError(f"could not save to the credential store: {exc}") from exc

    def delete(self, name: str) -> None:
        """Remove a secret, ignoring one that was never there."""
        import keyring

        with contextlib.suppress(Exception):
            # deleting something that was never there is fine
            keyring.delete_password(self._service, name)

    def describe(self) -> str:
        """Where this store keeps things."""
        return "the operating system credential store"


class EnvironmentSecretStore:
    """Read-only store backed by environment variables.

    For CI, for a developer who already exports ``ANTHROPIC_API_KEY``, and for
    tests. Writing is refused rather than silently dropped, so nobody believes a
    key was saved when it was not.
    """

    def get(self, name: str) -> str | None:
        """Read a secret from the environment."""
        return os.environ.get(name) or None

    def set(self, name: str, value: str) -> None:
        """Always refuses.

        Raises:
            RuntimeError: always. Pretending to save would be worse.
        """
        raise RuntimeError(
            "keys cannot be saved to the environment from inside the application; "
            "set the variable in your shell, or switch to the credential store"
        )

    def delete(self, name: str) -> None:
        """Refuses, for the same reason as :meth:`set`."""
        raise RuntimeError("keys cannot be removed from the environment here")

    def describe(self) -> str:
        """Where this store reads from."""
        return "environment variables"


class LayeredSecretStore:
    """Read from the environment first, then the credential store.

    An exported variable should win, because that is what a developer running
    the app from a terminal expects. Writes always go to the credential store.
    """

    def __init__(self, store: SecretStore | None = None) -> None:
        """Wrap a writable store with an environment lookup in front."""
        self._environment = EnvironmentSecretStore()
        self._store = store or KeyringSecretStore()

    def get(self, name: str) -> str | None:
        """Environment first, then the credential store."""
        return self._environment.get(name) or self._store.get(name)

    def set(self, name: str, value: str) -> None:
        """Store a secret in the credential store."""
        self._store.set(name, value)

    def delete(self, name: str) -> None:
        """Remove a secret from the credential store."""
        self._store.delete(name)

    def describe(self) -> str:
        """Where this store looks."""
        return f"environment variables, then {self._store.describe()}"

    def source_of(self, name: str) -> str:
        """Which layer a secret came from, so the settings panel can say so.

        Worth showing: a user who has saved a key and also exported one needs to
        know which is actually being used.
        """
        if self._environment.get(name):
            return "environment variable"
        if self._store.get(name):
            return self._store.describe()
        return "not set"


def default_store() -> LayeredSecretStore:
    """The store the application uses."""
    return LayeredSecretStore()
