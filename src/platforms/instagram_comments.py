"""
Instagram комментарии через HikerAPI.
Документация: https://hikerapi.com/docs

Получает топ комментариев к посту/рилсу по media_id или shortcode.
"""

import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from src.config import HIKERAPI_TOKEN

logger = logging.getLogger(__name__)

HIKERAPI_BASE = "https://api.hikerapi.com/v1"


@dataclass
class InstagramCommentsResult:
    post_url: str
    top_comments: list[str] = field(default_factory=list)
    total_count: Optional[int] = None
    error: Optional[str] = None


async def get_post_comments(media_id: str, post_url: str = "", limit: int = 10) -> InstagramCommentsResult:
    """
    Получает топ комментариев к Instagram-посту.
    
    Args:
        media_id: ID медиа из Instagram API
        post_url: Ссылка на пост (для логирования)
        limit: Количество комментариев для получения
    """
    logger.info(f"[COMMENTS DEBUG] instagram_comments.get_post_comments called for media_id={media_id}")
    
    if not HIKERAPI_TOKEN:
        logger.error(f"[COMMENTS DEBUG] HIKERAPI_TOKEN not set")
        return InstagramCommentsResult(post_url=post_url, error="HIKERAPI_TOKEN не задан")
    
    headers = {"x-access-key": HIKERAPI_TOKEN}
    
    try:
        async with aiohttp.ClientSession() as session:
            # Пробуем endpoint /media/comments
            params = {
                "id": media_id,  # HikerAPI требует параметр 'id', а не 'media_id'
                "count": limit,
            }
            async with session.get(
                f"{HIKERAPI_BASE}/media/comments",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                logger.info(f"[COMMENTS DEBUG] HikerAPI comments response status: {resp.status}")
                
                if resp.status == 404:
                    logger.warning(f"[COMMENTS DEBUG] HikerAPI comments endpoint not found - trying alternative")
                    # Возможно нужен другой endpoint
                    return InstagramCommentsResult(
                        post_url=post_url,
                        error="HikerAPI comments endpoint not available"
                    )
                elif resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[COMMENTS DEBUG] HikerAPI comments error: HTTP {resp.status}: {text[:200]}")
                    return InstagramCommentsResult(
                        post_url=post_url,
                        error=f"HTTP {resp.status}: {text[:100]}"
                    )
                
                data = await resp.json()
                logger.info(f"[COMMENTS DEBUG] HikerAPI response type: {type(data)}")
                
                # Обработка ответа
                if isinstance(data, dict):
                    comments_list = data.get("comments", [])
                    total = data.get("comment_count") or len(comments_list)
                elif isinstance(data, list):
                    comments_list = data
                    total = len(comments_list)
                else:
                    logger.error(f"[COMMENTS DEBUG] Unexpected response format")
                    return InstagramCommentsResult(post_url=post_url, error="Unexpected API response format")
                
                logger.info(f"[COMMENTS DEBUG] Got {len(comments_list)} comments from API")
                
                # Извлекаем тексты комментариев
                texts = []
                for comment in comments_list[:limit]:
                    if isinstance(comment, dict):
                        text = comment.get("text") or comment.get("comment_text") or ""
                    elif isinstance(comment, str):
                        text = comment
                    else:
                        continue
                    
                    text = text.strip()
                    if text and len(text) > 3:
                        texts.append(text)
                
                logger.info(f"[COMMENTS DEBUG] Extracted {len(texts)} text comments")
                
                return InstagramCommentsResult(
                    post_url=post_url,
                    top_comments=texts,
                    total_count=total,
                )
    
    except Exception as e:
        logger.error(f"[COMMENTS DEBUG] Instagram comments exception: {e}", exc_info=True)
        return InstagramCommentsResult(post_url=post_url, error=str(e))
