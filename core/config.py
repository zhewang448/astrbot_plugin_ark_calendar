from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


LEGACY_BUILTIN_MESSAGE_PREVIEW_HASHES = {
    "rhodes_catgirl_preview": {
        "624f6711c302857e3641bd098747396bbfc4288f6c56954a6fc3610a5fc17dd9",
        "7dbc988370b6d56da79c8be8abff922b6e0b4a0ee362f8e999f23b00ab45645b",
        "c5890db25a8d1797fef3bd0374d35d4cce2990639e5c17da4d4d71225a2631ac",
    },
    "plain_preview": {
        "cb1348392800d5225faa50e1d7ec9ae136b68feeca5ce805c92eee015654e24e",
        "58077e08119720fde37fec27fa7b13910f2c0bec575bdbc34c334e18357e16a1",
        "42a5d8edb220b5bbfdad1d54217d6c465e8b7cbd4ad26c8f4a319279328d5f1e",
    },
}


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


def config_int(
    config: Any,
    section_name: str,
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    legacy_key: str | None = None,
) -> int:
    """Read an integer setting with a safe default and optional bounds."""
    raw = config_value(config, section_name, key, default, legacy_key)
    try:
        if isinstance(raw, bool):
            raise ValueError("boolean is not an integer setting")
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

def sync_builtin_message_previews(config: Any, schema_path: Path) -> bool:
    """仅把历史内置预览迁移为当前 Schema 默认值。"""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    message_items = schema["messages"]["items"]
    messages = config.get("messages") if hasattr(config, "get") else None
    if not isinstance(messages, dict):
        return False

    changed = False
    for key, legacy_hashes in LEGACY_BUILTIN_MESSAGE_PREVIEW_HASHES.items():
        stored_preview = messages.get(key)
        if not isinstance(stored_preview, str):
            continue
        stored_hash = hashlib.sha256(stored_preview.encode("utf-8")).hexdigest()
        if stored_hash in legacy_hashes:
            messages[key] = message_items[key]["default"]
            changed = True
    return changed
