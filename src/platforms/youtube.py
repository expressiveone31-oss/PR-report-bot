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


async def _get_channel_average(
    session: aiohttp.ClientSession,
    channel_id: str,
    exclude_video_id: str,
    count: int = 20,
) -> ChannelAverage:
    """Берёт последние N видео канала и считает средние показатели."""
    try:
        # Получаем последние видео канала
        params = {
            "key": YOUTUBE_API_KEY,
            "channelId": channel_id,
            "part": "id",
            "order": "date",
            "maxResults": count + 1,
            "type": "video",
        }
        async with session.get(f"{YOUTUBE_API_BASE}/search", params=params) as resp:
            data = await resp.json()

        items = data.get("items", [])
        video_ids = [
            item["id"]["videoId"]
            for item in items
            if item.get("id", {}).get("videoId") != exclude_video_id
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

        logger.info(
            f"YouTube done: video_id={video_id}, views={views}, "
            f"channel={channel_title}, avg_views={channel_avg.avg_views}"
        )

    return YouTubePostStats(
        post_url=post_url,
        video_id=video_id,
        views=views,
        likes=likes,
        comments=comments,
        channel_title=channel_title,
        channel_id=channel_id,
        channel_avg=channel_avg,
    )
