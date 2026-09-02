import unittest
from unittest.mock import AsyncMock, patch

from src.analyzer.report_v2 import (
    _build_paid_table,
    _display_date,
    _generalized_engagement,
    _parse_date,
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
    def test_api_date_formats_are_supported(self):
        self.assertEqual(str(_parse_date("2026-07-16T10:30:00+00:00")), "2026-07-16")
        self.assertEqual(str(_parse_date("1784196000")), "2026-07-16")

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

    def test_engagement_is_generalized_across_campaign(self):
        posts = []
        for index in range(3):
            item = post(
                f"Канал {index}", "instagram", f"https://instagram.com/reel/{index}", views=100
            )
            item["stats"].update({
                "likes": 200,
                "comments": 10,
                "reposts": 2,
                "channel_avg": {"avg_likes": 100, "avg_comments": 10, "avg_reposts": 10},
            })
            posts.append(item)
        text = _generalized_engagement(posts)
        self.assertIn("в среднем набирали больше лайков/реакций", text)
        self.assertIn("обычное количество комментариев", text)
        self.assertIn("меньше репостов/пересылок", text)
        self.assertEqual(text.count("<a href="), 2)  # максимум по одному примеру на метрику

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

    def test_api_zero_does_not_overwrite_mp_reach(self):
        """Регрессия: Instagram/VK часто возвращают views=0 при недоступном
        посте или исчерпанной квоте. Раньше это затирало валидное значение
        из МП (Летняя_распаковка: 1 222 312 → 40 018)."""
        posts = [
            # Три поста, где API вернул None или 0, но в МП стоят реальные охваты
            post("ПАБЛО РАДИНИ", "instagram", "https://instagram.com/p/1",
                 views=None, mp_views=1_023_252, planned=80_000),
            post("Маршрутизатор", "instagram", "https://instagram.com/p/2",
                 views=0, mp_views=37_379, planned=10_000),
            post("Лепра", "instagram", "https://instagram.com/reel/3",
                 views=40_021, mp_views=40_011, planned=35_000),
            post("Рифмы", "instagram", "https://instagram.com/p/4",
                 views=0, mp_views=121_670, planned=35_000),
        ]
        resolve_fact_sources(posts)

        self.assertEqual(posts[0]["fact_source"], "МП")
        self.assertEqual(posts[0]["stats"]["views"], 1_023_252)
        self.assertEqual(posts[1]["fact_source"], "МП")
        self.assertEqual(posts[1]["stats"]["views"], 37_379)
        self.assertEqual(posts[2]["fact_source"], "API")
        self.assertEqual(posts[2]["stats"]["views"], 40_021)
        self.assertEqual(posts[3]["fact_source"], "МП")
        self.assertEqual(posts[3]["stats"]["views"], 121_670)

        metrics = calculate_metrics(posts, planned_reach=160_000, placement_budget=282_642)
        # Ожидаем: 1_023_252 + 37_379 + 40_021 + 121_670 = 1_222_322 (не 40_018!)
        self.assertEqual(metrics.paid_actual, 1_222_322)

    def test_api_zero_and_no_mp_gives_honest_zero(self):
        """Если API вернул 0 и в МП тоже пусто — оставляем 0, это честный
        результат (пост реально без просмотров)."""
        item = post("Пусто", "vk", "https://vk.com/wall1", views=0, mp_views=None)
        resolve_fact_sources([item])
        self.assertEqual(item["fact_source"], "API")
        self.assertEqual(item["stats"]["views"], 0)

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
            "ПЕРЕВЫПОЛНЕНИЕ И ЭКОНОМИЯ БЮДЖЕТА",
            "ХРОНОЛОГИЯ",
            "СВЕРХРЕЗУЛЬТАТЫ",
            "АНАЛИТИКА ПО ЛАЙКАМ, КОММЕНТАРИЯМ И РЕПОСТАМ",
            "О ЧЁМ ПИСАЛИ В КОММЕНТАРИЯХ",
            "ВСЕ ПУБЛИКАЦИИ",
            "ОРГАНИКА",
        ):
            self.assertIn(heading, text)
        self.assertNotIn("PAID-ПОСТЫ", text)
        self.assertNotIn("Итого органика:", text)
        self.assertNotIn("АНАЛИТИКА ВОВЛЕЧЁННОСТИ", text)
        self.assertIn("Контрольный итог в МП: 230", text)
        self.assertIn("Пересчитанный актуальный итог: 250", text)
        self.assertIn("Формула: органический охват × 1,5 ₽", text)
        self.assertIn("Paid: 1 публикация в 1 канале", text)
        self.assertIn("Органика: 1 публикация в 1 канале", text)
        self.assertIn("Даты флайта: 1 июля — 3 июля", text)
        self.assertIn("Период посева: с 1 июля по 3 июля", text)
        self.assertIn("Первые органические публикации: 3 июля", text)
        self.assertIn('• <a href="https://vk.com/wall-1_1">Твои мужики</a>', text)
        self.assertIn('• <a href="https://t.me/a/1">Органический канал</a>', text)

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
        self.assertIn("<b>ВСЕ ПУБЛИКАЦИИ</b>", text)
        self.assertNotIn("<b>ОРГАНИКА</b>", text)

    @patch("src.analyzer.openai_analyzer._analyze_comments", new_callable=AsyncMock)
    @patch("src.analyzer.report_v2._brief_summary", new_callable=AsyncMock)
    async def test_comments_are_limited_to_five_most_discussed(self, summary, comments):
        summary.return_value = "Короткий вывод."
        comments.return_value = "О чём писали в комментариях:\n\nТоп обсуждений."
        posts = []
        for count in (3, 50, 10, 100, 20, 80, 1):
            item = post(f"Канал {count}", "youtube", f"https://youtube.com/watch?v={count}", views=100)
            item["stats"].update({"comments": count, "top_comments": [f"Комментарий {count}"]})
            posts.append(item)
        text, _ = await build_report_v2("Тест", posts, 50, 100)
        selected = comments.await_args.args[0]
        self.assertEqual(len(selected), 5)
        self.assertEqual([p["stats"]["comments"] for p in selected], [100, 80, 50, 20, 10])
        self.assertIn("по 5 наиболее обсуждаемым публикациям из 7", text)


if __name__ == "__main__":
    unittest.main()
