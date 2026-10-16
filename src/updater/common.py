"""Общие утилиты обновления охватов для xlsx и Google Sheets.

Вынесено, чтобы xlsx_updater и google_sheets_updater работали через
одну и ту же логику детекции колонок, платформ и получения просмотров.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
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

# Параллельные запросы к RapidAPI/HikerAPI часто ловят rate limit.
_NON_VK_CONCURRENCY = 3


@dataclass(frozen=True)
class ViewsResult:
    """Результат запроса охватов для одной ссылки."""
    views: Optional[int]
    status: str          # "ok" | "deleted" | "error" | "no_data"
    detail: str = ""     # человекочитаемая причина / платформа
    platform: str = "unknown"

    def as_tuple(self) -> tuple[Optional[int], str]:
        """Обратная совместимость со старым (views, status)."""
        return self.views, self.status


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


def _short_error(msg: Optional[str], limit: int = 120) -> str:
    text = " ".join(str(msg or "неизвестная ошибка").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_compact_count(raw: str) -> Optional[int]:
    """'48.8K' / '146K' / '1.37K' / '227' → int."""
    s = (raw or "").strip().upper().replace("\xa0", " ").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    elif s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    try:
        return int(float(s.replace(",", ".")) * mult)
    except ValueError:
        return None


async def _telegram_embed_views(url: str) -> Optional[int]:
    """Публичный t.me embed — fallback, когда Telemetr/TGStat молчат."""
    import aiohttp

    parsed = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if not parsed:
        return None
    channel, post_id = parsed.group(1), parsed.group(2)
    embed_url = f"https://t.me/{channel}/{post_id}?embed=1&mode=tme"
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                embed_url,
                headers={"User-Agent": "Mozilla/5.0 pr-report-bot"},
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.text()
    except Exception as e:
        logger.info("TG embed fallback failed for %s: %s", url, e)
        return None
    if "tgme_widget_message_error" in body or "Post not found" in body:
        return None
    match = re.search(
        r'class="tgme_widget_message_views"[^>]*>([^<]+)',
        body,
    )
    if not match:
        match = re.search(r"tgme_widget_message_views[^>]*>([^<]+)", body)
    if not match:
        return None
    return _parse_compact_count(match.group(1))


async def get_views(url: str) -> ViewsResult:
    """Идёт в нужный API и возвращает ViewsResult.

    status:
      ok       — есть число просмотров (>= 0)
      deleted  — пост удалён, но есть last-known views
      error    — API ошибся / токен / rate limit
      no_data  — API ответил, но просмотров нет (фото IG, пустой ответ)
    """
    platform = detect_platform(url)
    try:
        if platform == "vk":
            from src.platforms.vk import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return ViewsResult(None, "error", _short_error(result.error), platform)
            if result.views is None:
                return ViewsResult(None, "no_data", "VK не вернул views", platform)
            return ViewsResult(result.views, "ok", "", platform)

        if platform == "telegram":
            from src.platforms import telemetr, tgstat
            from src.platforms.tgstat import TGStatPostStats

            result = await telemetr.get_post_stats(url)
            telemetr_views = result.views or 0
            telemetr_err = result.error

            # TGStat изолирован: падение/пустой токен НЕ должны затирать Telemetr.
            try:
                fallback = await tgstat.get_post_stats(url)
            except Exception as e:
                logger.warning("TGStat failed for %s: %s", url, e)
                fallback = TGStatPostStats(post_url=url, error=f"{type(e).__name__}: {e}")
            tgstat_views = fallback.views or 0

            post_deleted = fallback.error in (
                "post_not_found_in_channel", "post_not_found",
            )
            if post_deleted and telemetr_views > 0:
                return ViewsResult(telemetr_views, "deleted", "пост удалён, взяли кеш Telemetr", platform)
            if post_deleted and telemetr_views <= 0:
                # Ещё раз проверим публичный embed — TGStat иногда врёт.
                embed_views = await _telegram_embed_views(url)
                if embed_views and embed_views > 0:
                    return ViewsResult(embed_views, "ok", "t.me embed (TGStat said deleted)", platform)
                return ViewsResult(
                    None, "error",
                    _short_error(fallback.error or telemetr_err or "пост не найден"),
                    platform,
                )

            if not fallback.error and tgstat_views > telemetr_views:
                return ViewsResult(tgstat_views, "ok", "TGStat", platform)
            if telemetr_views > 0:
                return ViewsResult(telemetr_views, "ok", "Telemetr", platform)

            # Оба API молчат, но пост может быть жив — пробуем публичный embed.
            embed_views = await _telegram_embed_views(url)
            if embed_views and embed_views > 0:
                logger.info(
                    "TG embed fallback: %s → %s (telemetr=%s/%s tgstat=%s/%s)",
                    url, embed_views, telemetr_views, telemetr_err,
                    tgstat_views, fallback.error,
                )
                return ViewsResult(embed_views, "ok", "t.me embed fallback", platform)

            detail = _short_error(
                telemetr_err or fallback.error or "Telemetr/TGStat/embed не дали просмотров"
            )
            return ViewsResult(None, "error", detail, platform)

        if platform == "instagram":
            from src.platforms.hikerapi import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return ViewsResult(None, "error", _short_error(result.error), platform)
            if result.views is None:
                kind = result.post_type or "пост"
                likes = f", likes={result.likes}" if result.likes is not None else ""
                return ViewsResult(
                    None, "no_data",
                    f"Instagram {kind}: нет view_count/play_count{likes}",
                    platform,
                )
            return ViewsResult(result.views, "ok", "", platform)

        if platform == "youtube":
            from src.platforms.youtube import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return ViewsResult(None, "error", _short_error(result.error), platform)
            if result.views is None:
                return ViewsResult(None, "no_data", "YouTube не вернул views", platform)
            return ViewsResult(result.views, "ok", "", platform)

        if platform == "tiktok":
            from src.platforms.tiktok import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return ViewsResult(None, "error", _short_error(result.error), platform)
            if result.views is None:
                return ViewsResult(None, "no_data", "TikTok не вернул views", platform)
            return ViewsResult(result.views, "ok", "", platform)

        if platform == "twitter":
            from src.platforms.twitter import get_post_stats
            result = await get_post_stats(url)
            if result.error:
                return ViewsResult(None, "error", _short_error(result.error), platform)
            if result.views is None:
                return ViewsResult(None, "no_data", "X/Twitter не вернул views", platform)
            return ViewsResult(result.views, "ok", "", platform)

        return ViewsResult(None, "error", f"неизвестная площадка: {platform}", platform)

    except Exception as e:
        logger.warning("API error for %s: %s", url, e, exc_info=True)
        return ViewsResult(None, "error", _short_error(f"{type(e).__name__}: {e}"), platform)


async def fetch_views_for_urls(
    url_rows: list[tuple[int, str]],
) -> dict[int, ViewsResult]:
    """По списку (row_id, url) идёт в API площадок и возвращает {row_id: ViewsResult}.

    VK — последовательно с паузой 0.4 с (rate limit).
    Остальные — ограничено параллельно (семафор), чтобы не ловить 429 пачкой.
    """
    vk_rows = [(r, u) for r, u in url_rows if detect_platform(u) == "vk"]
    other_rows = [(r, u) for r, u in url_rows if detect_platform(u) != "vk"]

    views_map: dict[int, ViewsResult] = {}

    for row_id, url in vk_rows:
        views_map[row_id] = await get_views(url)
        await asyncio.sleep(0.4)

    if other_rows:
        sem = asyncio.Semaphore(_NON_VK_CONCURRENCY)

        async def _limited(u: str) -> ViewsResult:
            async with sem:
                return await get_views(u)

        results = await asyncio.gather(*[_limited(u) for _, u in other_rows])
        for (row_id, _), result in zip(other_rows, results):
            views_map[row_id] = result

    return views_map


def unpack_views_entry(entry) -> ViewsResult:
    """Нормализует ViewsResult или старый tuple (views, status[, detail])."""
    if isinstance(entry, ViewsResult):
        return entry
    if isinstance(entry, tuple):
        views = entry[0] if len(entry) > 0 else None
        status = entry[1] if len(entry) > 1 else "error"
        detail = entry[2] if len(entry) > 2 else ""
        return ViewsResult(views, status, detail or "")
    return ViewsResult(None, "error", "некорректный ответ fetch_views")
