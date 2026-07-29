"""
Twitter/X API модуль через scraper.tech (RapidAPI).
Host: twitter-api45.p.rapidapi.com

Получает статистику твита по ссылке вида:
  https://x.com/user/status/1671370010743263233
  https://twitter.com/user/status/1671370010743263233
"""

import re
import asyncio
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import TIKTOK_RAPIDAPI_KEY  # тот же RapidAPI ключ

logger = logging.getLogger(__name__)

TWITTER_HOST = "twitter-api45.p.rapidapi.com"
TWITTER_BASE = f"https://{TWITTER_HOST}"
HEADERS = {
    "x-rapidapi-key": TIKTOK_RAPIDAPI_KEY,
    "x-rapidapi-host": TWITTER_HOST,
    "Content-Type": "application/json",
}


@dataclass
class ChannelAverage:
    avg_views: Optional[float] = None
    avg_likes: Optional[float] = None
    avg_retweets: Optional[float] = None
    avg_replies: Optional[float] = None
    posts_analyzed: int = 0


@dataclass
class TwitterPostStats:
    post_url: str
    tweet_id: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    retweets: Optional[int] = None
    replies: Optional[int] = None
    bookmarks: Optional[int] = None
    channel_title: Optional[str] = None
    channel_username: Optional[str] = None
    published_at: Optional[str] = None
    channel_avg: Optional[ChannelAverage] = None
    error: Optional[str] = None


def _extract_tweet_id(url: str) -> Optional[str]:
    """Извлекает tweet_id из ссылки x.com/user/status/123456."""
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


async def _get_channel_average(
    session: aiohttp.ClientSession,
    username: str,
    exclude_tweet_id: str,
    count: int = 20,
) -> ChannelAverage:
    """Берёт последние N твитов канала и считает средние."""
    try:
        params = {"screenname": username, "count": count}
        async with session.get(
            f"{TWITTER_BASE}/timeline.php",
            headers=HEADERS,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    f"Twitter timeline HTTP {resp.status} for @{username}"
                )
                return ChannelAverage()
            try:
                data = await resp.json(content_type=None)
            except Exception:
                return ChannelAverage()

        if not isinstance(data, dict):
            return ChannelAverage()
        tweets = data.get("timeline", [])
        if not isinstance(tweets, list):
            return ChannelAverage()
        tweets = [t for t in tweets if str(t.get("tweet_id", "")) != exclude_tweet_id]
        if not tweets:
            return ChannelAverage()

        def safe_int(val) -> int:
            try:
                return int(val or 0)
            except (TypeError, ValueError):
                return 0

        views_list    = [safe_int(t.get("views")) for t in tweets]
        likes_list    = [safe_int(t.get("favorites")) for t in tweets]
        retweets_list = [safe_int(t.get("retweets")) for t in tweets]
        replies_list  = [safe_int(t.get("replies")) for t in tweets]

        n = len(tweets)
        return ChannelAverage(
            avg_views=round(sum(views_list) / n),
            avg_likes=round(sum(likes_list) / n),
            avg_retweets=round(sum(retweets_list) / n),
            avg_replies=round(sum(replies_list) / n),
            posts_analyzed=n,
        )
    except Exception as e:
        logger.warning(f"Twitter channel average error for {username}: {e}")
        return ChannelAverage()


async def get_post_stats(post_url: str) -> TwitterPostStats:
    tweet_id = _extract_tweet_id(post_url)
    if not tweet_id:
        return TwitterPostStats(post_url=post_url, error="Не удалось извлечь tweet_id из ссылки")

    if not TIKTOK_RAPIDAPI_KEY:
        return TwitterPostStats(post_url=post_url, error="TIKTOK_RAPIDAPI_KEY не задан")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TWITTER_BASE}/tweet.php",
                headers=HEADERS,
                params={"id": tweet_id},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                # Защита от HTTP-ошибок и не-JSON ответов (например HTTP 451, HTML со страницей входа и т.п.)
                if resp.status != 200:
                    body_preview = ""
                    try:
                        body_preview = (await resp.text())[:150]
                    except Exception:
                        pass
                    logger.warning(
                        f"Twitter API HTTP {resp.status} for tweet_id={tweet_id}: {body_preview}"
                    )
                    return TwitterPostStats(
                        post_url=post_url,
                        tweet_id=tweet_id,
                        error=f"Twitter API HTTP {resp.status}",
                    )

                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    body_preview = ""
                    try:
                        body_preview = (await resp.text())[:150]
                    except Exception:
                        pass
                    logger.warning(
                        f"Twitter API non-JSON response for tweet_id={tweet_id}: {body_preview}"
                    )
                    return TwitterPostStats(
                        post_url=post_url,
                        tweet_id=tweet_id,
                        error=f"Twitter API вернул не-JSON: {type(e).__name__}",
                    )

            if not isinstance(data, dict):
                return TwitterPostStats(
                    post_url=post_url,
                    tweet_id=tweet_id,
                    error="Twitter API вернул неожиданный формат",
                )

            if data.get("status") == "error" or not data.get("tweet_id"):
                return TwitterPostStats(
                    post_url=post_url,
                    tweet_id=tweet_id,
                    error=data.get("error", "Твит не найден"),
                )

            def safe_int(val) -> Optional[int]:
                try:
                    v = int(val or 0)
                    return v if v > 0 else None
                except (TypeError, ValueError):
                    return None

            views     = safe_int(data.get("views"))
            likes     = safe_int(data.get("favorites"))
            retweets  = safe_int(data.get("retweets"))
            replies   = safe_int(data.get("replies"))
            bookmarks = safe_int(data.get("bookmarks"))
            published_at = data.get("created_at") or data.get("createdAt")

            # Автор
            author = data.get("author") or {}
            if not isinstance(author, dict):
                author = {}
            channel_title    = author.get("name")
            channel_username = author.get("screen_name") or author.get("screenname")

            # Средние по каналу
            channel_avg = ChannelAverage()
            if channel_username:
                try:
                    channel_avg = await _get_channel_average(session, channel_username, tweet_id)
                except Exception as e:
                    logger.warning(f"Twitter channel_avg failed for @{channel_username}: {e}")

            logger.info(
                f"Twitter done: tweet_id={tweet_id}, views={views}, "
                f"likes={likes}, retweets={retweets}, channel={channel_title}"
            )

        return TwitterPostStats(
            post_url=post_url,
            tweet_id=tweet_id,
            views=views,
            likes=likes,
            retweets=retweets,
            replies=replies,
            bookmarks=bookmarks,
            channel_title=channel_title,
            channel_username=channel_username,
            published_at=str(published_at) if published_at else None,
            channel_avg=channel_avg,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Twitter API timeout for tweet_id={tweet_id}")
        return TwitterPostStats(
            post_url=post_url,
            tweet_id=tweet_id,
            error="Twitter API timeout",
        )
    except aiohttp.ClientError as e:
        logger.warning(f"Twitter API network error for tweet_id={tweet_id}: {e}")
        return TwitterPostStats(
            post_url=post_url,
            tweet_id=tweet_id,
            error=f"Twitter API network error: {e}",
        )
    except Exception as e:
        logger.error(f"Twitter unexpected error for tweet_id={tweet_id}: {e}", exc_info=True)
        return TwitterPostStats(
            post_url=post_url,
            tweet_id=tweet_id,
            error=f"Twitter API unexpected: {type(e).__name__}",
        )
