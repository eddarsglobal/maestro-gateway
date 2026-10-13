from __future__ import annotations

import os
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ()

def _parse_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    values = tuple(
        item.strip().rstrip("/")
        for item in raw.split(",")
        if item.strip()
    )
    for origin in values:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or not parsed.port
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ValueError("Only explicit HTTP loopback browser origins are allowed.")
    return values

@dataclass(frozen=True, slots=True)
class GatewayConfig:
    host: str
    port: int
    allowed_origins: tuple[str, ...]
    runtime_dir: Path

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        host = os.getenv("MAESTRO_GATEWAY_HOST", "127.0.0.1").strip()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "P0.3 refuses non-loopback MAESTRO_GATEWAY_HOST values."
            )

        port = int(os.getenv("MAESTRO_GATEWAY_PORT", "8787"))
        if not (1 <= port <= 65535):
            raise ValueError("MAESTRO_GATEWAY_PORT must be 1..65535.")

        runtime_dir = Path(
            os.getenv(
                "MAESTRO_RUNTIME_DIR",
                str(Path.home() / ".maestro" / "runtime"),
            )
        ).expanduser()

        return cls(
            host=host,
            port=port,
            allowed_origins=_parse_origins(
                os.getenv("MAESTRO_GATEWAY_ALLOWED_ORIGINS")
            ),
            runtime_dir=runtime_dir,
        )
