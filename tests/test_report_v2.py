import unittest
from unittest.mock import AsyncMock, patch

from src.analyzer.report_v2 import (
    _build_paid_table,
    _display_date,
    _engagement_rows,
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
    def test_excel_datetime_and_human_paid_format(self):
        item = post(
            "курОлеся", "instagram", "https://www.instagram.com/reel/Da2mSdPq7RF/",
            views=46040, planned=60000, date="2026-07-16 00:00:00",
        )
        resolve_fact_sources([item])
        text = _build_paid_table([item])
        self.assertEqual(_display_date("2026-07-16 00:00:00"), "16 июля")
        self.assertIn('<a href="https://www.instagram.com/reel/Da2mSdPq7RF/">курОлеся</a>', text)
        self.assertIn("16 июля", text)
        self.assertIn("Охват по плану — 60 000, фактический охват — 46 040 просмотров", text)
        self.assertIn("-13 960 просмотров (-23%)", text)

    def test_engagement_is_grouped_in_words_and_keeps_zero(self):
        item = post("Смешные Видео", "instagram", "https://instagram.com/reel/x", views=100)
        item["stats"].update({
            "likes": 200,
            "comments": 0,
            "reposts": 2,
            "channel_avg": {"avg_likes": 100, "avg_comments": 10, "avg_reposts": 10},
        })
        rows = _engagement_rows([item])
        self.assertEqual(len(rows), 1)
        self.assertIn("больше лайков чем обычно", rows[0])
        self.assertIn("меньше комментариев", rows[0])
        self.assertIn("меньше репостов/пересылок", rows[0])
        self.assertIn('<a href="https://instagram.com/reel/x">Смешные Видео</a>', rows[0])

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
            "О ЧЁМ ПИСАЛИ В КОММЕНТАРИЯХ",
        ):
            self.assertIn(heading, text)
        self.assertIn("Контрольный итог в МП: 230", text)
        self.assertIn("Пересчитанный актуальный итог: 250", text)
        self.assertIn("Формула: органический охват × 1,5 ₽", text)
        self.assertIn("Paid: 1 публикация в 1 канале", text)
        self.assertIn("Органика: 1 публикация в 1 канале", text)
        self.assertIn("Даты флайта: 1 июля — 1 июля", text)
        self.assertIn("Первые органические публикации: 3 июля", text)

    @patch("src.analyzer.openai_analyzer._analyze_comments", new_callable=AsyncMock)
    @patch("src.analyzer.report_v2._brief_summary", new_callable=AsyncMock)
    async def test_comments_analysis_is_included(self, summary, comments):
        summary.return_value = "Короткий вывод."
        comments.return_value = (
            "О чём писали в комментариях:\n\n"
            "ПОСТ 1: обсуждали конкретную сцену и героя."
        )
        item = post("Канал", "youtube", "https://youtube.com/watch?v=x", views=100)
        item["stats"].update({"comments": 20, "top_comments": ["Комментарий"]})
        text, _ = await build_report_v2("Тест", [item], 50, 100)
        self.assertIn("<b>О ЧЁМ ПИСАЛИ В КОММЕНТАРИЯХ</b>", text)
        self.assertIn("Комментарии проанализированы по 1 публикации из 1", text)
        self.assertIn("обсуждали конкретную сцену и героя", text)


if __name__ == "__main__":
    unittest.main()
