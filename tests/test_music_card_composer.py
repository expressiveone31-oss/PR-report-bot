import asyncio
import unittest

from src.card.card_composer import (
    CAMPAIGN_CARD_HEADER,
    build_picture_data_block,
    compose_music_cover_from_text,
    compute_facade_metrics,
    format_music_cover_preview,
    revise_music_cover_from_text,
    _campaign_card_from_block,
)


class MusicCardComposerTests(unittest.TestCase):
    def test_campaign_block_to_music_cover_facade(self):
        text = "\n".join([
            CAMPAIGN_CARD_HEADER,
            "Проект: Небренд-прогрев Распаковки",
            "Общий охват: 1 278 664",
            "Органический охват: 378 664",
            "Organic-публикации: 10",
            "Paid-охват: 900 000",
            "Paid-план: 600 000",
            "Выполнение paid-плана: 150%",
            "CPV: 0,47 ₽",
            "Топ-публикация: Твои мужики",
            "Топ-охват: 120 000",
        ])
        cover = asyncio.run(compose_music_cover_from_text(text))
        self.assertEqual(cover.hero, "1 278 664")
        self.assertEqual(cover.hero_label, "просмотров")
        names = [m.name for m in cover.metrics]
        self.assertEqual(names, ["ОРГАНИКА", "PAID-ПЛАН", "CPV"])
        self.assertEqual(cover.metrics[0].value, "378 664")
        self.assertEqual(cover.metrics[0].detail, "10 публикаций")
        self.assertEqual(cover.metrics[1].value, "150%")
        self.assertEqual(cover.metrics[2].value, "0,47 ₽")
        self.assertEqual(cover.highlight, "Твои мужики — 120 000")
        preview = format_music_cover_preview(cover)
        self.assertIn("Яндекс Музыка", preview)

    def test_legacy_overperformance_key_still_works(self):
        text = "\n".join([
            CAMPAIGN_CARD_HEADER,
            "Проект: Тест",
            "Общий охват: 100 000",
            "Paid-охват: 80 000",
            "Paid-план: 50 000",
            "Перевыполнение paid-плана: 160%",
            "Органический охват: 20 000",
            "Organic-публикации: 3",
        ])
        cover = asyncio.run(compose_music_cover_from_text(text))
        self.assertTrue(any(m.name == "PAID-ПЛАН" and m.value == "160%" for m in cover.metrics))

    def test_fallback_and_revise_background(self):
        cover = asyncio.run(
            compose_music_cover_from_text("Летний дроп\nИтоги посева\nрозовый фон слева")
        )
        self.assertEqual(cover.background, "pink")
        self.assertEqual(cover.align, "left")
        revised = revise_music_cover_from_text(cover, "зелёный фон, по центру")
        self.assertEqual(revised.background, "green")
        self.assertEqual(revised.align, "center")

    def test_campaign_card_kinopoisk_facade(self):
        text = "\n".join([
            CAMPAIGN_CARD_HEADER,
            "Проект: Тест",
            "Общий охват: 100 000",
            "Органический охват: 20 000",
            "Organic-публикации: 4",
            "Paid-охват: 80 000",
            "Paid-план: 50 000",
            "Выполнение paid-плана: 160%",
            "CPV: 1,25 ₽",
            "Топ-публикация: Канал А",
            "Топ-охват: 40 000",
        ])
        card = _campaign_card_from_block(text)
        self.assertIsNotNone(card)
        self.assertEqual(card.breakdown_label, "КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ")
        self.assertEqual(card.reach_label, "РЕЗУЛЬТАТ")
        self.assertEqual(card.hero, "100 000")
        self.assertEqual([r.name for r in card.rows], ["ОРГАНИКА", "PAID-ПЛАН", "CPV", "Канал А"])
        self.assertTrue(card.rows[-1].highlight)

    def test_build_picture_data_block_facade(self):
        posts = [
            {
                "name": "Канал А",
                "platform": "vk",
                "is_organic": False,
                "planned_reach": 50_000,
                "stats": {"views": 80_000},
            },
            {
                "name": "Канал Б",
                "platform": "telegram",
                "is_organic": True,
                "planned_reach": 0,
                "stats": {"views": 20_000},
            },
            {
                "name": "Канал В",
                "platform": "vk",
                "is_organic": True,
                "stats": {"views": 5_000},
            },
        ]
        block = build_picture_data_block(
            "Тестовый проект",
            posts,
            total_reach=105_000,
            placement_budget=52_500,
            planned_reach=50_000,
        )
        self.assertTrue(block.startswith(CAMPAIGN_CARD_HEADER))
        self.assertIn("Общий охват: 105 000", block)
        self.assertIn("Органический охват: 25 000", block)
        self.assertIn("Organic-публикации: 2", block)
        self.assertIn("Выполнение paid-плана: 160%", block)
        self.assertIn("CPV: 0,50 ₽", block)
        self.assertIn("Топ-публикация: Канал А", block)
        self.assertIn("Топ-охват: 80 000", block)

        facade = compute_facade_metrics(posts, 105_000, 52_500, 50_000)
        self.assertEqual(facade["paid_completion_pct"], 160.0)
        self.assertAlmostEqual(facade["actual_cpv"], 0.5)


if __name__ == "__main__":
    unittest.main()
