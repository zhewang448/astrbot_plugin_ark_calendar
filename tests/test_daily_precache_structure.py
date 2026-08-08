from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DailyPrecacheStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "main.py").read_text("utf-8"))
        cls.plugin = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ArkCalendarPlugin"
        )
        cls.methods = {
            node.name: node
            for node in cls.plugin.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_startup_precache_is_scheduled_without_awaiting_it(self):
        initialize = self.methods["initialize"]
        source = ast.unparse(initialize)
        self.assertIn("asyncio.create_task", source)
        self.assertIn("self._precache_help_images_after_reload()", source)

    def test_startup_precache_only_renders_missing_modes(self):
        task = self.methods["_precache_help_images_after_reload"]
        source = ast.unparse(task)
        self.assertIn("self.help_cache.lookup(mode)", source)
        self.assertIn("self.service.snapshot()", source)
        self.assertIn("await self._render_help_image(mode, snapshot)", source)
        self.assertIn("asyncio.CancelledError", source)

    def test_daily_precache_is_defined_and_registered_at_configured_time(self):
        self.assertIn("_daily_precache", self.methods)
        self.assertIsInstance(self.methods["_daily_precache"], ast.AsyncFunctionDef)

        job = self.methods["_add_daily_precache_job"]
        source = ast.unparse(job)
        # 时间来自配置项，默认值仍是 04:00（对齐游戏日切）。
        self.assertIn("daily_precache_time", source)
        self.assertIn("'04:00'", source)
        # 必须先避开定时日报再落库，否则两个任务会并发强制刷新并抢渲染。
        self.assertIn("self._avoid_report_collision(", source)

        add_job_calls = [
            node for node in ast.walk(job)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ]
        self.assertEqual(len(add_job_calls), 1)
        call = add_job_calls[0]
        self.assertIsInstance(call.args[0], ast.Attribute)
        self.assertEqual(call.args[0].attr, "_daily_precache")
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        # hour/minute 由配置时间解析而来，不再是字面量。
        self.assertIsInstance(keywords["hour"], ast.Name)
        self.assertEqual(keywords["hour"].id, "hour")
        self.assertIsInstance(keywords["minute"], ast.Name)
        self.assertEqual(keywords["minute"].id, "minute")

    def test_daily_precache_refreshes_then_rebuilds_all_day_images(self):
        task = self.methods["_daily_precache"]
        source = ast.unparse(task)

        # 必须取本次刷新结果：全局 last_refresh_* 会被并发任务覆盖，
        # 据此判定会把别的任务的故障算到预缓存头上。
        self.assertIn("self.service.snapshot_with_outcome(force=True)", source)
        self.assertIn("await self._calendar_image(snapshot)", source)
        self.assertIn("self.help_cache.invalidate()", source)
        self.assertIn("for mode in HelpImageCache.MODES", source)
        self.assertIn("await self._render_help_image(mode, snapshot)", source)
        self.assertIn("await self._observe_health(outcome, '每日预缓存')", source)

    def test_help_image_failure_only_logs_and_never_alerts_admin(self):
        """帮助图失败是缓存预热问题，按需重渲染能自愈，不该惊动管理员。"""
        task = self.methods["_daily_precache"]
        notify_calls = [
            node for node in ast.walk(task)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_notify_admin"
        ]
        # 只保留 except 块里那一次整体失败告警。
        self.assertEqual(len(notify_calls), 1)
        keys = [
            node.value for node in ast.walk(notify_calls[0])
            if isinstance(node, ast.Constant) and node.value == "daily_precache_help_failed"
        ]
        self.assertEqual(keys, [])

    def test_precache_avoids_report_collision_by_shifting_not_skipping(self):
        """撞车必须顺延而不是放弃建任务。

        预缓存里的 help_cache.invalidate() 是帮助长图当天唯一的重渲染点，
        任务不建，00:00-04:00 生成的帮助图会带着"还没结束"的活动留一整天。
        """
        avoid = self.methods["_avoid_report_collision"]
        source = ast.unparse(avoid)
        self.assertIn("self._scheduled_report_times()", source)
        self.assertIn("PRECACHE_SHIFT_MINUTES", source)
        self.assertIn("PRECACHE_SHIFT_ATTEMPTS", source)
        # 顺延要跨午夜回绕，不能算出 24:xx 这种非法时间。
        self.assertIn("24 * 60", source)


if __name__ == "__main__":
    unittest.main()
