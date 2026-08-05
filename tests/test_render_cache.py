import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot_plugin.core.models import CalendarSnapshot, SourceState
from astrbot_plugin.core.render_cache import CalendarImageCache


CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = CalendarSnapshot(
            generated_at="2026-08-03T08:00:00+08:00",
            calendar_date="2026-08-03",
            timeline_start="2026-08-02T00:00:00+08:00",
            timeline_end="2026-08-30T00:00:00+08:00",
        )
        self.display = {"timeline_days": 28}

    def tearDown(self):
        self.temp.cleanup()

    def test_reuses_matching_snapshot_and_display_config(self):
        source = self.root / "source.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nexample")
        cache = CalendarImageCache(self.root / "render")

        stored = cache.store(source, self.snapshot, self.display, 30, 3)

        self.assertEqual(
            cache.lookup(
                self.snapshot,
                self.display,
                datetime(2026, 8, 3, 8, 10, tzinfo=CN_TZ),
            ),
            stored,
        )
        self.assertIsNone(
            cache.lookup(
                self.snapshot,
                {"timeline_days": 14},
                datetime(2026, 8, 3, 8, 10, tzinfo=CN_TZ),
            )
        )

    def test_signature_ignores_refresh_metadata(self):
        cache = CalendarImageCache(self.root / "render")
        refreshed = CalendarSnapshot(
            generated_at="2026-08-03T10:00:00+08:00",
            calendar_date=self.snapshot.calendar_date,
            timeline_start=self.snapshot.timeline_start,
            timeline_end=self.snapshot.timeline_end,
            source_states=[SourceState(
                name="source",
                ok=True,
                updated_at="2026-08-03T10:00:00+08:00",
                message="transient diagnostic",
            )],
        )
        initial = CalendarSnapshot(
            generated_at="2026-08-03T08:00:00+08:00",
            calendar_date=self.snapshot.calendar_date,
            timeline_start=self.snapshot.timeline_start,
            timeline_end=self.snapshot.timeline_end,
            source_states=[SourceState(
                name="source",
                ok=True,
                updated_at="2026-08-03T08:00:00+08:00",
                message="older diagnostic",
            )],
        )

        self.assertEqual(cache.signature(initial, self.display), cache.signature(refreshed, self.display))
    def test_cache_expires_at_midnight(self):
        source = self.root / "source.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nexample")
        cache = CalendarImageCache(self.root / "render")
        cache.store(source, self.snapshot, self.display, 60 * 24, 3)

        self.assertIsNone(
            cache.lookup(
                self.snapshot,
                self.display,
                datetime(2026, 8, 4, 0, 1, tzinfo=CN_TZ),
            )
        )


    def test_fallback_rejects_previous_calendar_date(self):
        source = self.root / "source.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nexample")
        cache = CalendarImageCache(self.root / "render")
        cache.store(source, self.snapshot, self.display, 60 * 24, 3)

        self.assertIsNone(
            cache.fallback(
                12,
                datetime(2026, 8, 4, 0, 1, tzinfo=CN_TZ),
            )
        )


if __name__ == "__main__":
    unittest.main()
