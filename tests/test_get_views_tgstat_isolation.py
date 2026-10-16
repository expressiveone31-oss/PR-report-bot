"""TGStat без токена не должен затирать успешный ответ Telemetr."""

import unittest
from unittest.mock import AsyncMock, patch

from src.platforms.telemetr import TelemetrPostStats
from src.platforms.tgstat import TGStatPostStats
from src.updater.common import get_views


class TgstatIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_tgstat_token_keeps_telemetr(self):
        telemetr = TelemetrPostStats(
            post_url="https://t.me/rhymestg/194635",
            views=12345,
        )
        with patch(
            "src.platforms.telemetr.get_post_stats",
            new=AsyncMock(return_value=telemetr),
        ), patch(
            "src.config.TGSTAT_TOKEN",
            None,
        ), patch(
            "src.platforms.tgstat.TGSTAT_TOKEN",
            None,
        ):
            # Реальный tgstat.get_post_stats при token=None раньше давал TypeError
            # через aiohttp; теперь должен вернуть error без exception.
            from src.platforms import tgstat as tgstat_mod

            fallback = await tgstat_mod.get_post_stats("https://t.me/rhymestg/194635")
            self.assertEqual(fallback.error, "TGSTAT_TOKEN не задан")

            result = await get_views("https://t.me/rhymestg/194635")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.views, 12345)

    async def test_tgstat_exception_does_not_wipe_telemetr(self):
        telemetr = TelemetrPostStats(
            post_url="https://t.me/rhymestg/194635",
            views=999,
        )
        with patch(
            "src.platforms.telemetr.get_post_stats",
            new=AsyncMock(return_value=telemetr),
        ), patch(
            "src.platforms.tgstat.get_post_stats",
            new=AsyncMock(side_effect=TypeError(
                "Invalid variable type: value should be str, int or float, got None"
            )),
        ):
            result = await get_views("https://t.me/rhymestg/194635")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.views, 999)

    async def test_tgstat_guard_returns_error_not_raise(self):
        with patch("src.platforms.tgstat.TGSTAT_TOKEN", None):
            from src.platforms import tgstat as tgstat_mod

            result = await tgstat_mod.get_post_stats("https://t.me/lider/19961")
            self.assertIsInstance(result, TGStatPostStats)
            self.assertEqual(result.error, "TGSTAT_TOKEN не задан")
            self.assertIsNone(result.views)


if __name__ == "__main__":
    unittest.main()
