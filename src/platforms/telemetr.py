"""
Telemetr API модуль.
Документация: https://api.telemetr.me/doc
"""

import re
import ssl
import certifi
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import TELEMETR_TOKEN

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
TELEMETR_BASE = "https://api.telemetr.me"


@dataclass
class ChannelAverage:
    avg_views: Optional[float] = None
    avg_forwards: Optional[float] = None
    avg_reactions: Optional[float] = None
    avg_comments: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class TelemetrPostStats:
    post_url: str
    views: Optional[int] = None
    forwards: Optional[int] = None
    reactions: Optional[int] = None
    comments: Optional[int] = None
    channel_title: Optional[str] = None
    channel_subscribers: Optional[int] = None
    published_at: Optional[str] = None
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _parse_tg_url(url: str) -> Optional[tuple[str, str]]:
    match = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


async def _get_channel_average(session: aiohttp.ClientSession,
                                headers: dict,
                                channel_id: str,
                                exclude_post_id: str) -> ChannelAverage:
    """Берёт последние 20 постов канала и считает средние показатели."""
    params = {"channelId": channel_id, "limit": 20}
    async with session.get(
        f"{TELEMETR_BASE}/channels/posts",
        headers=headers,
        params=params,
    ) as resp:
        data = await resp.json()

    if data.get("status") != "ok":
        return ChannelAverage()

    items = data.get("response", {}).get("items", [])
    # Исключаем текущий пост
    items = [i for i in items if str(i.get("id")) != str(exclude_post_id)]

    if not items:
        return ChannelAverage()

    def safe_stat(item, key):
        return item.get("stats", {}).get(key) or 0

    n = len(items)
    return ChannelAverage(
        avg_views=round(sum(safe_stat(i, "views") for i in items) / n),
        avg_forwards=round(sum(safe_stat(i, "forwards") for i in items) / n),
        avg_reactions=round(sum(safe_stat(i, "reactions") for i in items) / n),
        avg_comments=round(sum(safe_stat(i, "comments") for i in items) / n),
        posts_analyzed=n,
    )


async def get_post_stats(post_url: str) -> TelemetrPostStats:
    parsed = _parse_tg_url(post_url)
    if not parsed:
        return TelemetrPostStats(
            post_url=post_url,
            error="Не удалось распарсить ссылку Telegram",
        )

    channel_id, post_id = parsed
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
    async with aiohttp.ClientSession(connector=connector) as session:

        # Статистика конкретного поста
        async with session.get(
            f"{TELEMETR_BASE}/channels/posts/get",
            headers=headers,
            params={"channelId": channel_id, "postId": post_id},
        ) as resp:
            post_data = await resp.json()

        # Статистика канала
        async with session.get(
            f"{TELEMETR_BASE}/channels/stat",
            headers=headers,
            params={"channelId": channel_id},
        ) as resp:
            channel_data = await resp.json()

        # Средние показатели канала
        channel_avg = await _get_channel_average(session, headers, channel_id, post_id)

    if post_data.get("status") != "ok":
        return TelemetrPostStats(
            post_url=post_url,
            error=post_data.get("response", {}).get("message", "Telemetr API error"),
        )

    item = post_data.get("response", {})
    stats = item.get("stats", {})

    channel_title = None
    channel_subscribers = None
    if channel_data.get("status") == "ok":
        ch = channel_data.get("response", {})
        channel_title = ch.get("title")
        channel_subscribers = ch.get("participants_count")

    return TelemetrPostStats(
        post_url=post_url,
        views=stats.get("views"),
        forwards=stats.get("forwards"),
        reactions=stats.get("reactions"),
        comments=stats.get("comments"),
        channel_title=channel_title,
        channel_subscribers=channel_subscribers,
        published_at=str(
            item.get("date") or item.get("post_date") or item.get("published_at")
            or item.get("created_at") or item.get("timestamp") or item.get("created") or ""
        ) or None,
        channel_avg=channel_avg,
    )
