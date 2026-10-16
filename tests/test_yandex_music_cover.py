import unittest

from src.card.yandex_music_cover import (
    YandexMusicCoverData,
    YandexMusicMetric,
    build_music_cover_svg,
    render_music_cover,
)


class YandexMusicCoverTests(unittest.TestCase):
    def test_build_svg_contains_title_and_hero(self):
        data = YandexMusicCoverData(
            title_lines=["Небренд-прогрев", "Распаковки"],
            subtitle="Итоги посева",
            background="blue",
            hero="1 278 664",
            hero_label="просмотров",
            metrics=[
                YandexMusicMetric("PAID-ОХВАТ", "900 000", "план 600 000"),
                YandexMusicMetric("ПЕРЕВЫПОЛНЕНИЕ", "150%", "paid-плана"),
            ],
            highlight="Топ-пост — 120 000 просмотров",
        )
        svg = build_music_cover_svg(data)
        self.assertIn("Небренд-прогрев", svg)
        self.assertIn("1 278 664", svg)
        self.assertIn("ХАЙЛАЙТ ПОСЕВА", svg)
        self.assertIn("data:image/png;base64,", svg)

    def test_render_png(self):
        data = YandexMusicCoverData(
            title_lines=["Тест"],
            subtitle="Итоги посева",
            background="pink",
            hero="10 000",
            hero_label="просмотров",
            metrics=[YandexMusicMetric("ПУБЛИКАЦИИ", "12", "вышли")],
        )
        png = render_music_cover(data)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 1000)


if __name__ == "__main__":
    unittest.main()
