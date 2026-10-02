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
        "3ffb5c4b82526f2989a3d8316c67c297d0654216dfadacddb886182a678f2843",
        "a9a674810df4ebba727f104f0a4c7424c15dde880431612511c0f293046b98f2",
    },
    "plain_preview": {
        "cb1348392800d5225faa50e1d7ec9ae136b68feeca5ce805c92eee015654e24e",
        "58077e08119720fde37fec27fa7b13910f2c0bec575bdbc34c334e18357e16a1",
        "42a5d8edb220b5bbfdad1d54217d6c465e8b7cbd4ad26c8f4a319279328d5f1e",
        "fe52d0e354dec3e02614b08e0f41ff44d5f287d1502fa14066ff2d0029f0de6e",
        "703816a6c1723e67dfee1b2694ba134e7e986d139aa537eadade276c8f49f3ac",
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


# 日报栏目，顺序即默认渲染顺序；须与 _conf_schema.json 中 basic.report_sections 的 options/default 一致。
REPORT_SECTIONS = (
    "today_ops", "birthday", "recent_operators",
    "voucher_exchange", "new_skins", "new_modules", "new_stages", "new_furniture",
    "events", "long_term", "pools", "pool_details",
)
# 旧版独立开关（已隐藏）：关闭时对应栏目不出现在日报中。
LEGACY_SECTION_SWITCHES = {
    "include_recent_operators": "recent_operators",
    "include_long_term": "long_term",
    "pool_detail_cards": "pool_details",
}


def _without_legacy_disabled(config: Any, sections: list[str]) -> list[str]:
    disabled = {
        section for key, section in LEGACY_SECTION_SWITCHES.items()
        if not config_value(config, "basic", key, True, key)
    }
    return [section for section in sections if section not in disabled]


def config_report_sections(config: Any) -> list[str]:
    """读取日报栏目及顺序；未知项和重复项忽略，清空时回退默认。"""
    raw = config_section(config, "basic").get("report_sections")
    if not isinstance(raw, list):
        return _without_legacy_disabled(config, list(REPORT_SECTIONS))
    sections = list(dict.fromkeys(item for item in map(str, raw) if item in REPORT_SECTIONS))
    return sections or list(REPORT_SECTIONS)


def migrate_report_sections(config: Any) -> bool:
    """首次加载新配置时把旧版三个显示开关折算进栏目列表；旧开关本身保留不动。"""
    basic = config.get("basic") if hasattr(config, "get") else None
    if not isinstance(basic, dict) or basic.get("report_sections_migrated"):
        return False
    current = basic.get("report_sections")
    basic["report_sections"] = _without_legacy_disabled(
        config, list(current) if isinstance(current, list) else list(REPORT_SECTIONS),
    )
    basic["report_sections_migrated"] = True
    return True


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
    """读取整数配置项，带安全默认值和可选的上下界。"""
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
