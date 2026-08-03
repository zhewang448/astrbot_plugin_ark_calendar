from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".testdeps"), str(ROOT.parent)]

from astrbot_plugin.core.service import CalendarService


class Logger:
    def info(self, *args, **kwargs): print("INFO", *args)
    def warning(self, *args, **kwargs): print("WARN", *args)
    def error(self, *args, **kwargs): print("ERROR", *args)


async def main() -> None:
    output = ROOT / "preview"
    output.mkdir(exist_ok=True)
    service = CalendarService(
        ROOT,
        output / "runtime_data",
        {
            "request_timeout": 35,
            "cache_ttl_minutes": 30,
            "timeline_days": 28,
            "include_recent_operators": True,
            "include_long_term": True,
            "show_source_footer": True,
        },
        Logger(),
    )
    service._now = lambda: datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    await service.initialize()
    try:
        snapshot = await service.snapshot(force=True)
        summary = {
            "generated_at": snapshot.generated_at,
            "today_birthdays": [item.name for item in snapshot.today_birthdays],
            "upcoming_birthdays": [
                {"date": f"{group.month:02d}-{group.day:02d}", "operators": [item.name for item in group.operators]}
                for group in snapshot.upcoming_birthdays
            ],
            "supplies": snapshot.today_info.supplies,
            "chips": snapshot.today_info.chips,
            "alerts": snapshot.today_info.alerts,
            "recent_operators": [item.name for item in snapshot.recent_operators],
            "events": [
                {"name": item.name, "start": item.start, "end": item.end, "type": item.item_type, "long": item.is_long_term}
                for item in snapshot.events + snapshot.long_term_events
            ],
            "gacha": [
                {"name": item.name, "start": item.start, "end": item.end, "type": item.item_type, "six": item.six_star_up}
                for item in snapshot.gacha_pools
            ],
            "source_states": [
                {"name": item.name, "ok": item.ok, "message": item.message}
                for item in snapshot.source_states
            ],
        }
        (output / "snapshot-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
