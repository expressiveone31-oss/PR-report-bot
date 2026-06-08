"""
YouTube Data API v3 модуль.
Получает статистику поста по ссылке вида:
  https://youtube.com/shorts/DmranGCKKQE
  https://youtu.be/DmranGCKKQE
  https://youtube.com/watch?v=DmranGCKKQE
"""

import re
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import YOUTUBE_API_KEY

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class ChannelAverage:
    avg_views: Optional[float] = None
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class YouTubePostStats:
    post_url: str
    video_id: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    top_comments: list[str] = field(default_factory=list)
    channel_title: Optional[str] = None
    channel_id: Optional[str] = None
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _parse_youtube_url(url: str) -> Optional[str]:
    """Извлекает video_id из YouTube-ссылки."""
    patterns = [
        r"youtube\.com/shorts/([A-Za-z0-9_-]+)",
        r"youtu\.be/([A-Za-z0-9_-]+)",
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]+)",
        r"youtube\.com/v/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1).split("?")[0].split("&")[0]
    return None


async def _get_uploads_playlist_id(
    session: aiohttp.ClientSession,
    channel_id: str,
) -> Optional[str]:
    """Получает ID плейлиста uploads канала (стоит 1 unit вместо 100 у search)."""
    params = {
        "key": YOUTUBE_API_KEY,
        "id": channel_id,
        "part": "contentDetails",
    }
    async with session.get(f"{YOUTUBE_API_BASE}/channels", params=params) as resp:
        data = await resp.json()
    items = data.get("items", [])
    if not items:
        return None
    return items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")


async def _get_top_comments(
    session: aiohttp.ClientSession,
    video_id: str,
    limit: int = 10,
) -> list[str]:
    """
    Получает топ комментариев к видео через commentThreads.list API.
    Стоимость: 1 unit за запрос.
    Сортировка: по релевантности (relevance).
    """
    try:
        params = {
            "key": YOUTUBE_API_KEY,
            "videoId": video_id,
            "part": "snippet",
            "order": "relevance",  # топ по релевантности
            "maxResults": limit,
            "textFormat": "plainText",
        }
        async with session.get(
            f"{YOUTUBE_API_BASE}/commentThreads", params=params
        ) as resp:
            data = await resp.json()
        
        # Проверка на ошибки API
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown error")
            logger.warning(f"YouTube comments API error: {error_msg}")
            return []
        
        items = data.get("items", [])
        comments = []
        
        for item in items:
            try:
                comment_text = (
                    item.get("snippet", {})
                    .get("topLevelComment", {})
                    .get("snippet", {})
                    .get("textDisplay", "")
                )
                comment_text = comment_text.strip()
                if comment_text and len(comment_text) > 3:
                    comments.append(comment_text)
            except (KeyError, AttributeError):
                continue
        
        logger.info(f"YouTube comments: got {len(comments)} for video_id={video_id}")
        return comments
        
    except Exception as e:
        logger.warning(f"YouTube comments error: {e}")
        return []


async def _get_channel_average(
    session: aiohttp.ClientSession,
    channel_id: str,
    exclude_video_id: str,
    count: int = 20,
) -> ChannelAverage:
    """Берёт последние N видео канала и считает средние показатели.
    Использует playlistItems.list (1 unit) вместо search.list (100 units).
    """
    try:
        # Шаг 1: получаем uploads playlist_id (1 unit)
        uploads_playlist_id = await _get_uploads_playlist_id(session, channel_id)
        if not uploads_playlist_id:
            return ChannelAverage()

        # Шаг 2: получаем последние видео из плейлиста (1 unit)
        params = {
            "key": YOUTUBE_API_KEY,
            "playlistId": uploads_playlist_id,
            "part": "contentDetails",
            "maxResults": count + 1,
        }
        async with session.get(f"{YOUTUBE_API_BASE}/playlistItems", params=params) as resp:
            data = await resp.json()

        items = data.get("items", [])
        video_ids = [
            item["contentDetails"]["videoId"]
            for item in items
            if item.get("contentDetails", {}).get("videoId") != exclude_video_id
        ][:count]

        if not video_ids:
            return ChannelAverage()

        # Получаем статистику по этим видео
        stats_params = {
            "key": YOUTUBE_API_KEY,
            "id": ",".join(video_ids),
            "part": "statistics",
        }
        async with session.get(
            f"{YOUTUBE_API_BASE}/videos", params=stats_params
        ) as resp:
            stats_data = await resp.json()

        stat_items = stats_data.get("items", [])
        if not stat_items:
            return ChannelAverage()

        views_list    = [int(i["statistics"].get("viewCount", 0)) for i in stat_items]
        likes_list    = [int(i["statistics"].get("likeCount", 0)) for i in stat_items]
        comments_list = [int(i["statistics"].get("commentCount", 0)) for i in stat_items]

        n = len(stat_items)
        return ChannelAverage(
            avg_views=round(sum(views_list) / n),
            avg_likes=round(sum(likes_list) / n),
            avg_comments=round(sum(comments_list) / n),
            posts_analyzed=n,
        )

    except Exception as e:
        logger.warning(f"YouTube channel average error: {e}")
        return ChannelAverage()


async def get_post_stats(post_url: str) -> YouTubePostStats:
    video_id = _parse_youtube_url(post_url)
    if not video_id:
        return YouTubePostStats(
            post_url=post_url,
            error="Не удалось распарсить ссылку YouTube",
        )

    if not YOUTUBE_API_KEY:
        return YouTubePostStats(
            post_url=post_url,
            video_id=video_id,
            error="YOUTUBE_API_KEY не задан",
        )

    async with aiohttp.ClientSession() as session:
        params = {
            "key": YOUTUBE_API_KEY,
            "id": video_id,
            "part": "statistics,snippet",
        }
        async with session.get(f"{YOUTUBE_API_BASE}/videos", params=params) as resp:
            data = await resp.json()

        items = data.get("items", [])
        if not items:
            return YouTubePostStats(
                post_url=post_url,
                video_id=video_id,
                error="Видео не найдено",
            )

        item = items[0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})

        views    = int(stats.get("viewCount", 0)) or None
        likes    = int(stats.get("likeCount", 0)) or None
        comments = int(stats.get("commentCount", 0)) or None
        channel_title = snippet.get("channelTitle")
        channel_id    = snippet.get("channelId")

        # Средние по каналу
        channel_avg = ChannelAverage()
        if channel_id:
            channel_avg = await _get_channel_average(session, channel_id, video_id)

        # Топ комментарии — собираем если их >= 5
        top_comments = []
        if comments and comments >= 5:
            logger.info(f"YouTube: fetching comments for video_id={video_id} (has {comments} comments)")
            top_comments = await _get_top_comments(session, video_id, limit=10)

        logger.info(
            f"YouTube done: video_id={video_id}, views={views}, comments={comments}, "
            f"top_comments={len(top_comments)}, channel={channel_title}, avg_views={channel_avg.avg_views}"
        )

    return YouTubePostStats(
        post_url=post_url,
        video_id=video_id,
        views=views,
        likes=likes,
        comments=comments,
        top_comments=top_comments,
        channel_title=channel_title,
        channel_id=channel_id,
        channel_avg=channel_avg,
    )
