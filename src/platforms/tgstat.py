"""
TGStat API модуль.
Документация: https://tgstat.ru/api/docs

Получает статистику поста по ссылке вида:
  https://t.me/rhymestg/194635
  https://t.me/lider/19961
"""

import re
import aiohttp
from dataclasses import dataclass
from typing import Optional
from src.config import TGSTAT_TOKEN

TGSTAT_BASE = "https://api.tgstat.ru"


@dataclass
class TGStatPostStats:
    post_url: str
    views: Optional[int] = None
    forwards: Optional[int] = None       # репосты в публичные каналы
    reactions_count: Optional[int] = None
    comments: Optional[int] = None
    channel_title: Optional[str] = None
    channel_subscribers: Optional[int] = None
    error: Optional[str] = None


def _parse_tg_url(url: str) -> Optional[tuple[str, str]]:
    """
    Возвращает (channel_username, post_id) из ссылки t.me/channel/123.
    """
    match = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


def _parse_item(post_url: str, item: dict) -> TGStatPostStats:
    """Собирает TGStatPostStats из объекта поста TGStat."""
    reactions_raw = item.get("reactions")
    reactions_count = None
    if isinstance(reactions_raw, list):
        reactions_count = sum(r.get("count", 0) for r in reactions_raw)
    elif isinstance(reactions_raw, int):
        reactions_count = reactions_raw

    return TGStatPostStats(
        post_url=post_url,
        views=item.get("viewsCount") or item.get("views"),
        forwards=item.get("forwardsCount") or item.get("forwards"),
        reactions_count=reactions_count,
        comments=item.get("commentsCount") or item.get("comments"),
        channel_title=item.get("channelTitle"),
        channel_subscribers=item.get("channelMembersCount"),
    )


async def _get_channel_title(session: aiohttp.ClientSession, channel_id: str) -> Optional[str]:
    """Получает название канала через channels/stat."""
    try:
        params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
        async with session.get(f"{TGSTAT_BASE}/channels/stat", params=params) as resp:
            data = await resp.json()
        if data.get("status") == "ok":
            return data.get("response", {}).get("title")
    except Exception:
        pass
    return None


async def get_post_stats(post_url: str) -> TGStatPostStats:
    parsed = _parse_tg_url(post_url)
    if not parsed:
        return TGStatPostStats(post_url=post_url,
                               error="Не удалось распарсить ссылку Telegram")

    channel, post_id = parsed

    async with aiohttp.ClientSession() as session:
        # Сначала пробуем быстрый метод posts/get
        params = {"token": TGSTAT_TOKEN, "postLink": post_url}
        async with session.get(f"{TGSTAT_BASE}/posts/get", params=params) as resp:
            data = await resp.json()

        if data.get("status") == "ok":
            result = _parse_item(post_url, data.get("response", {}))
            # posts/get возвращает channelTitle — подставляем если есть
            if not result.channel_title:
                result.channel_title = await _get_channel_title(session, channel)
            return result

        # posts/get не нашёл — ищем через channels/posts с пагинацией
        if data.get("error") == "post_not_found":
            post_id_int = int(post_id)
            channel_title = await _get_channel_title(session, channel)

            for offset in (0, 50, 100, 150):
                params2 = {
                    "token": TGSTAT_TOKEN,
                    "channelId": channel,
                    "limit": 50,
                    "offset": offset,
                }
                async with session.get(
                    f"{TGSTAT_BASE}/channels/posts", params=params2
                ) as resp2:
                    data2 = await resp2.json()

                if data2.get("status") != "ok":
                    break

                items = data2.get("response", {}).get("items", [])
                if not items:
                    break

                for item in items:
                    link = item.get("link", "")
                    if link.endswith(f"/{post_id}") or link.endswith(f"/{post_id_int}"):
                        result = _parse_item(post_url, item)
                        result.channel_title = channel_title
                        return result

                oldest = items[-1].get("link", "")
                oldest_id_match = re.search(r"/(\d+)$", oldest)
                if oldest_id_match and int(oldest_id_match.group(1)) < post_id_int:
                    break

            return TGStatPostStats(post_url=post_url, error="post_not_found_in_channel")

    return TGStatPostStats(
        post_url=post_url,
        error=data.get("error", "TGStat API error"),
    )
