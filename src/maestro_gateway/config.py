from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://maestro.men",
)

def _parse_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    values = tuple(
        item.strip().rstrip("/")
        for item in raw.split(",")
        if item.strip()
    )
    if "*" in values:
        raise ValueError("Wildcard CORS origin '*' is forbidden.")
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
