"""
Twitter/X через twitter241 (RapidAPI).
Host: twitter241.p.rapidapi.com

Альтернатива twitter-api45. Пытается заодно вытащить тексты replies.

Формат ответа реального API этого провайдера может отличаться — код
специально написан «толерантно»: не падает на неожиданной структуре,
просто возвращает то, что смог распарсить.

Основные endpoint'ы (типичные для этого класса API):
- /tweet-details?tweetId={id} — детали твита
- /user-tweets?userId={id}&count=N — твиты пользователя
- /comments?tweetId={id}&count=N — реплаи
- /search-tweets?query=...

Если реальные endpoint'ы отличаются, увидим это в логах вида
"twitter241 HTTP 404 for /tweet-details" и подкрутим по документации.
"""

import re
import asyncio
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import TIKTOK_RAPIDAPI_KEY

logger = logging.getLogger(__name__)

HOST = "twitter241.p.rapidapi.com"
BASE = f"https://{HOST}"
HEADERS = {
    "x-rapidapi-key": TIKTOK_RAPIDAPI_KEY,
    "x-rapidapi-host": HOST,
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
    top_comments: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _extract_tweet_id(url: str) -> Optional[str]:
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


def _safe_int(val) -> Optional[int]:
    """Аккуратно парсим число из твиттеровского payload."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        v = int(val)
        return v if v > 0 else None
    if isinstance(val, str):
        # У некоторых API числа приходят как "1,234" или "1.2K"
        s = val.strip().replace(",", "").replace(" ", "")
        try:
            return int(float(s)) or None
        except (TypeError, ValueError):
            # Обработка "1.2K" / "5M"
            m = re.match(r"^([\d.]+)([KkMmBb])$", s)
            if m:
                num = float(m.group(1))
                mul = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
                return int(num * mul) or None
            return None
    return None


def _dig(data: dict, *keys, default=None):
    """Извлекает значение по любому из ключей — на случай если API даёт разные имена."""
    for k in keys:
        if isinstance(data, dict) and k in data and data[k] is not None:
            return data[k]
    return default


async def _safe_get_json(session: aiohttp.ClientSession, url: str,
                        params: dict, label: str) -> Optional[dict]:
    """Безопасно делает GET и возвращает JSON. Логирует и возвращает None при ошибках."""
    try:
        async with session.get(
            url, headers=HEADERS, params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                body_preview = ""
                try:
                    body_preview = (await resp.text())[:200]
                except Exception:
                    pass
                logger.warning(f"twitter241 {label} HTTP {resp.status}: {body_preview}")
                return None
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                logger.warning(f"twitter241 {label} non-JSON: {type(e).__name__}")
                return None
    except asyncio.TimeoutError:
        logger.warning(f"twitter241 {label} timeout")
        return None
    except aiohttp.ClientError as e:
        logger.warning(f"twitter241 {label} network error: {e}")
        return None
    except Exception as e:
        logger.error(f"twitter241 {label} unexpected: {e}", exc_info=True)
        return None


def _parse_tweet_payload(payload: dict) -> dict:
    """
    Извлекает нужные метрики из разных возможных структур ответа.
    Мы не знаем точно как выглядит payload, поэтому проверяем разные пути.
    """
    if not isinstance(payload, dict):
        return {}

    # Некоторые API оборачивают tweet в поле "data" / "tweet" / "result"
    root = payload
    for wrap_key in ("data", "tweet", "result", "tweetResult"):
        if wrap_key in root and isinstance(root[wrap_key], dict):
            root = root[wrap_key]
            break

    # Легаси-структура X GraphQL: legacy
    legacy = root.get("legacy") if isinstance(root, dict) else None
    if isinstance(legacy, dict):
        views_raw = _dig(root, "views", "view_count_info", default={})
        views = _safe_int(
            _dig(views_raw, "count", "state") if isinstance(views_raw, dict) else views_raw
        )
        return {
            "tweet_id": _dig(legacy, "id_str", "id"),
            "text": _dig(legacy, "full_text", "text"),
            "likes": _safe_int(_dig(legacy, "favorite_count", "like_count")),
            "retweets": _safe_int(_dig(legacy, "retweet_count")),
            "replies": _safe_int(_dig(legacy, "reply_count")),
            "bookmarks": _safe_int(_dig(legacy, "bookmark_count")),
            "views": views,
            "published_at": _dig(legacy, "created_at", "createdAt"),
            "author": root.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {}),
        }

    # Плоская структура (как в twitter-api45)
    author = root.get("author") if isinstance(root.get("author"), dict) else {}
    return {
        "tweet_id": _dig(root, "tweet_id", "id_str", "id"),
        "text": _dig(root, "text", "full_text", "display_text"),
        "likes": _safe_int(_dig(root, "favorites", "favorite_count", "likes", "like_count")),
        "retweets": _safe_int(_dig(root, "retweets", "retweet_count")),
        "replies": _safe_int(_dig(root, "replies", "reply_count")),
        "bookmarks": _safe_int(_dig(root, "bookmarks", "bookmark_count")),
        "views": _safe_int(_dig(root, "views", "view_count", "impressions")),
        "published_at": _dig(root, "created_at", "createdAt", "date"),
        "author": {
            "name": _dig(author, "name", "full_name"),
            "screen_name": _dig(author, "screen_name", "screenname", "username"),
        },
    }


def _extract_reply_texts(payload: dict, limit: int = 10) -> list[str]:
    """Пытается извлечь тексты реплаев из ответа /comments или /replies."""
    if not isinstance(payload, dict):
        return []

    # Возможные места где лежат реплаи
    candidates = []
    for key in ("comments", "replies", "timeline", "tweets", "data", "results"):
        v = payload.get(key)
        if isinstance(v, list):
            candidates.append(v)
        elif isinstance(v, dict):
            # data.replies или timeline.instructions
            for k2 in ("replies", "comments", "timeline", "tweets", "instructions"):
                v2 = v.get(k2)
                if isinstance(v2, list):
                    candidates.append(v2)

    if not candidates:
        return []

    items = candidates[0]  # берём первый непустой список
    texts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Пробуем разные пути к тексту
        text = (
            _dig(item, "text", "full_text", "display_text")
            or _dig(item.get("legacy", {}), "full_text", "text")
            or _dig(item.get("tweet", {}), "text", "full_text")
        )
        if isinstance(text, str) and len(text.strip()) > 3:
            texts.append(text.strip())
        if len(texts) >= limit:
            break
    return texts


async def get_post_stats(post_url: str) -> TwitterPostStats:
    tweet_id = _extract_tweet_id(post_url)
    if not tweet_id:
        return TwitterPostStats(
            post_url=post_url,
            error="Не удалось извлечь tweet_id из ссылки",
        )

    if not TIKTOK_RAPIDAPI_KEY:
        return TwitterPostStats(
            post_url=post_url,
            error="TIKTOK_RAPIDAPI_KEY не задан",
        )

    async with aiohttp.ClientSession() as session:
        # 1. Основные метрики
        # У этого класса API типичный endpoint /tweet-details
        payload = await _safe_get_json(
            session,
            f"{BASE}/tweet-details",
            {"tweetId": tweet_id},
            label="tweet-details",
        )
        if payload is None:
            return TwitterPostStats(
                post_url=post_url,
                tweet_id=tweet_id,
                error="twitter241 tweet-details не ответил",
            )

        parsed = _parse_tweet_payload(payload)
        author = parsed.get("author") or {}
        channel_title = author.get("name")
        channel_username = author.get("screen_name")

        # 2. Средние по каналу
        channel_avg = ChannelAverage()
        if channel_username:
            channel_avg = await _get_channel_average(session, channel_username, tweet_id)

        # 3. Реплаи (тексты комментариев)
        top_comments: list[str] = []
        replies_count = parsed.get("replies") or 0
        if replies_count and replies_count >= 5:
            comments_payload = await _safe_get_json(
                session,
                f"{BASE}/comments",
                {"tweetId": tweet_id, "count": 10},
                label="comments",
            )
            if comments_payload is not None:
                top_comments = _extract_reply_texts(comments_payload, limit=5)

        logger.info(
            f"twitter241 done: tweet_id={tweet_id}, views={parsed.get('views')}, "
            f"likes={parsed.get('likes')}, retweets={parsed.get('retweets')}, "
            f"replies={parsed.get('replies')}, top_comments={len(top_comments)}, "
            f"channel={channel_title}"
        )

        return TwitterPostStats(
            post_url=post_url,
            tweet_id=tweet_id,
            views=parsed.get("views"),
            likes=parsed.get("likes"),
            retweets=parsed.get("retweets"),
            replies=parsed.get("replies"),
            bookmarks=parsed.get("bookmarks"),
            channel_title=channel_title,
            channel_username=channel_username,
            published_at=(str(parsed.get("published_at")) if parsed.get("published_at") else None),
            channel_avg=channel_avg,
            top_comments=top_comments,
        )


async def _get_channel_average(
    session: aiohttp.ClientSession,
    username: str,
    exclude_tweet_id: str,
    count: int = 20,
) -> ChannelAverage:
    """Средние по последним твитам пользователя."""
    payload = await _safe_get_json(
        session,
        f"{BASE}/user-tweets",
        {"username": username, "count": count},
        label="user-tweets",
    )
    if payload is None:
        return ChannelAverage()

    # Извлекаем список твитов
    tweets = None
    for key in ("tweets", "data", "results", "timeline"):
        v = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(v, list):
            tweets = v
            break
        if isinstance(v, dict):
            for k2 in ("tweets", "timeline"):
                v2 = v.get(k2)
                if isinstance(v2, list):
                    tweets = v2
                    break
            if tweets is not None:
                break

    if not tweets:
        return ChannelAverage()

    # Собираем метрики
    metrics = {"views": [], "likes": [], "retweets": [], "replies": []}
    for item in tweets:
        if not isinstance(item, dict):
            continue
        parsed = _parse_tweet_payload(item)
        tid = parsed.get("tweet_id")
        if tid and str(tid) == str(exclude_tweet_id):
            continue
        for key in metrics:
            v = parsed.get(key)
            if v:
                metrics[key].append(v)

    n = max(len(v) for v in metrics.values()) if any(metrics.values()) else 0
    if n == 0:
        return ChannelAverage()

    def avg(lst):
        return round(sum(lst) / len(lst)) if lst else None

    return ChannelAverage(
        avg_views=avg(metrics["views"]),
        avg_likes=avg(metrics["likes"]),
        avg_retweets=avg(metrics["retweets"]),
        avg_replies=avg(metrics["replies"]),
        posts_analyzed=n,
    )
