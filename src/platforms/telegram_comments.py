"""
Telegram комментарии через telegram92 API (RapidAPI).
Host: telegram92.p.rapidapi.com

Получает комментарии к посту по ссылке вида:
  https://t.me/petrovtel/79228
  https://t.me/channel/123
"""

import re
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import RAPIDAPI_KEY

logger = logging.getLogger(__name__)

TELEGRAM92_HOST = "telegram92.p.rapidapi.com"
TELEGRAM92_BASE = f"https://{TELEGRAM92_HOST}"
HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": TELEGRAM92_HOST,
    "Content-Type": "application/json",
}


@dataclass
class TelegramCommentsResult:
    post_url: str
    top_comments: list[str] = field(default_factory=list)
    total_count: Optional[int] = None
    error: Optional[str] = None


def _parse_tg_url(url: str) -> Optional[tuple[str, str]]:
    """Возвращает (peer, msg_id) из ссылки t.me/channel/123."""
    match = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


async def get_post_comments(post_url: str, limit: int = 5) -> TelegramCommentsResult:
    """Получает топ комментариев к Telegram-посту."""
    if not RAPIDAPI_KEY:
        return TelegramCommentsResult(post_url=post_url, error="RAPIDAPI_KEY не задан")

    parsed = _parse_tg_url(post_url)
    if not parsed:
        return TelegramCommentsResult(
            post_url=post_url,
            error="Не удалось распарсить ссылку Telegram",
        )

    peer, msg_id = parsed

    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "peer": peer,
                "msg_id": msg_id,
                "limit": str(limit),
                "offset": "0",
                "offset_id": "0",
            }
            async with session.get(
                f"{TELEGRAM92_BASE}/api/discuss",
                headers=HEADERS,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    logger.warning(f"telegram92 rate limit for {post_url}, retrying in 5s")
                    await asyncio.sleep(5)
                    async with session.get(
                        f"{TELEGRAM92_BASE}/api/discuss",
                        headers=HEADERS,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp2:
                        if resp2.status != 200:
                            text2 = await resp2.text()
                            return TelegramCommentsResult(
                                post_url=post_url,
                                error=f"HTTP {resp2.status} after retry: {text2[:100]}",
                            )
                        data = await resp2.json()
                elif resp.status != 200:
                    text = await resp.text()
                    return TelegramCommentsResult(
                        post_url=post_url,
                        error=f"HTTP {resp.status}: {text[:100]}",
                    )
                else:
                    data = await resp.json()

        # Структура ответа: {"messages": [...], "count": N}
        messages = data.get("messages", [])
        count = data.get("count")

        texts = []
        for msg in messages:
            # Текст может быть в message или text
            text = (msg.get("message") or msg.get("text") or "").strip()
            if text and len(text) > 3:
                texts.append(text)

        logger.info(
            f"TG comments done: peer={peer}, msg_id={msg_id}, "
            f"got={len(texts)}, total={count}"
        )

        return TelegramCommentsResult(
            post_url=post_url,
            top_comments=texts[:limit],
            total_count=count,
        )

    except Exception as e:
        logger.warning(f"TG comments error for {post_url}: {e}")
        return TelegramCommentsResult(post_url=post_url, error=str(e))
