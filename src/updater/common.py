"""Общие утилиты обновления охватов для xlsx и Google Sheets.

Вынесено, чтобы xlsx_updater и google_sheets_updater работали через
одну и ту же логику детекции колонок, платформ и получения просмотров.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Ключевые слова для поиска нужных колонок
POST_URL_KEYWORDS = (
    "ссылка на публикацию", "ссылка на пост", "ссылка на твит",
    "ссылка на рекламу", "публикация", "post url",
)

# Порядок важен: более точные совпадения первыми.
# НЕ включаем просто "охват" — это подхватит "Планируемый охват".
REACH_FACT_KEYWORDS = (
    "охват (факт)", "охват факт", "просмотры факт", "просмотры (факт)",
    "реальный охват", "итого охват", "итого просмотры",
    "реальные просмотры", "факт охват", "факт просмотры", "views fact",
)

FINAL_STOP_KEYWORDS = (
    "итого с органикой", "итого с органики", "сумма с учетом ндс",
    "сумма с ндс", "общий прогнозируемый", "общий прогноз",
    "фактический общий охват", "стоимость размещений",
)

ORGANIC_MARKERS = ("органика", "ссылка на публикацию", "ссылка на пост")


def detect_platform(url: str) -> str:
    url = url.lower()
    if "vk.com" in url or "vk.ru" in url:
        return "vk"
    if "t.me" in url or "telegram" in url:
        return "telegram"
    if "instagram.com" in url:
        return "instagram"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "tiktok.com" in url or "vt.tiktok.com" in url:
        return "tiktok"
    if "x.com" in url or "twitter.com" in url:
        return "twitter"
    return "unknown"


def is_post_url(url: str) -> bool:
    """Проверяет что ссылка ведёт на конкретный пост, а не на канал."""
    url = url.strip().lower()
    if not url.startswith("http"):
        return False
    skip_patterns = (
        "disk.yandex", "yandex.ru/i/", "prnt.sc", "drive.google",
        "clck.ru", "yandex.go",
    )
    if any(p in url for p in skip_patterns):
        return False
    if "vk.com" in url or "vk.ru" in url:
        return "wall" in url or "clip" in url
    if "t.me" in url:
        return bool(re.search(r"/\d+$", url))
    if "instagram.com" in url:
        return "/reel/" in url or "/p/" in url
    if "youtube.com" in url or "youtu.be" in url:
        return "/shorts/" in url or "watch?v=" in url or "youtu.be/" in url
    if "tiktok.com" in url:
        # Прямая ссылка: tiktok.com/@user/video/1234
        # Короткие: vt.tiktok.com/XXX, vm.tiktok.com/XXX, tiktok.com/t/XXX
        return (
            "/video/" in url
            or "vt.tiktok" in url
            or "vm.tiktok" in url
            or "/t/" in url
        )
    if "x.com" in url or "twitter.com" in url:
        return "/status/" in url
    return False


def find_col(
    headers: list[str],
    keywords: tuple[str, ...],
    exclude_keywords: tuple[str, ...] = (),
) -> Optional[int]:
    """Ищет колонку по ключевым словам в заголовке.

    exclude_keywords — слова, которые НЕ должны быть в заголовке.
    """
    for i, h in enumerate(headers):
        h_lower = h.strip().lower()
        if any(ex in h_lower for ex in exclude_keywords):
            continue
        for kw in keywords:
            if kw in h_lower:
                return i
    return None


async def get_views(url: str) -> tuple[Optional[int], str]:
    """Идёт в нужный API и возвращает (просмотры, статус).

    Статус: "ok" | "deleted" | "error"
    """
    platform = detect_platform(url)
    try:
        if platform == "vk":
            from src.platforms.vk import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return None, "error"
            return result.views, "ok"

        elif platform == "telegram":
            from src.platforms import telemetr, tgstat
            result = await telemetr.get_post_stats(url)
            telemetr_views = result.views or 0
            fallback = await tgstat.get_post_stats(url)
            tgstat_views = fallback.views or 0

            # Пост удалён: TGStat не находит, но Telemetr отдаёт кеш
            post_deleted = fallback.error in (
                "post_not_found_in_channel", "post_not_found",
            )
            if post_deleted and telemetr_views > 0:
                return telemetr_views, "deleted"
            if post_deleted:
                return None, "error"

            if not fallback.error and tgstat_views > telemetr_views:
                return tgstat_views, "ok"
            return (telemetr_views, "ok") if telemetr_views > 0 else (None, "error")

        elif platform == "instagram":
            from src.platforms.hikerapi import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return None, "error"
            return result.views, "ok"

        elif platform == "youtube":
            from src.platforms.youtube import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return None, "error"
            return result.views, "ok"

        elif platform == "tiktok":
            from src.platforms.tiktok import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return None, "error"
            return result.views, "ok"

        elif platform == "twitter":
            from src.platforms.twitter import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return None, "error"
            return result.views, "ok"

    except Exception as e:
        logger.warning(f"API error for {url}: {e}")

    return None, "error"


async def fetch_views_for_urls(
    url_rows: list[tuple[int, str]],
) -> dict[int, tuple[Optional[int], str]]:
    """По списку (row_id, url) идёт в API площадок и возвращает {row_id: (views, status)}.

    VK — последовательно с паузой 0.4 с (rate limit).
    Остальные площадки — параллельно.
    row_id не интерпретируется, это просто ключ для сопоставления.
    """
    vk_rows = [(r, u) for r, u in url_rows if detect_platform(u) == "vk"]
    other_rows = [(r, u) for r, u in url_rows if detect_platform(u) != "vk"]

    views_map: dict[int, tuple[Optional[int], str]] = {}

    for row_id, url in vk_rows:
        views_map[row_id] = await get_views(url)
        await asyncio.sleep(0.4)

    if other_rows:
        results = await asyncio.gather(*[get_views(u) for _, u in other_rows])
        for (row_id, _), result in zip(other_rows, results):
            views_map[row_id] = result

    return views_map
