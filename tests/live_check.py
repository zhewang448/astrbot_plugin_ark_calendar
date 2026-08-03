from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".testdeps"), str(ROOT.parent)]

import aiohttp

from astrbot_plugin.sources.anything_ics import AnythingIcsSource
from astrbot_plugin.sources.gacha import GachaSource
from astrbot_plugin.sources.http import HttpClient
from astrbot_plugin.sources.prts import PrtsSource


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "AstrBot-ArkCalendar-Test/0.1"}) as session:
        http = HttpClient(session, retries=0)
        anything = AnythingIcsSource(http, "https://proxy.avgt.ink/ics")
        prts = PrtsSource(http, "https://prts.wiki")
        gacha = GachaSource(http, "https://raw.githubusercontent.com/s-yh-china/ArknightsGachaData/master/data/pool_info.json")
        birthdays, events, home, overview = await asyncio.gather(
            anything.birthdays(), anything.events(), prts.home(), prts.gacha_overview()
        )
        start = datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        pools = await gacha.pools(start, start + timedelta(days=28), overview)
        output = {
            "birthday_count": len(birthdays),
            "birthday_sample": birthdays[:2],
            "event_count": len(events),
            "event_sample": events[:3],
            "home": home,
            "overview_count": len(overview),
            "overview_tail": overview[-3:],
            "pools": [
                {**item, "start": item["start"].isoformat(), "end": item["end"].isoformat()}
                for item in pools
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
