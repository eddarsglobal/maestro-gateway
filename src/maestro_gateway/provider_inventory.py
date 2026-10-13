"""Vendor-neutral local runtime inventory.

Online compatible runtimes expose executable candidates, subject to governed
registration. A discovered model is never execution authorization or proof
of task/language quality. Offline installed software is never auto-started.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from .ollama import _get_json, discover_ollama


# Bootstrap *probes*, not an inventory of installed providers.  Additional
# local OpenAI-compatible endpoints may be added via the optional JSON env var.
_LOCAL_PROBES = (
    ("lmstudio", "LM Studio", "http://127.0.0.1:1234"),
    ("llamacpp", "llama.cpp compatible", "http://127.0.0.1:8080"),
    ("mlx", "MLX compatible", "http://127.0.0.1:8000"),
)


def _loopback_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/", "/v1"}
        ):
            return None
    except ValueError:
        return None
    normalized = value.strip().rstrip("/").removesuffix("/v1")
    return normalized.replace("localhost", "127.0.0.1") if parsed.hostname == "localhost" else normalized


def _extra_probes() -> tuple[tuple[str, str, str], ...]:
    raw = os.getenv("MAESTRO_LOCAL_PROVIDER_ENDPOINTS", "").strip()
    if not raw:
        return ()
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(entries, list):
        return ()
    rows = []
    for item in entries[:24]:
        if not isinstance(item, dict):
            continue
        url = _loopback_url(item.get("url"))
        name = item.get("name")
        identifier = item.get("id")
        if (
            url
            and isinstance(name, str)
            and name.strip()
            and len(name) <= 80
            and isinstance(identifier, str)
            and identifier.isascii()
            and identifier.replace("-", "").replace("_", "").isalnum()
            and 1 <= len(identifier) <= 64
            and identifier not in {"ollama", "lmstudio", "llamacpp", "mlx"}
        ):
            rows.append((identifier, name.strip(), url))
    return tuple(rows)


def _discover_openai(name: str, identifier: str, base_url: str) -> dict[str, Any] | None:
    try:
        payload = _get_json(base_url + "/v1/models", timeout=0.35)
        if not isinstance(payload, dict):
            return None
        entries = payload.get("data")
        if not isinstance(entries, list):
            return None
        models = sorted({
            item["id"] for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and len(item["id"]) <= 200
            and not any(ord(c) < 32 for c in item["id"])
        })[:128]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return {
        "id": identifier,
        "name": name,
        "status": "online",
        "url": base_url,
        "models": models,
        "inference_supported": True,
        "selection_enabled": bool(models),
        "reason": "OpenAI-compatible local chat adapter; capabilities need qualification",
    }


def _is_installed(identifier: str) -> bool:
    binaries = {
        "ollama": ("ollama",),
        "llamacpp": ("llama-server", "llama.cpp"),
        "mlx": ("mlx_lm.server", "mlx_lm"),
        "lmstudio": ("lms",),
    }
    apps = {
        "ollama": ("Ollama.app",),
        "lmstudio": ("LM Studio.app",),
    }
    if any(shutil.which(cmd) for cmd in binaries.get(identifier, ())):
        return True
    if sys.platform == "darwin":
        for app in apps.get(identifier, ()):
            if (Path('/Applications') / app).is_dir() or (Path.home()/'Applications'/app).is_dir():
                return True
    return False


def discover_runtime_inventory() -> list[dict[str, Any]]:
    """Probe all configured peers concurrently, with no vendor-first gating.

    Endpoint checks are passive; status is not permission to execute. Identical
    URLs are probed once, avoiding false duplicate model resources.
    """
    # Explicit user-declared loopback endpoints resolve collisions against
    # standard bootstrap port guesses. This is user intent, NOT vendor rank.
    probes = list(_extra_probes())
    probes += [("ollama", "Ollama", "http://127.0.0.1:11434")]
    probes += list(_LOCAL_PROBES)
    seen_urls: set[str] = set()
    unique = []
    seen_ids = set()
    for identifier, name, url in probes:
        if identifier in seen_ids or url in seen_urls:
            continue
        seen_ids.add(identifier)
        seen_urls.add(url)
        unique.append((identifier, name, url))

    def probe(item: tuple[str, str, str]) -> dict[str, Any]:
        identifier, name, url = item
        if identifier == "ollama":
            data = discover_ollama()
            return {**data, "name": name, "installed": _is_installed(identifier), "inference_supported": True,
                    "selection_enabled": data.get("status") == "online" and bool(data.get("models")),
                    "reason": "Ollama local chat adapter"}
        detected = _discover_openai(name, identifier, url)
        if detected is not None:
            detected["installed"] = _is_installed(identifier)
            return detected
        return {
            "id": identifier, "name": name, "url": url, "status": "offline",
            "models": [], "inference_supported": True, "selection_enabled": False,
            "reason": "Installed but not running" if _is_installed(identifier) else "No responding OpenAI-compatible /v1/models endpoint",
            "installed": _is_installed(identifier),
        }

    with ThreadPoolExecutor(max_workers=max(1, len(unique))) as pool:
        discovered = list(pool.map(probe, unique))
    return sorted(discovered, key=lambda e: e["id"])
