"""
TikTok модуль через Tikfly API (RapidAPI).
Документация: https://docs.tikfly.io

Получает статистику поста по ссылке вида:
  https://www.tiktok.com/@user/video/7637168738818821396
  https://vt.tiktok.com/ZS94KjMwb/
"""

import re
import logging
import aiohttp
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from src.config import TIKTOK_RAPIDAPI_KEY

logger = logging.getLogger(__name__)

TIKFLY_BASE = "https://tiktok-api23.p.rapidapi.com"
HEADERS = {
    "x-rapidapi-key": TIKTOK_RAPIDAPI_KEY,
    "x-rapidapi-host": "tiktok-api23.p.rapidapi.com",
}


@dataclass
class ChannelAverage:
    avg_views: Optional[float] = None
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    avg_shares: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class TikTokPostStats:
    post_url: str
    video_id: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    channel_title: Optional[str] = None
    channel_username: Optional[str] = None
    published_at: Optional[str] = None
    top_comments: list[str] = field(default_factory=list)
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _extract_video_id(url: str) -> Optional[str]:
    """Извлекает video_id из TikTok URL."""
    # Прямая ссылка: tiktok.com/@user/video/1234567890
    match = re.search(r"/video/(\d+)", url)
    if match:
        return match.group(1)
    # Короткая ссылка vt.tiktok.com — нужен редирект
    return None


async def _resolve_short_url(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Разрезолвает короткую ссылку vt.tiktok.com → полный URL."""
    try:
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            final_url = str(resp.url)
            return _extract_video_id(final_url)
    except Exception as e:
        logger.warning(f"TikTok short URL resolve failed for {url}: {e}")
        return None


async def _get_channel_average(
    session: aiohttp.ClientSession,
    unique_id: str,
    exclude_video_id: str,
    count: int = 20,
) -> ChannelAverage:
    """Берёт последние N постов канала и считает средние."""
    try:
        params = {"uniqueId": unique_id, "count": count}
        async with session.get(
            f"{TIKFLY_BASE}/api/user/posts",
            headers=HEADERS,
            params=params,
        ) as resp:
            data = await resp.json()

        items = data.get("itemList", [])
        items = [i for i in items if i.get("id") != exclude_video_id]
        if not items:
            return ChannelAverage()

        def safe_int(val) -> int:
            try:
                return int(val or 0)
            except (TypeError, ValueError):
                return 0

        views_list    = [safe_int(i.get("statsV2", {}).get("playCount") or i.get("stats", {}).get("playCount")) for i in items]
        likes_list    = [safe_int(i.get("statsV2", {}).get("diggCount") or i.get("stats", {}).get("diggCount")) for i in items]
        comments_list = [safe_int(i.get("statsV2", {}).get("commentCount") or i.get("stats", {}).get("commentCount")) for i in items]
        shares_list   = [safe_int(i.get("statsV2", {}).get("shareCount") or i.get("stats", {}).get("shareCount")) for i in items]

        n = len(items)
        return ChannelAverage(
            avg_views=round(sum(views_list) / n),
            avg_likes=round(sum(likes_list) / n),
            avg_comments=round(sum(comments_list) / n),
            avg_shares=round(sum(shares_list) / n),
            posts_analyzed=n,
        )
    except Exception as e:
        logger.warning(f"TikTok channel average error for {unique_id}: {e}")
        return ChannelAverage()


async def _get_top_comments(
    session: aiohttp.ClientSession,
    video_id: str,
    limit: int = 5,
) -> list[str]:
    """Получает топ комментариев к посту."""
    try:
        params = {"videoId": video_id, "count": limit}
        async with session.get(
            f"{TIKFLY_BASE}/api/post/comments",
            headers=HEADERS,
            params=params,
        ) as resp:
            data = await resp.json()

        comments = data.get("comments", [])
        texts = []
        for c in comments[:limit]:
            text = c.get("text", "").strip()
            if text:
                texts.append(text)
        return texts
    except Exception as e:
        logger.warning(f"TikTok comments error for {video_id}: {e}")
        return []


async def get_post_stats(post_url: str, fetch_comments: bool = False) -> TikTokPostStats:
    if not TIKTOK_RAPIDAPI_KEY:
        return TikTokPostStats(post_url=post_url, error="TIKTOK_RAPIDAPI_KEY не задан")

    async with aiohttp.ClientSession() as session:
        # Резолвим video_id
        video_id = _extract_video_id(post_url)
        if not video_id and ("vt.tiktok.com" in post_url or "vm.tiktok.com" in post_url):
            video_id = await _resolve_short_url(session, post_url)

        if not video_id:
            return TikTokPostStats(post_url=post_url, error="Не удалось извлечь video_id из ссылки")

        # Статистика поста
        params = {"videoId": video_id}
        async with session.get(
            f"{TIKFLY_BASE}/api/post/detail",
            headers=HEADERS,
            params=params,
        ) as resp:
            data = await resp.json()

        if data.get("statusCode") == 10204 or not data.get("itemInfo"):
            return TikTokPostStats(post_url=post_url, video_id=video_id, error="Пост не найден")

        item = data.get("itemInfo", {}).get("itemStruct", {})
        stats = item.get("statsV2") or item.get("stats", {})
        author = item.get("author", {})

        def safe_int(val) -> Optional[int]:
            try:
                v = int(val or 0)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

        views    = safe_int(stats.get("playCount"))
        likes    = safe_int(stats.get("diggCount"))
        comments = safe_int(stats.get("commentCount"))
        shares   = safe_int(stats.get("shareCount"))
        channel_title    = author.get("nickname")
        channel_username = author.get("uniqueId")
        create_time = item.get("createTime")
        published_at = (
            datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
            if create_time else None
        )

        # Средние по каналу
        channel_avg = ChannelAverage()
        if channel_username:
            channel_avg = await _get_channel_average(session, channel_username, video_id)

        # Комментарии (опционально)
        top_comments = []
        if fetch_comments and comments and comments >= 5:
            top_comments = await _get_top_comments(session, video_id)

        logger.info(
            f"TikTok done: video_id={video_id}, views={views}, "
            f"channel={channel_title}, avg_views={channel_avg.avg_views}"
        )

    return TikTokPostStats(
        post_url=post_url,
        video_id=video_id,
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        channel_title=channel_title,
        channel_username=channel_username,
        published_at=published_at,
        top_comments=top_comments,
        channel_avg=channel_avg,
    )
