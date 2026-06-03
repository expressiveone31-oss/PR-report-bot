"""
GPT-assisted CSV schema detector.

Получает первые строки CSV, возвращает маппинг смысловых полей → индексы колонок.
Работает с любым форматом МП — Telegram, VK, Instagram, Twitter.
"""

import csv
import io
import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI
from src.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


@dataclass
class CsvSchema:
    """Маппинг смысловых полей → индексы колонок в paid-блоке."""
    header_row: int           # индекс строки-заголовка paid-блока (0-based)

    col_name: Optional[int]         # название канала / блогера
    col_channel_url: Optional[int]  # ссылка на канал
    col_post_url: Optional[int]     # ссылка на публикацию
    col_planned_reach: Optional[int]
    col_actual_reach: Optional[int]
    col_cost: Optional[int]         # бюджет размещения (стоимость)
    col_planned_cpv: Optional[int]
    col_actual_cpv: Optional[int]
    col_platform: Optional[int]     # если есть явная колонка платформы
    col_date: Optional[int]

    # Органика — может быть в правой части той же таблицы
    # или в отдельном блоке ниже
    organic_trigger: str = "органик"  # подстрока, по которой ищем начало блока органики


SYSTEM_PROMPT = """\
Ты анализируешь заголовки CSV медиаплана рекламного посева.
Твоя задача — найти строку-заголовок paid-блока и определить индексы нужных колонок (0-based).

Верни ТОЛЬКО валидный JSON без пояснений:
{
  "header_row": <int>,
  "col_name": <int или null>,
  "col_channel_url": <int или null>,
  "col_post_url": <int или null>,
  "col_planned_reach": <int или null>,
  "col_actual_reach": <int или null>,
  "col_cost": <int или null>,
  "col_planned_cpv": <int или null>,
  "col_actual_cpv": <int или null>,
  "col_platform": <int или null>,
  "col_date": <int или null>
}

Правила:
- header_row: индекс первой строки с заголовками paid-постов (обычно строка 0)
- col_post_url: колонка со ссылкой непосредственно на публикацию (не на канал). Примеры заголовков: «Ссылка на публикацию», «Публикация», «Ссылка на пост», «Ссылка на твит»
- col_channel_url: колонка со ссылкой на канал/профиль (не на конкретный пост)
- col_planned_reach: плановый/ожидаемый охват. Примеры: «Планируемый охват», «Охват план», «Просмотры», «Ожидаемый охват». ВАЖНО: НЕ путать с «Подписчики» — это разные колонки
- col_actual_reach: фактический/реальный охват. Примеры: «Охват (факт)», «Просмотры факт», «Реальный охват», «Охват факт». ВАЖНО: если в таблице есть и «Просмотры» и «Просмотры факт» — col_actual_reach это «Просмотры факт», а col_planned_reach это «Просмотры»
- col_cost: стоимость/цена размещения с агентской комиссией. Примеры: «Общая стоимость с АК 15%», «Цена с АК», «Стоимость с АК». НЕ брать «Цена до АК»
- col_planned_cpv: плановый CPV. Примеры: «Планируемый CPV», «CPV», «CPV план». НЕ брать «CPV ФАКТ»
- col_actual_cpv: фактический CPV. Примеры: «Факт CPV», «CPV факт», «CPV ФАКТ»
- Если колонки нет — верни null
- Индексы считай с 0
- «Подписчики» — это НЕ охват, игнорируй эту колонку при определении col_planned_reach
"""


async def detect_schema(content: str) -> CsvSchema:
    """
    Определяет схему CSV через GPT.
    Передаёт только заголовки + первые 5 строк данных, чтобы не тратить токены.
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    # Берём первые 8 строк — обычно этого достаточно для определения структуры
    sample_rows = rows[:8]
    sample_text = "\n".join(
        f"row {i}: {row}" for i, row in enumerate(sample_rows)
    )

    logger.info(f"CSV schema detection: {len(rows)} rows total, sending first {len(sample_rows)} to GPT")

    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"CSV (первые строки):\n{sample_text}"},
        ],
        temperature=0,
        timeout=30,
    )

    raw = response.choices[0].message.content.strip()

    # Вырезаем JSON из ответа на случай если модель добавила текст вокруг
    json_match = __import__("re").search(r"\{.*\}", raw, __import__("re").DOTALL)
    if not json_match:
        raise ValueError(f"GPT не вернул JSON: {raw[:200]}")

    data = json.loads(json_match.group())
    logger.info(f"CSV schema detected: {data}")

    return CsvSchema(
        header_row=data.get("header_row", 0),
        col_name=data.get("col_name"),
        col_channel_url=data.get("col_channel_url"),
        col_post_url=data.get("col_post_url"),
        col_planned_reach=data.get("col_planned_reach"),
        col_actual_reach=data.get("col_actual_reach"),
        col_cost=data.get("col_cost"),
        col_planned_cpv=data.get("col_planned_cpv"),
        col_actual_cpv=data.get("col_actual_cpv"),
        col_platform=data.get("col_platform"),
        col_date=data.get("col_date"),
    )
