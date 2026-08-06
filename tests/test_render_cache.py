import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot_plugin.core.models import CalendarSnapshot, SourceState
from astrbot_plugin.core.render_cache import CalendarImageCache, HelpImageCache


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


class HelpImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "render"
        self.cache = HelpImageCache(self.root)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _now(day: int) -> datetime:
        return datetime(2026, 8, day, 4, 0, tzinfo=CN_TZ)

    def test_reuses_only_the_same_natural_day(self):
        stored = self.cache.store(b"\x89PNG\r\n\x1a\nexample", "full", now=self._now(3))

        self.assertEqual(self.cache.lookup("full", self._now(3)), stored)
        self.assertIsNone(self.cache.lookup("full", self._now(4)))

    def test_prunes_old_images_per_mode(self):
        for day in (1, 2, 3):
            self.cache.store(
                b"\x89PNG\r\n\x1a\nexample",
                "full",
                now=self._now(day),
                keep_days=2,
            )
        self.cache.store(b"\x89PNG\r\n\x1a\nexample", "subscribe", now=self._now(1), keep_days=2)

        self.assertFalse((self.root / "help-full-2026-08-01.png").exists())
        self.assertTrue((self.root / "help-full-2026-08-02.png").exists())
        self.assertTrue((self.root / "help-full-2026-08-03.png").exists())
        self.assertTrue((self.root / "help-subscribe-2026-08-01.png").exists())

    def test_invalid_image_does_not_leave_a_temporary_file(self):
        with self.assertRaises(ValueError):
            self.cache.store(b"not-a-png", "full", now=self._now(3))

        self.assertFalse((self.root / "help-full-2026-08-03.png").exists())
        self.assertEqual(list(self.root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
