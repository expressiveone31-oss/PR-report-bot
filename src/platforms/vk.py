"""
VK API модуль.
Получает статистику поста, топ комментариев и средние показатели канала.
"""

import re
import ssl
import certifi
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import VK_ACCESS_TOKEN

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

VK_API_VERSION = "5.199"
VK_API_BASE = "https://api.vk.com/method"


@dataclass
class ChannelAverage:
    """Средние показатели канала по последним N постам."""
    avg_views: Optional[float] = None
    avg_likes: Optional[float] = None
    avg_reposts: Optional[float] = None
    avg_comments: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class VKPostStats:
    post_url: str
    owner_id: int
    post_id: int
    views: Optional[int] = None
    likes: Optional[int] = None
    reposts: Optional[int] = None
    comments: Optional[int] = None
    channel_title: Optional[str] = None
    top_comments: list[str] = field(default_factory=list)
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _parse_vk_url(url: str) -> Optional[tuple[int, int]]:
    match = re.search(r"wall(-?\d+)_(\d+)", url)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _parse_vk_clip_url(url: str) -> Optional[tuple[int, int]]:
    match = re.search(r"clip(-?\d+)_(\d+)", url)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


async def _get_channel_average(session: aiohttp.ClientSession,
                                owner_id: int,
                                exclude_post_id: int) -> ChannelAverage:
    """Берёт последние 20 постов канала и считает средние показатели."""
    params = {
        "owner_id": owner_id,
        "count": 20,
        "filter": "owner",       # только посты самого паблика, без репостов
        "access_token": VK_ACCESS_TOKEN,
        "v": VK_API_VERSION,
    }
    async with session.get(f"{VK_API_BASE}/wall.get", params=params) as resp:
        data = await resp.json()

    if "error" in data:
        return ChannelAverage()

    items = data.get("response", {}).get("items", [])
    # Исключаем текущий пост из расчёта нормы
    items = [i for i in items if i.get("id") != exclude_post_id]

    if not items:
        return ChannelAverage()

    views_list =    [i.get("views", {}).get("count", 0) for i in items]
    likes_list =    [i.get("likes", {}).get("count", 0) for i in items]
    reposts_list =  [i.get("reposts", {}).get("count", 0) for i in items]
    comments_list = [i.get("comments", {}).get("count", 0) for i in items]

    n = len(items)
    return ChannelAverage(
        avg_views=round(sum(views_list) / n),
        avg_likes=round(sum(likes_list) / n),
        avg_reposts=round(sum(reposts_list) / n),
        avg_comments=round(sum(comments_list) / n),
        posts_analyzed=n,
    )


async def get_post_stats(post_url: str) -> VKPostStats:
    parsed = _parse_vk_url(post_url)
    clip_parsed = _parse_vk_clip_url(post_url)

    if not parsed and clip_parsed:
        # VK clip: используем video.get
        owner_id, video_id = clip_parsed
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(connector=connector) as session:
            params = {
                "videos": f"{owner_id}_{video_id}",
                "extended": 1,
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
            }
            async with session.get(f"{VK_API_BASE}/video.get", params=params) as resp:
                data = await resp.json()
            if "error" in data:
                return VKPostStats(post_url=post_url, owner_id=owner_id, post_id=video_id,
                                   error=data["error"].get("error_msg", "VK API error"))
            items = data.get("response", {}).get("items", [])
            if not items:
                return VKPostStats(post_url=post_url, owner_id=owner_id, post_id=video_id,
                                   error="Клип не найден")
            item = items[0]
            # Название группы
            channel_title = None
            if owner_id < 0:
                gp = {"group_ids": str(-owner_id), "fields": "name",
                      "access_token": VK_ACCESS_TOKEN, "v": VK_API_VERSION}
                async with session.get(f"{VK_API_BASE}/groups.getById", params=gp) as resp:
                    gd = await resp.json()
                groups = gd.get("response", {}).get("groups") or gd.get("response", [])
                if groups:
                    channel_title = groups[0].get("name")
        return VKPostStats(
            post_url=post_url,
            owner_id=owner_id,
            post_id=video_id,
            views=item.get("views"),
            likes=item.get("likes", {}).get("count") if isinstance(item.get("likes"), dict) else item.get("likes"),
            reposts=item.get("reposts", {}).get("count") if isinstance(item.get("reposts"), dict) else item.get("reposts"),
            comments=item.get("comments", {}).get("count") if isinstance(item.get("comments"), dict) else item.get("comments"),
            channel_title=channel_title,
            channel_avg=ChannelAverage(),
        )

    if not parsed:
        return VKPostStats(post_url=post_url, owner_id=0, post_id=0,
                           error="Не удалось распарсить ссылку VK")

    owner_id, post_id = parsed

    connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
    async with aiohttp.ClientSession(connector=connector) as session:

        # Статистика поста
        params = {
            "posts": f"{owner_id}_{post_id}",
            "access_token": VK_ACCESS_TOKEN,
            "v": VK_API_VERSION,
        }
        async with session.get(f"{VK_API_BASE}/wall.getById", params=params) as resp:
            data = await resp.json()

        if "error" in data:
            return VKPostStats(
                post_url=post_url,
                owner_id=owner_id,
                post_id=post_id,
                error=data["error"].get("error_msg", "VK API error"),
            )

        response = data.get("response", {})
        if isinstance(response, dict):
            items = response.get("items", [])
        else:
            items = response

        if not items:
            return VKPostStats(post_url=post_url, owner_id=owner_id, post_id=post_id,
                               error="Пост не найден")

        item = items[0]
        views = item.get("views", {}).get("count")
        likes = item.get("likes", {}).get("count")
        reposts = item.get("reposts", {}).get("count")
        comments_count = item.get("comments", {}).get("count")

        # Топ комментарии
        top_comments = []
        if comments_count and comments_count > 0:
            comments_params = {
                "owner_id": owner_id,
                "post_id": post_id,
                "count": 10,
                "sort": "desc",
                "need_likes": 1,
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
            }
            async with session.get(f"{VK_API_BASE}/wall.getComments",
                                   params=comments_params) as resp:
                comments_data = await resp.json()

            if "response" in comments_data:
                comment_items = comments_data["response"].get("items", [])
                comment_items.sort(key=lambda c: c.get("likes", {}).get("count", 0), reverse=True)
                for c in comment_items[:5]:
                    text = c.get("text", "").strip()
                    if text:
                        top_comments.append(text)

        # Средние показатели канала
        channel_avg = await _get_channel_average(session, owner_id, post_id)

        # Название группы/паблика (owner_id отрицательный = группа)
        channel_title = None
        if owner_id < 0:
            group_params = {
                "group_ids": str(-owner_id),
                "fields": "name",
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
            }
            async with session.get(f"{VK_API_BASE}/groups.getById",
                                   params=group_params) as resp:
                group_data = await resp.json()
            groups = group_data.get("response", {}).get("groups") or group_data.get("response", [])
            if groups:
                channel_title = groups[0].get("name")

    return VKPostStats(
        post_url=post_url,
        owner_id=owner_id,
        post_id=post_id,
        views=views,
        likes=likes,
        reposts=reposts,
        comments=comments_count,
        channel_title=channel_title,
        top_comments=top_comments,
        channel_avg=channel_avg,
    )
