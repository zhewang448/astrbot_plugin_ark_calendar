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

    def test_daily_precache_is_defined_and_registered_at_0400(self):
        self.assertIn("_daily_precache", self.methods)
        self.assertIsInstance(self.methods["_daily_precache"], ast.AsyncFunctionDef)

        job = self.methods["_add_daily_precache_job"]
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
        self.assertEqual(getattr(keywords["hour"], "value", None), 4)
        self.assertEqual(getattr(keywords["minute"], "value", None), 0)

    def test_daily_precache_refreshes_then_rebuilds_all_day_images(self):
        task = self.methods["_daily_precache"]
        source = ast.unparse(task)

        self.assertIn("self.service.snapshot(force=True)", source)
        self.assertIn("await self._calendar_image(snapshot)", source)
        self.assertIn("self.help_cache.invalidate()", source)
        self.assertIn("for mode in HelpImageCache.MODES", source)
        self.assertIn("await self._render_help_image(mode, snapshot)", source)


if __name__ == "__main__":
    unittest.main()
