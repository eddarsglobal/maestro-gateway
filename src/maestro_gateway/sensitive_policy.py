from __future__ import annotations

from pathlib import Path


DENIED_NAMES = frozenset({
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
})


DENIED_SUFFIXES = frozenset({
    ".pem",
    ".key",
    ".p12",
    ".pfx",
})


def is_sensitive_path(
    path: Path | str,
) -> bool:
    value = Path(path)

    name = value.name.casefold()

    if name in DENIED_NAMES:
        return True

    if (
        value.suffix.casefold()
        in DENIED_SUFFIXES
    ):
        return True

    return False
