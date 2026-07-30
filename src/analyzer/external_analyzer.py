"""Генерация короткого поста для внешнего рабочего чата из /sumup."""

import logging

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)

MAX_REPORT_CHARS = 50_000
MIN_WORDS = 120
MAX_WORDS = 250

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Ты — редактор, который превращает внутренний отчёт по посевной
или PR-кампании в короткий пост для рабочего чата: такой, который дочитают за
20 секунд и захотят переслать.

Тебе передан структурированный внутренний отчёт: общие результаты, органика,
хронология, сверхрезультаты, анализ вовлечённости и комментарии.

ЖЁСТКОЕ ПРАВИЛО ПО ФАКТАМ:
Используй только цифры, названия каналов, ссылки и даты из входного отчёта.
Ничего не досочиняй, не меняй и не округляй цифры в пользу кампании. Если
данных для элемента нет — пропусти его.

ЗАДАЧА:
1. Найди одного героя: самую впечатляющую цифру или момент кампании. Обычно
это перевыполнение на конкретном канале, итог против плана или самый вирусный
пост. Остальные факты должны поддерживать героя, а не быть равным списком.
2. Начни с заголовка-крючка: шутка, инсайт или неожиданный факт, а не
«Отчёт по проекту». Допустим один эмодзи в начале.
3. Если есть даты, покажи движение: как началось, когда пошла органика, чем
закончилось. Не выдумывай даты.
4. Каждый упомянутый канал оформляй как [Название](ссылка). Бери 2–4 самых
ярких paid/organic момента; не перечисляй все публикации.
5. При наличии яркой реакции аудитории из комментариев вплети её в историю
своими словами, без прямых цитат и отдельного технического блока.
6. Последний абзац — кульминация: итоговый охват против плана и/или экономия
как один сильный факт. Общий охват не ставь в самый первый абзац.

СТИЛЬ И ФОРМАТ:
- Telegram Markdown: **жирный** для ключевых цифр, [текст](ссылка) для каналов.
- Неформально, слегка иронично, без канцелярита и слова «отчёт».
- 3–5 эмодзи максимум на весь текст.
- 120–250 слов, обычные абзацы, без H1/H2 и без пояснений от себя.
- Не пиши «как видно из таблицы» и не показывай следы внутренней структуры.

На выходе дай только готовый пост."""


def word_count(text: str) -> int:
    return len([word for word in text.split() if word])


async def generate_external_post(internal_report: str) -> str:
    """Возвращает Telegram Markdown-пост из полного внутреннего отчёта."""
    internal_report = (internal_report or "").strip()
    if not internal_report:
        raise ValueError("Внутренний отчёт пуст")
    if len(internal_report) > MAX_REPORT_CHARS:
        raise ValueError(
            f"Отчёт слишком длинный: {len(internal_report):,} символов. "
            f"Лимит — {MAX_REPORT_CHARS:,}."
        )

    logger.info("Generating external post: input_chars=%s model=%s", len(internal_report), OPENAI_MODEL)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": internal_report},
        ],
        temperature=0.55,
        timeout=90,
    )
    result = (response.choices[0].message.content or "").strip()
    if not result:
        raise ValueError("OpenAI вернул пустой пост")

    count = word_count(result)
    if MIN_WORDS <= count <= MAX_WORDS:
        logger.info("External post generated: words=%s", count)
        return result

    # Один корректирующий запрос, если модель проигнорировала лимит длины.
    logger.info("External post needs length correction: words=%s", count)
    correction = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Сократи или дополни этот готовый пост до 120–250 слов, не меняя "
                    "ни одного факта, числа, даты, названия или ссылки. Верни только "
                    "исправленный Telegram Markdown-пост.\n\n" + result
                ),
            },
        ],
        temperature=0.2,
        timeout=60,
    )
    corrected = (correction.choices[0].message.content or "").strip()
    if not corrected:
        raise ValueError("OpenAI вернул пустой пост после коррекции длины")
    logger.info("External post corrected: words=%s", word_count(corrected))
    return corrected
