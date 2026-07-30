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
    async def test_corrects_post_when_word_count_is_outside_range(self, create):
        short = " ".join(["коротко"] * 10)
        corrected = " ".join(["исправлено"] * 125)
        create.side_effect = [response(short), response(corrected)]
        result = await generate_external_post("Внутренний отчёт с фактами")
        self.assertEqual(result, corrected)
        self.assertEqual(create.await_count, 2)


if __name__ == "__main__":
    unittest.main()
