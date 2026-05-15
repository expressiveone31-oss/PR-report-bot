"""
Pyrogram модуль — парсинг комментариев из публичных Telegram-каналов.
Работает от имени пользовательского аккаунта (не бота).
Сессия хранится в файле session/userbot.session.
"""

import re
import os
import logging
from dataclasses import dataclass, field
from typing import Optional
from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelPrivate, UsernameNotOccupied
from src.config import PYROGRAM_API_ID, PYROGRAM_API_HASH, PYROGRAM_SESSION_STRING

logger = logging.getLogger(__name__)

SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "session", "userbot")


@dataclass
class TGComments:
    post_url: str
    comments_count: Optional[int] = None
    top_comments: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _parse_tg_url(url: str) -> Optional[tuple[str, int]]:
    """Возвращает (channel_username, message_id)."""
    match = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if match:
        return match.group(1), int(match.group(2))
    return None


def get_client() -> Client:
    """Создаёт клиент Pyrogram — из string session (Railway) или файла (локально)."""
    if PYROGRAM_SESSION_STRING:
        return Client(
            name="userbot",
            api_id=PYROGRAM_API_ID,
            api_hash=PYROGRAM_API_HASH,
            session_string=PYROGRAM_SESSION_STRING,
        )
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    return Client(
        name=SESSION_PATH,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
    )


async def get_post_comments(post_url: str, limit: int = 10) -> TGComments:
    """
    Получает топ комментариев к посту в публичном Telegram-канале.
    Возвращает топ по реакциям/лайкам (или просто последние если реакций нет).
    """
    parsed = _parse_tg_url(post_url)
    if not parsed:
        return TGComments(post_url=post_url, error="Не удалось распарсить ссылку")

    channel, message_id = parsed

    try:
        async with get_client() as app:
            # Получаем дискуссию (тред комментариев) к посту
            comments = []
            async for message in app.get_discussion_replies(channel, message_id):
                text = message.text or message.caption or ""
                text = text.strip()
                if not text:
                    continue
                reactions_count = 0
                if message.reactions:
                    for r in message.reactions.reactions:
                        reactions_count += r.count
                comments.append((reactions_count, text))
                if len(comments) >= 50:  # берём 50, потом сортируем
                    break

            # Сортируем по реакциям
            comments.sort(key=lambda x: x[0], reverse=True)
            top = [text for _, text in comments[:limit]]

            return TGComments(
                post_url=post_url,
                comments_count=len(comments),
                top_comments=top,
            )

    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s для {post_url}")
        return TGComments(post_url=post_url, error=f"FloodWait: подождите {e.value} сек")
    except (ChannelPrivate, UsernameNotOccupied) as e:
        return TGComments(post_url=post_url, error=f"Канал недоступен: {e}")
    except Exception as e:
        logger.error(f"Pyrogram error для {post_url}: {e}", exc_info=True)
        return TGComments(post_url=post_url, error=str(e))
