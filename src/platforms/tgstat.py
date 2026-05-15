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


async def get_post_stats(post_url: str) -> TGStatPostStats:
    parsed = _parse_tg_url(post_url)
    if not parsed:
        return TGStatPostStats(post_url=post_url,
                               error="Не удалось распарсить ссылку Telegram")

    channel, post_id = parsed

    params = {
        "token": TGSTAT_TOKEN,
        "postLink": post_url,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{TGSTAT_BASE}/posts/get", params=params
        ) as resp:
            data = await resp.json()

    if data.get("status") != "ok":
        return TGStatPostStats(
            post_url=post_url,
            error=data.get("error", "TGStat API error"),
        )

    item = data.get("response", {})

    # Реакции могут быть списком объектов или суммой
    reactions_raw = item.get("reactions")
    reactions_count = None
    if isinstance(reactions_raw, list):
        reactions_count = sum(r.get("count", 0) for r in reactions_raw)
    elif isinstance(reactions_raw, int):
        reactions_count = reactions_raw

    return TGStatPostStats(
        post_url=post_url,
        views=item.get("viewsCount"),
        forwards=item.get("forwardsCount"),
        reactions_count=reactions_count,
        comments=item.get("commentsCount"),
        channel_title=item.get("channelTitle"),
        channel_subscribers=item.get("channelMembersCount"),
    )
