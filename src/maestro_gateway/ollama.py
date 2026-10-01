from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

OLLAMA_BASE_URL = "http://127.0.0.1:11434"

def _get_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def discover_ollama() -> dict[str, Any]:
    try:
        payload = _get_json(f"{OLLAMA_BASE_URL}/api/tags")
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ) as exc:
        return {
            "id": "ollama",
            "status": "offline",
            "url": OLLAMA_BASE_URL,
            "models": [],
            "error": type(exc).__name__,
        }

    models = []
    for item in payload.get("models", []):
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name:
            models.append(name)

    return {
        "id": "ollama",
        "status": "online",
        "url": OLLAMA_BASE_URL,
        "models": sorted(set(models)),
    }
