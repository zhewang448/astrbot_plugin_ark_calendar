from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMANDS = [
    ("CALENDAR_COMMAND", "方舟日历"),
    ("BIRTHDAY_COMMAND", "方舟生日"),
    ("STATUS_COMMAND", "方舟日历状态"),
    ("SUBSCRIBE_COMMAND", "方舟订阅"),
    ("UNSUBSCRIBE_COMMAND", "方舟取消订阅"),
    ("SUBSCRIPTION_LIST_COMMAND", "方舟订阅列表"),
    ("HELP_COMMAND", "方舟日历帮助"),
    ("REFRESH_COMMAND", "方舟日历刷新"),
    ("HISTORICAL_COMMAND", "方舟历史日程测试"),
]


def test_command_specs_and_readme_table_share_the_same_order() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_values: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in dict(EXPECTED_COMMANDS):
            continue
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.args[0], ast.Constant)
        command_values.append((target.id, node.value.args[0].value))
    assert command_values == EXPECTED_COMMANDS

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = readme.split("| 主指令 | 别称 | 权限 | 说明 |", 1)[1].split("\n\n示例：", 1)[0]
    command_rows = [line for line in table.splitlines() if line.startswith("| `/")]
    primary_commands = [line.split("|")[1].strip().strip("`").split(" <", 1)[0] for line in command_rows]
    assert primary_commands == [f"/{name}" for _, name in EXPECTED_COMMANDS]
    assert "| `/方舟日历帮助` | `/方舟日报帮助`、`/明日方舟日报帮助`" in table


def test_readme_centers_the_visitor_counter() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '<p align="center">' in readme
    assert 'count.getloli.com/get/@:astrbot_plugin_ark_calendar' in readme


def test_command_handler_order_matches_the_documented_order() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    plugin = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "ArkCalendarPlugin"
    )
    documented_handlers = [
        "calendar_command", "birthday_command", "status_command",
        "subscribe_command", "unsubscribe_command", "subscription_list_command",
        "help_command", "refresh_command", "historical_schedule_command",
    ]
    actual = [
        node.name
        for node in plugin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in documented_handlers
    ]
    assert actual == documented_handlers
