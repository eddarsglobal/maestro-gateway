from __future__ import annotations

import json
import urllib.error
from maestro.providers.ollama.client import OllamaHTTPError
import urllib.request
from typing import Any

OLLAMA_BASE_URL = "http://127.0.0.1:11434"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local runtime must not redirect Gateway discovery to a remote host.
        return None


def _get_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("Local model inventory exceeds 1 MB")
        return json.loads(body.decode("utf-8"))

def discover_ollama() -> dict[str, Any]:
    try:
        payload = _get_json(f"{OLLAMA_BASE_URL}/api/tags", timeout=0.5)
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        OllamaHTTPError,
    ) as exc:
        return {
            "id": "ollama",
            "status": "offline",
            "url": OLLAMA_BASE_URL,
            "models": [],
            "error": type(exc).__name__,
        }

    models = []
    digests = {}
    for item in payload.get("models", []):
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name:
            models.append(name)
            if isinstance(item.get("digest"), str) and len(item["digest"]) >= 16:
                digests[name] = item["digest"][:256]

    return {
        "id": "ollama",
        "status": "online",
        "url": OLLAMA_BASE_URL,
        "models": sorted(set(models)),
        "model_fingerprints": digests,
    }
