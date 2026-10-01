"""Resolve integrations.api_key_ref to a secret. Keys never live in the DB."""

from __future__ import annotations

import os


class SecretMissing(Exception):
    pass


def resolve(ref: str) -> str:
    """Supported refs: "env:VAR_NAME". Add a Vault/KMS scheme here when needed."""
    scheme, _, name = ref.partition(":")
    if scheme == "env" and name:
        value = os.environ.get(name)
        if value:
            return value
        raise SecretMissing(f"env var {name} is not set")
    raise SecretMissing(f"unsupported secret ref scheme: {scheme!r}")
