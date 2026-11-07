from __future__ import annotations

import os
from pathlib import Path

from core.memory_provider import MemoryProvider


def resolve_location(
    explicit_location: str = "",
    *,
    vault_path: Path | None = None,
    default: str = "",
) -> str:
    """Resolve location: explicit override, then the remembered `topic:
    location` memory note (set by telling the assistant where you live),
    then the HOME_LOCATION setting, then the caller's default."""
    explicit = str(explicit_location or "").strip()
    if explicit:
        return explicit

    provider = MemoryProvider(vault_path=vault_path, backend="vault")
    remembered = provider.load_topic("location")
    if remembered:
        return remembered

    configured = os.environ.get("HOME_LOCATION", "").strip()
    if configured:
        return configured

    return str(default or "").strip()
