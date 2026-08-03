import unittest

from astrbot_plugin.core.renderer import CalendarRenderer


class FakePlugin:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    async def html_render(self, template, data, return_url=True, options=None):
        self.calls.append({"return_url": return_url, "options": options})
        if self.error:
            raise self.error
        return "rendered.png"


class RendererErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_direct_image_response(self):
        plugin = FakePlugin()
        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.plugin = plugin

        result = await renderer._html_render("template", {}, {"type": "png"})

        self.assertEqual(result, "rendered.png")
        self.assertFalse(plugin.calls[0]["return_url"])

    async def test_other_render_error_is_preserved(self):
        original = RuntimeError("other render error")
        renderer = CalendarRenderer.__new__(CalendarRenderer)
        renderer.plugin = FakePlugin(original)

        with self.assertRaises(RuntimeError) as raised:
            await renderer._html_render("template", {}, {})

        self.assertIs(raised.exception, original)


if __name__ == "__main__":
    unittest.main()
