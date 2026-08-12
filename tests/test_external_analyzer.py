import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import MessageEntity

from main import message_to_external_markdown
from src.analyzer.external_analyzer import generate_external_post, word_count


def response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class ExternalAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    def test_restores_text_link_with_utf16_offsets(self):
        text = "🔥 Канал принёс результат"
        # Telegram offset в UTF-16: emoji занимает два code units, затем пробел.
        entity = MessageEntity(
            type="text_link",
            offset=3,
            length=5,
            url="https://example.com/channel",
        )
        message = SimpleNamespace(text=text, entities=[entity])
        self.assertEqual(
            message_to_external_markdown(message),
            "🔥 [Канал](https://example.com/channel) принёс результат",
        )

    @patch("src.analyzer.external_analyzer.client.chat.completions.create", new_callable=AsyncMock)
    async def test_returns_first_post_when_word_count_is_valid(self, create):
        post = " ".join(["слово"] * 130)
        create.return_value = response(post)
        result = await generate_external_post("Внутренний отчёт с фактами")
        self.assertEqual(result, post)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(word_count(result), 130)

    @patch("src.analyzer.external_analyzer.client.chat.completions.create", new_callable=AsyncMock)
    async def test_does_not_trim_long_post_with_required_link_lists(self, create):
        post = " ".join(["публикация"] * 300)
        create.return_value = response(post)
        result = await generate_external_post("Внутренний отчёт с фактами")
        self.assertEqual(result, post)
        self.assertEqual(create.await_count, 1)

    @patch("src.analyzer.external_analyzer.client.chat.completions.create", new_callable=AsyncMock)
    async def test_returns_short_formal_post_without_expansion(self, create):
        short = " ".join(["коротко"] * 10)
        create.return_value = response(short)
        result = await generate_external_post("Внутренний отчёт с фактами")
        self.assertEqual(result, short)
        self.assertEqual(create.await_count, 1)

    @patch("src.analyzer.external_analyzer.client.chat.completions.create", new_callable=AsyncMock)
    async def test_uses_fact_based_fallback_when_openai_is_unavailable(self, create):
        create.side_effect = ConnectionError("Connection error")
        report = """Проект: Тест
Плановый paid-охват: 100
Фактический paid-охват: 150
Общий охват с органикой: 180
Выполнение paid-плана: 150%
Органический охват: 30 просмотров
Фактический CPV с учётом органики: 1,00 ₽

СВЕРХРЕЗУЛЬТАТЫ

[Paid](https://example.com/paid) — 150 просмотров при плане 100 (1,5× плана)

ВСЕ ПУБЛИКАЦИИ

• [Paid](https://example.com/paid)

ОРГАНИКА

• [Organic](https://example.com/organic)
"""

        result = await generate_external_post(report, "Продвигали аниме в развлекательных каналах.")

        self.assertTrue(result.startswith("Продвигали аниме в развлекательных каналах."))
        self.assertIn("Вместо 100 просмотров получили 150.", result)
        self.assertIn("Превысили план на 50%.", result)
        self.assertIn("Больше всего просмотров принесла публикация у [Paid](https://example.com/paid)", result)
        self.assertIn("Все вышедшие публикации:\n• [Paid](https://example.com/paid)", result)
        self.assertIn("Органические публикации:\n• [Organic](https://example.com/organic)", result)


if __name__ == "__main__":
    unittest.main()
