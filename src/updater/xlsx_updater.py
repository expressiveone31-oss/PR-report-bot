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
                     "ссылка на рекламу", "публикация", "post url")
# Порядок важен: более точные совпадения первыми
# НЕ включаем просто "охват" — это подхватит "Планируемый охват"
REACH_FACT_KEYWORDS = ("охват (факт)", "охват факт", "просмотры факт", "просмотры (факт)",
                       "реальный охват", "итого охват", "итого просмотры",
                       "реальные просмотры", "факт охват", "факт просмотры", "views fact")

# Цвета ячеек
UPDATED_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")   # жёлтый — охват обновлён
ERROR_FILL   = PatternFill(start_color="FFB3B3", end_color="FFB3B3", fill_type="solid")   # красный — нет данных
DELETED_FILL = PatternFill(start_color="FFD0E4", end_color="FFD0E4", fill_type="solid")   # розовый — пост удалён


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


def _find_col(headers: list[str], keywords: tuple,
              exclude_keywords: tuple = ()) -> Optional[int]:
    """Ищет колонку по ключевым словам в заголовке.
    exclude_keywords — слова которые НЕ должны быть в заголовке.
    """
    for i, h in enumerate(headers):
        h_lower = h.strip().lower()
        # Пропускаем если заголовок содержит слова-исключения
        if any(ex in h_lower for ex in exclude_keywords):
            continue
        for kw in keywords:
            if kw in h_lower:
                return i
    return None


async def _get_views(url: str) -> tuple[Optional[int], str]:
    """
    Идёт в нужный API и возвращает (просмотры, статус).
    Статус: "ok" | "deleted" | "error"
    """
    platform = _detect_platform(url)
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
            post_deleted = fallback.error in ("post_not_found_in_channel", "post_not_found")
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


async def update_xlsx(xlsx_bytes: bytes) -> tuple[bytes, dict]:
    """
    Обновляет охваты в xlsx.
    Возвращает (updated_xlsx_bytes, stats).
    stats = {"updated": N, "skipped": N, "errors": N}
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    stats = {"updated": 0, "skipped": 0, "errors": 0, "deleted": 0}

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

        col_url = _find_col(headers, POST_URL_KEYWORDS,
                            exclude_keywords=("канал", "channel", "профил"))
        col_reach = _find_col(headers, REACH_FACT_KEYWORDS,
                              exclude_keywords=("план", "прогноз", "ожидаем", "plan"))

        if col_url is None or col_reach is None:
            logger.warning(f"Sheet '{sheet.title}': col_url={col_url}, col_reach={col_reach} — не найдены нужные колонки")
            logger.warning(f"Sheet '{sheet.title}': headers={headers}")
            continue

        logger.info(f"Sheet '{sheet.title}': url_col={col_url} ('{headers[col_url]}'), reach_col={col_reach} ('{headers[col_reach]}')")

        # Собираем все URL для обработки.
        # Останавливаемся на строке "Итого" — дальше менеджерский блок.
        url_rows = []
        for row_idx in range(header_row_idx + 1, sheet.max_row + 1):
            # Проверяем первые 3 ячейки строки на стоп-слова
            stop = False
            for col_check in range(1, 4):
                cell_val = str(sheet.cell(row=row_idx, column=col_check).value or "").strip().lower()
                if cell_val.startswith("итого") or cell_val.startswith("общий"):
                    stop = True
                    break
            if stop:
                break
            # Проверяем ячейку с URL
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
        views_map: dict[int, tuple[Optional[int], str]] = {}

        for row_idx, url in vk_rows:
            result = await _get_views(url)
            views_map[row_idx] = result
            await asyncio.sleep(0.4)

        if other_rows:
            results = await asyncio.gather(*[_get_views(u) for _, u in other_rows])
            for (row_idx, _), result in zip(other_rows, results):
                views_map[row_idx] = result

        # Проставляем в таблицу
        for row_idx, url in url_rows:
            views, status = views_map.get(row_idx, (None, "error"))
            cell_reach = sheet.cell(row=row_idx, column=col_reach + 1)

            if status == "ok" and views is not None:
                cell_reach.value = views
                cell_reach.fill = UPDATED_FILL
                stats["updated"] += 1
                logger.info(f"Updated row {row_idx}: {views:,} for {url}")
            elif status == "deleted" and views is not None:
                # Пост удалён — проставляем последний известный охват, розовый цвет
                cell_reach.value = views
                cell_reach.fill = DELETED_FILL
                stats["deleted"] += 1
                logger.info(f"Deleted post row {row_idx}: last known {views:,} for {url}")
            else:
                # Нет данных — оставляем пустым, красный цвет
                cell_reach.value = None
                cell_reach.fill = ERROR_FILL
                stats["errors"] += 1
                logger.warning(f"No data for row {row_idx}: {url}")

    # Сохраняем
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue(), stats
