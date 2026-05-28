"""
Модуль обновления охватов в xlsx медиаплане.

Алгоритм:
1. Читает xlsx, находит колонки со ссылками на публикации и «Охват (факт)»
2. По каждой ссылке идёт в API нужной платформы
3. Проставляет фактический охват в ячейку
4. Возвращает обновлённый xlsx как bytes
"""

import io
import re
import asyncio
import logging
from typing import Optional

import openpyxl
from openpyxl.styles import PatternFill

logger = logging.getLogger(__name__)

# Ключевые слова для поиска нужных колонок
POST_URL_KEYWORDS = ("ссылка на публикацию", "ссылка на пост", "ссылка на твит",
                     "ссылка на рекламу", "post url", "link")
REACH_FACT_KEYWORDS = ("охват (факт)", "охват факт", "реальный охват", "факт охват",
                       "views fact", "охват")

# Жёлтая заливка для обновлённых ячеек
UPDATED_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
ERROR_FILL = PatternFill(start_color="FFB3B3", end_color="FFB3B3", fill_type="solid")


def _detect_platform(url: str) -> str:
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


def _is_post_url(url: str) -> bool:
    """Проверяет что ссылка ведёт на конкретный пост, а не на канал."""
    url = url.strip().lower()
    if not url.startswith("http"):
        return False
    # Исключаем ссылки на каналы/профили без ID поста
    skip_patterns = (
        "disk.yandex", "yandex.ru/i/", "prnt.sc", "drive.google",
        "clck.ru", "yandex.go",
    )
    if any(p in url for p in skip_patterns):
        return False
    # VK wall — должен содержать _
    if ("vk.com" in url or "vk.ru" in url):
        return "wall" in url or "clip" in url
    # Telegram — должен содержать /число в конце
    if "t.me" in url:
        return bool(re.search(r"/\d+$", url))
    # Instagram — reel или /p/
    if "instagram.com" in url:
        return "/reel/" in url or "/p/" in url
    # YouTube shorts
    if "youtube.com" in url or "youtu.be" in url:
        return "/shorts/" in url or "watch?v=" in url or "youtu.be/" in url
    # TikTok video
    if "tiktok.com" in url:
        return "/video/" in url or "vt.tiktok" in url
    # Twitter status
    if "x.com" in url or "twitter.com" in url:
        return "/status/" in url
    return False


def _find_col(headers: list[str], keywords: tuple) -> Optional[int]:
    """Ищет колонку по ключевым словам в заголовке."""
    for i, h in enumerate(headers):
        h_lower = h.strip().lower()
        for kw in keywords:
            if kw in h_lower:
                return i
    return None


async def _get_views(url: str) -> Optional[int]:
    """Идёт в нужный API и возвращает просмотры. None если не удалось."""
    platform = _detect_platform(url)
    try:
        if platform == "vk":
            from src.platforms.vk import get_post_stats
            result = await get_post_stats(url)
            return result.views

        elif platform == "telegram":
            from src.platforms import telemetr, tgstat
            result = await telemetr.get_post_stats(url)
            telemetr_views = result.views or 0
            # TGStat cross-check
            fallback = await tgstat.get_post_stats(url)
            tgstat_views = fallback.views or 0
            if not fallback.error and tgstat_views > telemetr_views:
                return tgstat_views
            return telemetr_views if telemetr_views > 0 else None

        elif platform == "instagram":
            from src.platforms.hikerapi import get_post_stats
            result = await get_post_stats(url)
            return result.views

        elif platform == "youtube":
            from src.platforms.youtube import get_post_stats
            result = await get_post_stats(url)
            return result.views

        elif platform == "tiktok":
            from src.platforms.tiktok import get_post_stats
            result = await get_post_stats(url)
            return result.views

        elif platform == "twitter":
            from src.platforms.twitter import get_post_stats
            result = await get_post_stats(url)
            return result.views

    except Exception as e:
        logger.warning(f"API error for {url}: {e}")

    return None


async def update_xlsx(xlsx_bytes: bytes) -> tuple[bytes, dict]:
    """
    Обновляет охваты в xlsx.
    Возвращает (updated_xlsx_bytes, stats).
    stats = {"updated": N, "skipped": N, "errors": N}
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    stats = {"updated": 0, "skipped": 0, "errors": 0}

    for sheet in wb.worksheets:
        # Ищем строку-заголовок
        header_row_idx = None
        headers = []
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), 1):
            row_strs = [str(c).strip() if c is not None else "" for c in row]
            row_lower = " ".join(row_strs).lower()
            if any(kw in row_lower for kw in ("ссылка на публикацию", "ссылка на пост",
                                               "ссылка на твит", "охват (факт)")):
                header_row_idx = row_idx
                headers = row_strs
                break

        if header_row_idx is None:
            logger.info(f"Sheet '{sheet.title}': no header found, skipping")
            continue

        col_url = _find_col(headers, POST_URL_KEYWORDS)
        col_reach = _find_col(headers, REACH_FACT_KEYWORDS)

        if col_url is None or col_reach is None:
            logger.info(f"Sheet '{sheet.title}': col_url={col_url}, col_reach={col_reach}, skipping")
            continue

        logger.info(f"Sheet '{sheet.title}': url_col={col_url}, reach_col={col_reach}")

        # Собираем все URL для обработки (VK последовательно, остальные параллельно)
        url_rows = []
        for row_idx in range(header_row_idx + 1, sheet.max_row + 1):
            cell_url = sheet.cell(row=row_idx, column=col_url + 1)
            url = str(cell_url.value).strip() if cell_url.value else ""
            if _is_post_url(url):
                url_rows.append((row_idx, url))

        if not url_rows:
            continue

        # VK — последовательно с паузой
        vk_rows = [(r, u) for r, u in url_rows if _detect_platform(u) == "vk"]
        other_rows = [(r, u) for r, u in url_rows if _detect_platform(u) != "vk"]

        # Получаем просмотры
        views_map: dict[int, Optional[int]] = {}

        for row_idx, url in vk_rows:
            views = await _get_views(url)
            views_map[row_idx] = views
            await asyncio.sleep(0.4)

        if other_rows:
            results = await asyncio.gather(*[_get_views(u) for _, u in other_rows])
            for (row_idx, _), views in zip(other_rows, results):
                views_map[row_idx] = views

        # Проставляем в таблицу
        for row_idx, url in url_rows:
            views = views_map.get(row_idx)
            cell_reach = sheet.cell(row=row_idx, column=col_reach + 1)

            if views is not None:
                cell_reach.value = views
                cell_reach.fill = UPDATED_FILL
                stats["updated"] += 1
                logger.info(f"Updated row {row_idx}: {views:,} for {url}")
            else:
                # Оставляем пустым, помечаем красным
                cell_reach.value = None
                cell_reach.fill = ERROR_FILL
                stats["errors"] += 1
                logger.warning(f"No data for row {row_idx}: {url}")

    # Сохраняем
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue(), stats
