from core.config import REPORT_SECTIONS, config_report_sections, migrate_report_sections
from core.renderer import CalendarRenderer


def test_migration_inherits_disabled_legacy_switches_once():
    config = {"basic": {
        "report_sections": list(REPORT_SECTIONS), "report_sections_migrated": False,
        "include_recent_operators": False, "include_long_term": True, "pool_detail_cards": False,
    }}

    assert migrate_report_sections(config)
    assert "recent_operators" not in config_report_sections(config)
    assert "pool_details" not in config_report_sections(config)
    assert "long_term" in config_report_sections(config)
    # 迁移后旧开关不再生效，用户重新加回栏目即可显示。
    config["basic"]["report_sections"].append("recent_operators")
    assert not migrate_report_sections(config)
    assert config_report_sections(config)[-1] == "recent_operators"


def test_sections_ignore_unknown_and_fall_back_when_empty():
    assert config_report_sections({"basic": {"report_sections": ["pools", "bogus", "pools", "birthday"]}}) == ["pools", "birthday"]
    assert config_report_sections({"basic": {"report_sections": []}}) == list(REPORT_SECTIONS)


def test_adjacent_highlights_and_long_term_share_one_block():
    groups = CalendarRenderer._section_groups(list(REPORT_SECTIONS))

    assert ["voucher_exchange", "new_skins", "new_modules"] in groups
    assert ["events", "long_term"] in groups
    assert CalendarRenderer._section_groups(["new_skins", "pools", "long_term"]) == [["new_skins"], ["pools"], ["long_term"]]
