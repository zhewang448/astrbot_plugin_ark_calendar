import unittest
from pathlib import Path

from astrbot_plugin.core.models import CalendarSnapshot, TimelineItem
from astrbot_plugin.core.renderer import CalendarRenderer


class FakePlugin:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    async def html_render(self, template, data, return_url=True, options=None):
        self.calls.append({"return_url": return_url, "options": options})
        if self.error:
            raise self.error
        return b"\x89PNG\r\n\x1a\nrendered"


class RendererErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_direct_image_response(self):
        plugin = FakePlugin()
        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.plugin = plugin

        result = await renderer._html_render("template", {}, {"type": "png"})

        self.assertEqual(result, b"\x89PNG\r\n\x1a\nrendered")
        self.assertFalse(plugin.calls[0]["return_url"])

    async def test_other_render_error_is_preserved(self):
        original = RuntimeError("other render error")
        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.plugin = FakePlugin(original)

        with self.assertRaises(RuntimeError) as raised:
            await renderer._html_render("template", {}, {})

        self.assertIs(raised.exception, original)


class RendererTimeoutTests(unittest.TestCase):
    def test_render_timeout_is_configurable_and_clamped(self):
        class Service:
            @staticmethod
            def value(section, key, default):
                return 75

        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.service = Service()
        self.assertEqual(renderer._render_timeout_ms(), 75000)

        Service.value = staticmethod(lambda section, key, default: 999)
        self.assertEqual(renderer._render_timeout_ms(), 300000)


class HistoryTemplateTests(unittest.TestCase):
    def test_history_template_styles_background_and_portrait_images(self):
        template = (Path(__file__).resolve().parents[1] / "templates" / "history_schedule.html").read_text("utf-8")
        self.assertIn(".bar img.bg", template)
        self.assertIn(".bar .portrait", template)
        self.assertIn("{% elif item.image %}", template)


class HistoricalRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_renders_only_historical_event_and_pool_timelines(self):
        class Assets:
            async def data_uri(self, source):
                return "data:font/otf;base64,Zm9udA=="

        class Gacha:
            @staticmethod
            def label(pool_type):
                return "限定寻访" if pool_type == "LIMITED" else "限时寻访"

        class Service:
            assets = Assets()
            gacha = Gacha()

        plugin = FakePlugin()
        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.plugin = plugin
        renderer.service = Service()
        renderer.history_template = "history-template"

        snapshot = CalendarSnapshot(
            generated_at="2026-08-04T08:00:00+08:00",
            calendar_date="2026-08-04",
            timeline_start="2026-07-01T00:00:00+08:00",
            timeline_end="2026-07-07T23:59:00+08:00",
            events=[TimelineItem("event-1", "历史活动", "event", "活动", "2026-07-02T00:00:00+08:00", "2026-07-05T00:00:00+08:00", image="data:image/png;base64,ZmFrZQ==")],
            gacha_pools=[TimelineItem("pool-1", "历史寻访", "gacha", "限定寻访", "2026-07-01T00:00:00+08:00", "2026-07-06T00:00:00+08:00", image="data:image/png;base64,ZmFrZQ==")],
        )
        result = await renderer.historical_calendar(snapshot)

        self.assertEqual(result, b"\x89PNG\r\n\x1a\nrendered")
        self.assertEqual(plugin.calls[0]["options"]["type"], "png")


if __name__ == "__main__":
    unittest.main()
