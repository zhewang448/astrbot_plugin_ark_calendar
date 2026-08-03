from __future__ import annotations

from typing import Any


def config_section(config: Any, name: str) -> dict[str, Any]:
    value = config.get(name, {}) if hasattr(config, "get") else {}
    return value if isinstance(value, dict) else {}


def config_value(
    config: Any,
    section_name: str,
    key: str,
    default: Any,
    legacy_key: str | None = None,
) -> Any:
    section = config_section(config, section_name)
    if key in section:
        return section[key]
    if legacy_key and hasattr(config, "get"):
        return config.get(legacy_key, default)
    return default


def config_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
