from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".testdeps"), str(ROOT.parent)]

from jinja2 import Environment

from astrbot_plugin.core.renderer import CalendarRenderer
from astrbot_plugin.core.service import CalendarService


class Logger:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): print("WARN", *args)
    def error(self, *args, **kwargs): print("ERROR", *args)


class PreviewPlugin:
    def __init__(self, output: Path):
        self.output = output

    async def html_render(self, template: str, data: dict, options: dict | None = None) -> str:
        html = Environment(autoescape=False).from_string(template).render(**data)
        name = "calendar-rendered.html" if "方舟日历" in template else "birthday-rendered.html"
        path = self.output / name
        path.write_text('<!doctype html><html><head><meta charset="utf-8"></head><body>' + html + '</body></html>', "utf-8")
        return str(path)


async def main() -> None:
    output = ROOT / "preview"
    service = CalendarService(
        ROOT,
        output / "runtime_data",
        {"show_source_footer": True},
        Logger(),
    )
    service._now = lambda: datetime(2026, 8, 2, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    await service.initialize()
    try:
        snapshot = await service.snapshot()
        renderer = CalendarRenderer(PreviewPlugin(output), service)
        print(await renderer.calendar(snapshot))
        if snapshot.today_birthdays:
            print(await renderer.birthday(snapshot.today_birthdays[0]))
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
