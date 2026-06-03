"""
Универсальный парсер медиаплана.

Использует CsvSchema (определённую через GPT) для извлечения данных
из CSV любого формата. Не зависит от конкретных названий колонок.
"""

import csv
import io
import os
import re
import logging
from typing import Optional

from src.parsers.mediaplan import Post, MediaPlan, _detect_platform, _parse_number, _pick_best_url
from src.parsers.csv_schema import CsvSchema, detect_schema

logger = logging.getLogger(__name__)

# Стоп-слова для строк которые не являются размещениями
SKIP_ROW_KEYWORDS = (
    "итого", "общий", "сумма", "стоимость", "копирайт",
    "account", "junior", "менеджер", "прогноз", "факт cpv",
    "ндс", "органик", "суммарный",
)


def _cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _is_skip_row(row: list[str]) -> bool:
    first = row[0].strip().lower() if row else ""
    if not first:
        return True
    return any(kw in first for kw in SKIP_ROW_KEYWORDS)


def _parse_int(value: str) -> Optional[int]:
    result = _parse_number(value)
    return int(result) if result is not None else None


def parse_with_schema(content: str, schema: CsvSchema) -> MediaPlan:
    """
    Парсит CSV по готовой схеме.
    Возвращает MediaPlan с paid_posts и organic_posts.
    """
    mp = MediaPlan()
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    # --- Итоговый охват из МП ---
    # Ищем строки с «итого» и берём числа из них
    for row in rows:
        if not row:
            continue
        row_text = " ".join(row).lower()
        if "итого с органикой" in row_text:
            for cell in row:
                val = _parse_number(cell.strip())
                if val and val > 1000:
                    mp.mp_total_actual_reach = int(val)
                    break
            break

    # --- Paid-блок: строки после header_row ---
    organic_started = False
    seen_urls: set[str] = set()

    for i, row in enumerate(rows):
        if i <= schema.header_row:
            continue
        if not row:
            continue

        # Триггер органики
        row_text = " ".join(str(c) for c in row).lower()
        if schema.organic_trigger in row_text:
            organic_started = True
            continue

        if organic_started:
            # --- Органика ---
            # Ищем http-ссылки в любой ячейке строки
            for col_idx, cell in enumerate(row):
                cell = cell.strip()
                if not cell.startswith("http"):
                    continue
                SOCIAL_DOMAINS = ("vk.com", "vk.ru", "t.me", "instagram.com",
                                  "x.com", "twitter.com", "threads.com")
                if not any(d in cell for d in SOCIAL_DOMAINS):
                    continue
                if any(skip in cell for skip in ("disk.yandex", "yandex.ru/i/", "prnt.sc")):
                    continue
                if cell in seen_urls:
                    continue
                seen_urls.add(cell)

                # Охват из следующей непустой ячейки
                reach = 0
                for next_cell in row[col_idx + 1:]:
                    next_cell = next_cell.strip()
                    if next_cell and not next_cell.startswith("http"):
                        parsed = _parse_number(next_cell)
                        if parsed is not None:
                            reach = int(parsed)
                        break

                platform = _detect_platform(cell)
                mp.organic_posts.append(Post(
                    name=cell,
                    channel_url=cell,
                    platform=platform,
                    post_url=cell,
                    planned_reach=0,
                    actual_reach=reach if reach else None,
                    is_organic=True,
                ))
            continue

        # --- Paid-блок ---
        # Строка «Итого» завершает paid-блок — всё что ниже игнорируем
        first = row[0].strip().lower() if row else ""
        if first.startswith("итого") or first.startswith("общий"):
            break

        if _is_skip_row(row):
            continue

        # Нужна хотя бы ссылка на пост или канал
        post_url_raw = _cell(row, schema.col_post_url)
        channel_url_raw = _cell(row, schema.col_channel_url)

        post_url = _pick_best_url(post_url_raw) if post_url_raw else ""
        channel_url = channel_url_raw.strip()

        if not post_url and not channel_url:
            continue

        # Определяем платформу
        platform = _detect_platform(post_url or channel_url)
        if platform == "unknown" and not post_url:
            continue

        # Название
        name = _cell(row, schema.col_name) or post_url or channel_url

        # Числовые поля
        planned_reach = _parse_int(_cell(row, schema.col_planned_reach)) or 0
        actual_reach = _parse_int(_cell(row, schema.col_actual_reach))
        cost = _parse_number(_cell(row, schema.col_cost))
        planned_cpv = _parse_number(_cell(row, schema.col_planned_cpv))
        actual_cpv = _parse_number(_cell(row, schema.col_actual_cpv))
        date = _cell(row, schema.col_date)

        # Пропускаем строки без охвата и без ссылки на пост
        if not post_url and planned_reach == 0:
            continue

        mp.paid_posts.append(Post(
            name=name,
            channel_url=channel_url,
            platform=platform,
            post_url=post_url,
            planned_reach=planned_reach,
            actual_reach=actual_reach,
            cost=cost,
            planned_cpv=planned_cpv,
            actual_cpv=actual_cpv,
            date=date,
            is_organic=False,
        ))

    logger.info(
        f"Universal parse done: {len(mp.paid_posts)} paid posts, "
        f"{len(mp.organic_posts)} organic posts, "
        f"total_planned={mp.total_planned_reach}, total_budget={mp.total_budget:.0f}"
    )
    return mp


async def parse_mediaplan_auto(content: str) -> tuple[MediaPlan, CsvSchema]:
    """
    Главная точка входа: определяет схему через GPT, парсит CSV.
    Возвращает (MediaPlan, CsvSchema) — схема нужна для отладочного вывода.
    """
    schema = await detect_schema(content)
    mp = parse_with_schema(content, schema)
    return mp, schema
