import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.orchestrator import process_mediaplan_full
from src.parsers.mediaplan import MediaPlan, Post


class SumupV2IntegrationTests(unittest.IsolatedAsyncioTestCase):
    @patch("src.analyzer.report_v2._brief_summary", new_callable=AsyncMock)
    @patch("src.orchestrator._fetch_stats_for_post", new_callable=AsyncMock)
    async def test_full_mediaplan_flow_uses_api_then_mp_fallback(self, fetch, summary):
        summary.return_value = "Короткий вывод."
        mp = MediaPlan(
            paid_posts=[
                Post(
                    name="Канал A",
                    channel_url="https://vk.com/a",
                    platform="vk",
                    post_url="https://vk.com/wall-1_1",
                    planned_reach=100,
                    actual_reach=110,
                    cost=150,
                    date="01.07.2026",
                ),
                Post(
                    name="Канал B",
                    channel_url="https://t.me/b",
                    platform="telegram",
                    post_url="https://t.me/b/1",
                    planned_reach=50,
                    actual_reach=60,
                    date="02.07.2026",
                ),
            ],
            organic_posts=[
                Post(
                    name="Органика C",
                    channel_url="https://vk.com/c",
                    platform="vk",
                    post_url="https://vk.com/wall-3_3",
                    actual_reach=40,
                    is_organic=True,
                    date="03.07.2026",
                ),
            ],
            mp_total_actual_reach=240,
        )

        fetch.side_effect = [
            {
                "name": "Канал A", "channel_url": "https://vk.com/a", "platform": "vk",
                "is_organic": False, "post_url": "https://vk.com/wall-1_1", "date": "01.07.2026",
                "planned_reach": 100, "mp_actual_reach": 110, "actual_cpv": None,
                "planned_cpv": None, "stats": {"views": 130, "error": None},
            },
            {
                "name": "Органика C", "channel_url": "https://vk.com/c", "platform": "vk",
                "is_organic": True, "post_url": "https://vk.com/wall-3_3", "date": "03.07.2026",
                "planned_reach": 0, "mp_actual_reach": 40, "actual_cpv": None,
                "planned_cpv": None, "stats": {"views": 50, "error": None},
            },
            {
                "name": "Канал B", "channel_url": "https://t.me/b", "platform": "telegram",
                "is_organic": False, "post_url": "https://t.me/b/1", "date": "02.07.2026",
                "planned_reach": 50, "mp_actual_reach": 60, "actual_cpv": None,
                "planned_cpv": None, "stats": {"views": None, "error": "timeout"},
            },
        ]

        text, posts_data, total = await process_mediaplan_full(mp, "Тестовый проект")

        self.assertEqual(total, 240)  # paid 130 + fallback MP 60 + organic API 50
        self.assertEqual(posts_data[0]["fact_source"], "API")
        self.assertEqual(posts_data[1]["fact_source"], "МП")
        self.assertEqual(posts_data[2]["fact_source"], "API")
        self.assertIn("ОБЩИЕ РЕЗУЛЬТАТЫ", text)
        self.assertIn("Фактический paid-охват: 190", text)
        self.assertNotIn("<b>ОРГАНИКА</b>", text)
        self.assertNotIn("Итого органика:", text)
        self.assertIn("Органический охват: 50 просмотров", text)
        self.assertNotIn("Контрольный итог в МП", text)  # контроль совпал


if __name__ == "__main__":
    unittest.main()
