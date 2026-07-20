import unittest
from unittest.mock import AsyncMock, patch

from src.analyzer.report_v2 import (
    build_report_v2,
    calculate_metrics,
    resolve_fact_sources,
)


def post(
    name,
    platform,
    url,
    views=None,
    mp_views=None,
    planned=0,
    organic=False,
    date=None,
    channel_url="",
):
    return {
        "name": name,
        "channel_url": channel_url,
        "platform": platform,
        "post_url": url,
        "is_organic": organic,
        "date": date,
        "planned_reach": planned,
        "mp_actual_reach": mp_views,
        "stats": {"views": views, "error": None},
    }


class ReportV2Tests(unittest.IsolatedAsyncioTestCase):
    def test_api_priority_mp_fallback_and_no_double_count(self):
        posts = [
            post("Paid API", "vk", "https://vk.com/wall-1_1", views=120, mp_views=100, planned=80),
            post("Paid MP", "vk", "https://vk.com/wall-2_2", views=None, mp_views=50, planned=40),
            post("Organic API", "telegram", "https://t.me/a/1", views=70, mp_views=60, organic=True),
            post("Organic MP", "telegram", "https://t.me/b/1", views=None, mp_views=30, organic=True),
        ]
        resolve_fact_sources(posts)
        metrics = calculate_metrics(posts, planned_reach=120, placement_budget=240, control_total=999)

        self.assertEqual(posts[0]["fact_source"], "API")
        self.assertEqual(posts[1]["fact_source"], "МП")
        self.assertEqual(posts[2]["fact_source"], "API")
        self.assertEqual(posts[3]["fact_source"], "МП")
        self.assertEqual(metrics.paid_actual, 170)
        self.assertEqual(metrics.organic_actual, 100)
        self.assertEqual(metrics.total_actual, 270)
        self.assertEqual(metrics.organic_budget_equivalent, 150)
        self.assertAlmostEqual(metrics.actual_cpv, 240 / 270)
        self.assertEqual(metrics.control_difference, 270 - 999)

    @patch("src.analyzer.report_v2._brief_summary", new_callable=AsyncMock)
    async def test_report_contains_v2_sections_and_control_difference(self, summary):
        summary.return_value = "Короткий вывод по фактам."
        posts = [
            post(
                "Твои мужики",
                "vk",
                "https://vk.com/wall-1_1",
                views=200,
                planned=100,
                date="01.07.2026",
                channel_url="https://vk.com/club1",
            ),
            post(
                "Органический канал",
                "telegram",
                "https://t.me/a/1",
                views=50,
                organic=True,
                date="03.07.2026",
                channel_url="https://t.me/a",
            ),
        ]
        text, metrics = await build_report_v2(
            "Тест",
            posts,
            planned_reach=100,
            placement_budget=150,
            control_total=230,
        )

        self.assertEqual(metrics.total_actual, 250)
        for heading in (
            "КРАТКИЙ ВЫВОД",
            "ОБЩИЕ РЕЗУЛЬТАТЫ",
            "PAID-ПОСТЫ",
            "ОРГАНИКА",
            "ПЕРЕВЫПОЛНЕНИЕ И ЭКОНОМИЯ БЮДЖЕТА",
            "ХРОНОЛОГИЯ",
            "СВЕРХРЕЗУЛЬТАТЫ",
            "АНАЛИТИКА ВОВЛЕЧЁННОСТИ",
            "КОММЕНТАРИИ",
        ):
            self.assertIn(heading, text)
        self.assertIn("Источник", text)
        self.assertIn("Контрольный итог в МП: 230", text)
        self.assertIn("Пересчитанный актуальный итог: 250", text)
        self.assertIn("Формула: органический охват × 1,5 ₽", text)
        self.assertIn("Paid: 1 публикация в 1 канале", text)
        self.assertIn("Органика: 1 публикация в 1 канале", text)
        self.assertIn("Даты флайта: 01.07.2026 — 01.07.2026", text)
        self.assertIn("Первые органические публикации: 03.07.2026", text)


if __name__ == "__main__":
    unittest.main()
