import asyncio
import unittest

from src.card.card_composer import (
    CAMPAIGN_CARD_HEADER,
    compose_music_cover_from_text,
    format_music_cover_preview,
    revise_music_cover_from_text,
    _campaign_card_from_block,
)


class MusicCardComposerTests(unittest.TestCase):
    def test_campaign_block_to_music_cover(self):
        text = "\n".join([
            CAMPAIGN_CARD_HEADER,
            "Проект: Небренд-прогрев Распаковки",
            "Общий охват: 1 278 664",
            "Публикации: 40",
            "Paid-охват: 900 000",
            "Paid-план: 600 000",
            "Перевыполнение paid-плана: 150%",
            "Органический охват: 378 664",
            "Organic-публикации: 10",
            "Хайлайт: Топ-пост | 120 000 | просмотров",
        ])
        cover = asyncio.run(compose_music_cover_from_text(text))
        self.assertEqual(cover.hero, "1 278 664")
        self.assertTrue(any(m.name == "PAID-ОХВАТ" for m in cover.metrics))
        preview = format_music_cover_preview(cover)
        self.assertIn("Яндекс Музыка", preview)

    def test_fallback_and_revise_background(self):
        cover = asyncio.run(
            compose_music_cover_from_text("Летний дроп\nИтоги посева\nрозовый фон слева")
        )
        self.assertEqual(cover.background, "pink")
        self.assertEqual(cover.align, "left")
        revised = revise_music_cover_from_text(cover, "зелёный фон, по центру")
        self.assertEqual(revised.background, "green")
        self.assertEqual(revised.align, "center")

    def test_campaign_card_kinopoisk_labels(self):
        text = "\n".join([
            CAMPAIGN_CARD_HEADER,
            "Проект: Тест",
            "Общий охват: 100 000",
            "Публикации: 5",
            "Paid-охват: 80 000",
            "Paid-план: 50 000",
        ])
        card = _campaign_card_from_block(text)
        self.assertIsNotNone(card)
        self.assertEqual(card.breakdown_label, "КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ")
        self.assertEqual(card.reach_label, "РЕЗУЛЬТАТ")


if __name__ == "__main__":
    unittest.main()
