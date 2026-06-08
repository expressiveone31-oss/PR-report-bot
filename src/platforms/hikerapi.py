"""
HikerAPI модуль для Instagram.
Документация: https://hikerapi.com/docs

Получает статистику поста/рилса по ссылке вида:
  https://www.instagram.com/p/DU50GS-Erhb/
  https://www.instagram.com/reel/DU8kzzniqC2/
"""

import re
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import HIKERAPI_TOKEN

logger = logging.getLogger(__name__)

HIKERAPI_BASE = "https://api.hikerapi.com/v1"


@dataclass
class ChannelAverage:
    avg_views: Optional[float] = None
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class InstagramPostStats:
    post_url: str
    shortcode: Optional[str] = None
    media_id: Optional[str] = None       # нужен для запроса комментариев
    views: Optional[int] = None          # для видео/рилсов
    likes: Optional[int] = None
    comments: Optional[int] = None
    reposts: Optional[int] = None        # reshares (инста почти не отдаёт)
    saves: Optional[int] = None
    post_type: Optional[str] = None      # photo | video | reel
    author: Optional[str] = None
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _parse_instagram_url(url: str) -> Optional[str]:
    """
    Извлекает shortcode из ссылки Instagram.
    Поддерживает /p/, /reel/, /tv/.
    """
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    return None


async def _get_user_id(session: aiohttp.ClientSession, headers: dict, username: str) -> Optional[int]:
    """Получает user_id по username."""
    try:
        async with session.get(
            f"{HIKERAPI_BASE}/user/by/username",
            headers=headers,
            params={"username": username},
        ) as resp:
            data = await resp.json()
        return data.get("pk") or data.get("id")
    except Exception as e:
        logger.warning(f"HikerAPI: не удалось получить user_id для {username}: {e}")
        return None


async def _get_channel_average(
    session: aiohttp.ClientSession,
    headers: dict,
    user_id: int,
    exclude_shortcode: str,
    count: int = 20,
) -> ChannelAverage:
    """Берёт последние N постов канала и считает средние показатели."""
    try:
        async with session.get(
            f"{HIKERAPI_BASE}/user/medias",
            headers=headers,
            params={"user_id": user_id, "count": count},
        ) as resp:
            data = await resp.json()
    except Exception as e:
        logger.warning(f"HikerAPI: не удалось получить посты канала: {e}")
        return ChannelAverage()

    if not isinstance(data, list):
        return ChannelAverage()

    # Исключаем текущий пост из расчёта нормы
    items = [m for m in data if m.get("code") != exclude_shortcode]
    if not items:
        return ChannelAverage()

    views_list    = [m.get("view_count") or m.get("play_count") or 0 for m in items]
    likes_list    = [m.get("like_count") or 0 for m in items]
    comments_list = [m.get("comment_count") or 0 for m in items]

    n = len(items)
    return ChannelAverage(
        avg_views=round(sum(views_list) / n),
        avg_likes=round(sum(likes_list) / n),
        avg_comments=round(sum(comments_list) / n),
        posts_analyzed=n,
    )


async def get_post_stats(post_url: str) -> InstagramPostStats:
    shortcode = _parse_instagram_url(post_url)
    if not shortcode:
        return InstagramPostStats(post_url=post_url,
                                  error="Не удалось распарсить ссылку Instagram")

    headers = {"x-access-key": HIKERAPI_TOKEN}

    async with aiohttp.ClientSession() as session:
        # Статистика поста
        async with session.get(
            f"{HIKERAPI_BASE}/media/by/code",
            headers=headers,
            params={"code": shortcode},
        ) as resp:
            data = await resp.json()

        if "detail" in data or "error" in data:
            error_msg = data.get("detail") or data.get("error", "HikerAPI error")
            return InstagramPostStats(
                post_url=post_url,
                shortcode=shortcode,
                error=str(error_msg),
            )

        media = data

        # ID поста для запроса комментариев
        media_id = media.get("id") or media.get("pk")

        # Тип поста
        media_type   = media.get("media_type")
        product_type = media.get("product_type", "")
        if product_type in ("reels", "clips") or media_type == 2:
            post_type = "reel"
        elif media_type == 8:
            post_type = "album"
        else:
            post_type = "photo"

        views   = media.get("view_count") or media.get("play_count")
        likes   = media.get("like_count")
        comments = media.get("comment_count")
        saves   = media.get("saved_count")

        edge_reshares = media.get("edge_web_media_to_related_media", {})
        reposts = edge_reshares.get("count") if isinstance(edge_reshares, dict) else None

        author = None
        owner = media.get("owner") or media.get("user")
        if owner:
            author = owner.get("username")

        # Средние по каналу
        channel_avg = ChannelAverage()
        if author:
            user_id = await _get_user_id(session, headers, author)
            if user_id:
                channel_avg = await _get_channel_average(session, headers, user_id, shortcode)

    return InstagramPostStats(
        post_url=post_url,
        shortcode=shortcode,
        media_id=str(media_id) if media_id else None,
        views=views,
        likes=likes,
        comments=comments,
        reposts=reposts,
        saves=saves,
        post_type=post_type,
        author=author,
        channel_avg=channel_avg,
    )
