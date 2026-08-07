"""tests/test_command_args.py — 订阅命令参数解析器单元测试"""
from __future__ import annotations

import unittest
from astrbot_plugin.core.command_args import (
    parse_hhmm,
    split_name_and_time,
    strip_command_prefix,
)


class TestParseHhmm(unittest.TestCase):

    def test_normal(self):
        self.assertEqual("12:00", parse_hhmm("12:00"))
        self.assertEqual("09:30", parse_hhmm("9:30"))
        self.assertEqual("00:00", parse_hhmm("0:00"))
        self.assertEqual("23:59", parse_hhmm("23:59"))

    def test_fullwidth_colon(self):
        self.assertEqual("09:00", parse_hhmm("09：00"))

    def test_leading_whitespace(self):
        self.assertEqual("12:00", parse_hhmm("  12:00  "))

    def test_invalid(self):
        self.assertIsNone(parse_hhmm(""))
        self.assertIsNone(parse_hhmm("24:00"))
        self.assertIsNone(parse_hhmm("12:60"))
        self.assertIsNone(parse_hhmm("abc"))
        self.assertIsNone(parse_hhmm("12:0"))   # 分钟需要两位
        self.assertIsNone(parse_hhmm("·"))
        self.assertIsNone(parse_hhmm("感谢庆典"))


class TestSplitNameAndTime(unittest.TestCase):

    # ── 无时间 ──────────────────────────────────────────────────────────

    def test_simple_name(self):
        name, t = split_name_and_time("感谢庆典")
        self.assertEqual("感谢庆典", name)
        self.assertIsNone(t)

    def test_name_with_spaces(self):
        name, t = split_name_and_time("危机合约 · 熔火行动")
        self.assertEqual("危机合约 · 熔火行动", name)
        self.assertIsNone(t)

    def test_middle_dot_variant(self):
        name, t = split_name_and_time("中坚甄选·第十二期")
        self.assertEqual("中坚甄选·第十二期", name)
        self.assertIsNone(t)

    # ── 有时间 ──────────────────────────────────────────────────────────

    def test_name_plus_time(self):
        name, t = split_name_and_time("感谢庆典 09:30")
        self.assertEqual("感谢庆典", name)
        self.assertEqual("09:30", t)

    def test_name_with_spaces_plus_time(self):
        """修复前此测试失败：活动名带空格时时间被框架切给第二个参数，现在整段解析。"""
        name, t = split_name_and_time("危机合约 · 熔火行动 20:30")
        self.assertEqual("危机合约 · 熔火行动", name)
        self.assertEqual("20:30", t)

    def test_fullwidth_colon_in_time(self):
        name, t = split_name_and_time("感谢庆典 09：00")
        self.assertEqual("感谢庆典", name)
        self.assertEqual("09:00", t)

    def test_leading_zero_hour(self):
        name, t = split_name_and_time("感谢庆典 8:30")
        self.assertEqual("感谢庆典", name)
        self.assertEqual("08:30", t)

    # ── 空输入 ──────────────────────────────────────────────────────────

    def test_empty(self):
        name, t = split_name_and_time("")
        self.assertEqual("", name)
        self.assertIsNone(t)

    def test_only_time(self):
        """只给了时间没给名称 → 名称为空，调用方走帮助分支。"""
        name, t = split_name_and_time("12:00")
        self.assertEqual("", name)
        self.assertEqual("12:00", t)

    # ── 末尾 token 不是时间 ─────────────────────────────────────────────

    def test_dot_at_end_is_not_time(self):
        """「·」不是合法时间，应被归入名称。"""
        name, t = split_name_and_time("危机合约 ·")
        self.assertEqual("危机合约 ·", name)
        self.assertIsNone(t)


class TestStripCommandPrefix(unittest.TestCase):

    INVOCATIONS = ("方舟订阅", "订阅方舟活动", "订阅卡池")

    def test_slash_prefix(self):
        result = strip_command_prefix("/方舟订阅 感谢庆典", self.INVOCATIONS)
        self.assertEqual("感谢庆典", result)

    def test_exclamation_prefix(self):
        result = strip_command_prefix("!方舟订阅 感谢庆典", self.INVOCATIONS)
        self.assertEqual("感谢庆典", result)

    def test_alias(self):
        result = strip_command_prefix("/订阅方舟活动 危机合约 · 熔火行动", self.INVOCATIONS)
        self.assertEqual("危机合约 · 熔火行动", result)

    def test_no_args(self):
        result = strip_command_prefix("/方舟订阅", self.INVOCATIONS)
        self.assertEqual("", result)

    def test_fullwidth_space(self):
        result = strip_command_prefix("/方舟订阅　感谢庆典", self.INVOCATIONS)
        self.assertEqual("感谢庆典", result)

    def test_longer_alias_wins(self):
        """「订阅方舟活动」比「订阅」更长，确保不会被短名截断。"""
        invocations = ("订阅", "订阅方舟活动")
        result = strip_command_prefix("/订阅方舟活动 感谢庆典", invocations)
        self.assertEqual("感谢庆典", result)


if __name__ == "__main__":
    unittest.main()
