"""
Оркестратор — собирает данные по всем постам из медиаплана
параллельно через API платформ, затем передаёт в OpenAI.
"""

import asyncio
import logging
from src.parsers.mediaplan import MediaPlan, Post
import os
from src.platforms import vk, telemetr, tgstat, hikerapi, pyrogram_tg, youtube, tiktok, twitter, telegram_comments
from src.analyzer.openai_analyzer import analyze_campaign

# Pyrogram доступен только если есть локальный файл сессии.
# На Railway используем telegram92 API — SESSION_STRING там не надёжен.
SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "session", "userbot.session")
PYROGRAM_AVAILABLE = os.path.exists(SESSION_FILE)

logger = logging.getLogger(__name__)


async def _fetch_stats_for_post(post: Post) -> dict:
    """Идёт за статистикой на нужную платформу."""
    stats = {}

    try:
        if post.platform == "vk" and post.post_url:
            logger.info(f"VK: fetching {post.post_url}")
            result = await vk.get_post_stats(post.post_url)
            # Подставляем название группы из API если name — это ссылка
            if result.channel_title and post.name.startswith("http"):
                post.name = result.channel_title
            stats = {
                "views": result.views,
                "likes": result.likes,
                "reposts": result.reposts,
                "comments": result.comments,
                "top_comments": result.top_comments,
                "error": result.error,
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_likes": result.channel_avg.avg_likes,
                    "avg_reposts": result.channel_avg.avg_reposts,
                    "avg_comments": result.channel_avg.avg_comments,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"VK done: views={result.views}, channel_title={result.channel_title}, avg_views={result.channel_avg.avg_views if result.channel_avg else None}, error={result.error}")

        elif post.platform == "telegram" and post.post_url:
            logger.info(f"Telemetr: fetching {post.post_url}")
            result = await telemetr.get_post_stats(post.post_url)
            if result.channel_title and post.name.startswith("http"):
                post.name = result.channel_title
            stats = {
                "views": result.views,
                "forwards": result.forwards,
                "reactions_count": result.reactions,
                "comments": result.comments,
                "channel_subscribers": result.channel_subscribers,
                "error": result.error,
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_forwards": result.channel_avg.avg_forwards,
                    "avg_reactions": result.channel_avg.avg_reactions,
                    "avg_comments": result.channel_avg.avg_comments,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"Telemetr done: views={result.views}, channel_title={result.channel_title}, avg_views={result.channel_avg.avg_views if result.channel_avg else None}, error={result.error}")

            # TGStat перекрёстная проверка: запускаем параллельно с Telemetr и берём большее значение.
            # Это защищает от случаев когда Telemetr занижает реальный охват.
            logger.info(f"TGStat cross-check: fetching {post.post_url}")
            fallback = await tgstat.get_post_stats(post.post_url)
            telemetr_views = result.views or 0
            tgstat_views = fallback.views or 0

            if not fallback.error and tgstat_views > telemetr_views:
                logger.info(f"TGStat wins: tgstat={tgstat_views} > telemetr={telemetr_views}")
                # Название: TGStat приоритетнее, но если у него нет — оставляем от Telemetr
                if fallback.channel_title:
                    post.name = fallback.channel_title
                # views берём от TGStat, остальное — лучшее из двух
                stats["views"] = tgstat_views
                stats["forwards"] = fallback.forwards or stats.get("forwards")
                stats["reactions_count"] = fallback.reactions_count or stats.get("reactions_count")
                stats["comments"] = fallback.comments or stats.get("comments")
                stats["channel_subscribers"] = fallback.channel_subscribers or stats.get("channel_subscribers")
                stats["error"] = None
                stats["tgstat_fallback"] = True
            elif result.error and not fallback.error and tgstat_views > 0:
                logger.info(f"TGStat used (telemetr error): tgstat={tgstat_views}")
                if fallback.channel_title:
                    post.name = fallback.channel_title
                stats["views"] = tgstat_views
                stats["forwards"] = fallback.forwards
                stats["reactions_count"] = fallback.reactions_count
                stats["comments"] = fallback.comments
                stats["channel_subscribers"] = fallback.channel_subscribers
                stats["error"] = None
                stats["tgstat_fallback"] = True
            else:
                logger.info(f"Telemetr wins or TGStat failed: telemetr={telemetr_views}, tgstat={tgstat_views}, tgstat_err={fallback.error}")

            # Комментарии собираем позже отдельным проходом (после gather),
            # чтобы не превышать rate limit telegram92 при параллельных запросах.
            # Флаг для второго прохода:
            if (stats.get("comments") or 0) >= 5:
                stats["_needs_comments"] = True

        elif post.platform == "instagram" and post.post_url:
            logger.info(f"HikerAPI: fetching {post.post_url}")
            result = await hikerapi.get_post_stats(post.post_url)
            stats = {
                "views": result.views,
                "likes": result.likes,
                "comments": result.comments,
                "reposts": result.reposts,
                "saves": result.saves,
                "post_type": result.post_type,
                "error": result.error,
                "_instagram_media_id": result.media_id,  # сохраняем для второго прохода
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_likes": result.channel_avg.avg_likes,
                    "avg_comments": result.channel_avg.avg_comments,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"HikerAPI done: views={result.views}, likes={result.likes}, comments={result.comments}, media_id={result.media_id}, error={result.error}")
            
            # Instagram комментарии — помечаем для второго прохода если их достаточно
            if (stats.get("comments") or 0) >= 5 and result.media_id:
                stats["_needs_comments"] = True
                logger.info(f"[COMMENTS DEBUG] Instagram post marked for comments collection: {result.comments} comments")

        elif post.platform == "youtube" and post.post_url:
            logger.info(f"YouTube: fetching {post.post_url}")
            result = await youtube.get_post_stats(post.post_url)
            if result.channel_title and post.name.startswith("http"):
                post.name = result.channel_title
            stats = {
                "views": result.views,
                "likes": result.likes,
                "comments": result.comments,
                "error": result.error,
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_likes": result.channel_avg.avg_likes,
                    "avg_comments": result.channel_avg.avg_comments,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"YouTube done: views={result.views}, channel={result.channel_title}, error={result.error}")

        elif post.platform == "tiktok" and post.post_url:
            logger.info(f"TikTok: fetching {post.post_url}")
            result = await tiktok.get_post_stats(post.post_url, fetch_comments=PYROGRAM_AVAILABLE)
            if result.channel_title and post.name.startswith("http"):
                post.name = result.channel_title
            stats = {
                "views": result.views,
                "likes": result.likes,
                "comments": result.comments,
                "reposts": result.shares,
                "top_comments": result.top_comments,
                "error": result.error,
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_likes": result.channel_avg.avg_likes,
                    "avg_comments": result.channel_avg.avg_comments,
                    "avg_reposts": result.channel_avg.avg_shares,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"TikTok done: views={result.views}, channel={result.channel_title}, error={result.error}")

        elif post.platform == "twitter" and post.post_url:
            logger.info(f"Twitter: fetching {post.post_url}")
            result = await twitter.get_post_stats(post.post_url)
            if result.channel_title and post.name.startswith("http"):
                post.name = result.channel_title
            stats = {
                "views": result.views,
                "likes": result.likes,
                "reposts": result.retweets,
                "comments": result.replies,
                "error": result.error,
            }
            if result.channel_avg and result.channel_avg.posts_analyzed > 0:
                stats["channel_avg"] = {
                    "avg_views": result.channel_avg.avg_views,
                    "avg_likes": result.channel_avg.avg_likes,
                    "avg_reposts": result.channel_avg.avg_retweets,
                    "avg_comments": result.channel_avg.avg_replies,
                    "posts_analyzed": result.channel_avg.posts_analyzed,
                }
            logger.info(f"Twitter done: views={result.views}, likes={result.likes}, error={result.error}")

        else:
            stats = {"error": f"Платформа {post.platform!r} не поддерживается или нет ссылки на пост"}

    except Exception as e:
        logger.error(f"Error fetching {post.platform} / {post.post_url}: {e}", exc_info=True)
        stats = {"error": str(e)}

    return {
        "name": post.name,
        "platform": post.platform,
        "is_organic": post.is_organic,
        "post_url": post.post_url,
        "planned_reach": post.planned_reach,
        "actual_cpv": post.actual_cpv,
        "planned_cpv": post.planned_cpv,
        "stats": stats,
    }


async def process_mediaplan(mp: MediaPlan, project_name: str = "") -> str:
    """
    Принимает объект MediaPlan, параллельно собирает статистику
    по всем постам и возвращает текст акцентов от OpenAI.
    """
    # Для органики берём только топ-20 по охвату — остальные раздувают промпт без пользы
    organic_sorted = sorted(
        [p for p in mp.organic_posts if p.actual_reach],
        key=lambda p: p.actual_reach or 0,
        reverse=True
    )[:20]

    all_posts = mp.paid_posts + organic_sorted

    # Разделяем посты по платформам
    # VK требует rate limiting (не более 3 запросов/сек) — обрабатываем последовательно с задержкой
    # Telegram и остальные — параллельно
    vk_posts = [(i, p) for i, p in enumerate(all_posts) if p.platform == "vk"]
    other_posts = [(i, p) for i, p in enumerate(all_posts) if p.platform != "vk"]

    posts_data = [None] * len(all_posts)

    # VK — последовательно с паузой 0.4 сек (до 3 req/s)
    for i, post in vk_posts:
        posts_data[i] = await _fetch_stats_for_post(post)
        await asyncio.sleep(0.4)

    # Остальные платформы — параллельно
    if other_posts:
        other_results = await asyncio.gather(*[_fetch_stats_for_post(p) for _, p in other_posts])
        for (i, _), result in zip(other_posts, other_results):
            posts_data[i] = result

    # Второй проход — комментарии.
    # telegram92 лимит: 1 req/min на ULTRA — берём только топ-1 пост по кол-ву комментариев.
    # Pyrogram (локально) — без ограничений, берём все посты.
    posts_needing_comments = [
        pd for pd in posts_data
        if pd and pd.get("stats", {}).get("_needs_comments")
    ]
    logger.info(f"[COMMENTS DEBUG] Posts needing comments: {len(posts_needing_comments)}")
    logger.info(f"[COMMENTS DEBUG] PYROGRAM_AVAILABLE: {PYROGRAM_AVAILABLE}")
    for pd in posts_needing_comments:
        pd["stats"].pop("_needs_comments")

    if posts_needing_comments:
        # Разделяем посты по платформам
        telegram_posts = [pd for pd in posts_needing_comments if pd.get("platform") == "telegram"]
        instagram_posts = [pd for pd in posts_needing_comments if pd.get("platform") == "instagram"]
        
        logger.info(f"[COMMENTS DEBUG] Telegram posts: {len(telegram_posts)}, Instagram posts: {len(instagram_posts)}")
        
        # Telegram комментарии
        if telegram_posts:
            if PYROGRAM_AVAILABLE:
                # Локально — Pyrogram без ограничений
                logger.info(f"[COMMENTS DEBUG] Using Pyrogram for {len(telegram_posts)} Telegram posts")
                for post_dict in telegram_posts:
                    url = post_dict.get("post_url", "")
                    logger.info(f"[COMMENTS DEBUG] Pyrogram fetching: {url}")
                    result = await pyrogram_tg.get_post_comments(url, limit=5)  # type: ignore
                    if result.top_comments:
                        post_dict["stats"]["top_comments"] = result.top_comments
                        logger.info(f"[COMMENTS DEBUG] Pyrogram success: {len(result.top_comments)} comments for {url}")
                    else:
                        logger.warning(f"[COMMENTS DEBUG] Pyrogram no comments: {url}, error: {result.error}")
            else:
                # Railway — telegram92, 1 req/min, пауза 61 сек между постами
                logger.info(f"[COMMENTS DEBUG] Using telegram92 for {len(telegram_posts)} Telegram posts")
                for idx, post_dict in enumerate(telegram_posts):
                    if idx > 0:
                        logger.info(f"[COMMENTS DEBUG] telegram92 rate limit pause 61s before next comments request")
                        await asyncio.sleep(61)
                    url = post_dict.get("post_url", "")
                    logger.info(f"[COMMENTS DEBUG] telegram92 fetching: {url}")
                    tg_result = await telegram_comments.get_post_comments(url, limit=5)
                    if tg_result.top_comments:
                        post_dict["stats"]["top_comments"] = tg_result.top_comments
                        logger.info(f"[COMMENTS DEBUG] telegram92 success: {len(tg_result.top_comments)} comments")
                    elif tg_result.error:
                        logger.warning(f"[COMMENTS DEBUG] telegram92 error: {tg_result.error}")
                    else:
                        logger.warning(f"[COMMENTS DEBUG] telegram92 no comments and no error for {url}")
        
        # Instagram комментарии
        if instagram_posts:
            from src.platforms import instagram_comments
            logger.info(f"[COMMENTS DEBUG] Fetching Instagram comments for {len(instagram_posts)} posts")
            for post_dict in instagram_posts:
                url = post_dict.get("post_url", "")
                media_id = post_dict.get("stats", {}).get("_instagram_media_id")
                if not media_id:
                    logger.warning(f"[COMMENTS DEBUG] Instagram post has no media_id: {url}")
                    continue
                logger.info(f"[COMMENTS DEBUG] Instagram fetching comments: {url} (media_id={media_id})")
                insta_result = await instagram_comments.get_post_comments(media_id, post_url=url, limit=5)
                if insta_result.top_comments:
                    post_dict["stats"]["top_comments"] = insta_result.top_comments
                    logger.info(f"[COMMENTS DEBUG] Instagram success: {len(insta_result.top_comments)} comments")
                elif insta_result.error:
                    logger.warning(f"[COMMENTS DEBUG] Instagram error: {insta_result.error}")
                else:
                    logger.warning(f"[COMMENTS DEBUG] Instagram no comments for {url}")

    # Считаем охват
    # Для экономии используем данные из МП (не из API) — они зафиксированы командой
    total_actual_from_mp = mp.total_actual_reach  # берём итоговый охват из строки МП
    total_organic_reach = sum(p.actual_reach or 0 for p in mp.organic_posts)

    # Для передачи в промпт — для органики всегда берём цифры из МП, не из API
    # Это предотвращает ситуацию когда GPT суммирует API-просмотры органики вместо МП-значений
    total_actual = 0
    for i, post_dict in enumerate(posts_data):
        post = all_posts[i]
        if post.is_organic:
            actual_views = post.actual_reach or 0
            # Подменяем API-просмотры на МП-значения для органики
            post_dict["stats"]["views"] = actual_views
        else:
            views = post_dict["stats"].get("views")
            actual_views = views if views else (post.actual_reach or 0)
        if actual_views:
            total_actual += actual_views

    # Экономия считается по данным МП, не API
    # Формула: (итоговый охват из МП - плановый охват) ÷ 2 руб.
    MARKET_CPV = 2.0
    mp_total = total_actual_from_mp if total_actual_from_mp else total_actual
    overreach = mp_total - mp.total_planned_reach
    total_savings = max(overreach / MARKET_CPV, 0)

    result = await analyze_campaign(
        project_name=project_name or "Без названия",
        posts_data=list(posts_data),
        total_planned_reach=mp.total_planned_reach,
        total_actual_reach=mp_total,       # итог из МП — зафиксированные цифры команды
        total_budget=mp.total_budget,
        total_savings=total_savings,
        total_organic_reach=total_organic_reach,
        total_placement_budget=mp.total_budget,
    )

    return result


async def process_links(
    paid_links: list[str],
    organic_links: list[str],
    organic_reach_manual: int | None,
    planned_reach: int,
    budget: float,
    project_name: str = "",
    plan_by_url: dict | None = None,
) -> tuple[str, int]:
    """
    Новый диалоговый режим — принимает ссылки напрямую без МП.
    Возвращает (текст акцентов, суммарный фактический охват).
    """
    from src.parsers.mediaplan import Post

    plan_by_url = plan_by_url or {}

    # Собираем paid-посты из ссылок
    paid_posts = []
    for url in paid_links:
        url = url.strip()
        platform = _detect_platform(url)
        # Ищем плановый охват в маппинге — пробуем точное совпадение и без слеша в конце
        post_plan = plan_by_url.get(url) or plan_by_url.get(url.rstrip("/")) or 0
        paid_posts.append(Post(
            name=url,
            channel_url=url,
            platform=platform,
            post_url=url,
            planned_reach=post_plan,
            is_organic=False,
        ))

    # Органика из ссылок
    organic_posts = []
    for url in organic_links:
        url = url.strip()
        platform = _detect_platform(url)
        organic_posts.append(Post(
            name=url,
            channel_url=url,
            platform=platform,
            post_url=url,
            planned_reach=0,
            actual_reach=None,
            is_organic=True,
        ))

    all_posts = paid_posts + organic_posts

    # VK — последовательно, остальные параллельно
    vk_posts = [(i, p) for i, p in enumerate(all_posts) if p.platform == "vk"]
    other_posts = [(i, p) for i, p in enumerate(all_posts) if p.platform != "vk"]

    posts_data = [None] * len(all_posts)

    for i, post in vk_posts:
        posts_data[i] = await _fetch_stats_for_post(post)
        await asyncio.sleep(0.4)

    if other_posts:
        other_results = await asyncio.gather(*[_fetch_stats_for_post(p) for _, p in other_posts])
        for (i, _), result in zip(other_posts, other_results):
            posts_data[i] = result

    # Второй проход — комментарии (та же логика: Pyrogram локально, telegram92 топ-1 на Railway)
    posts_needing_comments = [
        pd for pd in posts_data
        if pd and pd.get("stats", {}).get("_needs_comments")
    ]
    for pd in posts_needing_comments:
        pd["stats"].pop("_needs_comments")

    if posts_needing_comments:
        # Разделяем по платформам
        telegram_posts = [pd for pd in posts_needing_comments if pd.get("platform") == "telegram"]
        instagram_posts = [pd for pd in posts_needing_comments if pd.get("platform") == "instagram"]
        
        # Telegram
        if telegram_posts:
            if PYROGRAM_AVAILABLE:
                for post_dict in telegram_posts:
                    url = post_dict.get("post_url", "")
                    result = await pyrogram_tg.get_post_comments(url, limit=5)  # type: ignore
                    if result.top_comments:
                        post_dict["stats"]["top_comments"] = result.top_comments
            else:
                for idx, post_dict in enumerate(telegram_posts):
                    if idx > 0:
                        await asyncio.sleep(61)
                    url = post_dict.get("post_url", "")
                    tg_result = await telegram_comments.get_post_comments(url, limit=5)
                    if tg_result.top_comments:
                        post_dict["stats"]["top_comments"] = tg_result.top_comments
        
        # Instagram
        if instagram_posts:
            from src.platforms import instagram_comments
            for post_dict in instagram_posts:
                url = post_dict.get("post_url", "")
                media_id = post_dict.get("stats", {}).get("_instagram_media_id")
                if media_id:
                    insta_result = await instagram_comments.get_post_comments(media_id, post_url=url, limit=5)
                    if insta_result.top_comments:
                        post_dict["stats"]["top_comments"] = insta_result.top_comments

    # Суммарный охват из API + разбивка по постам для диагностики
    total_actual = 0
    breakdown = []
    for post_dict, post in zip(posts_data, all_posts):
        views = post_dict["stats"].get("views") or 0
        total_actual += views
        breakdown.append({
            "url": post.post_url,
            "is_organic": post.is_organic,
            "views": views,
            "error": post_dict["stats"].get("error"),
            "tgstat_fallback": post_dict["stats"].get("tgstat_fallback", False),
        })

    # Органика — если передана цифрой вручную, используем её
    total_organic_reach = organic_reach_manual or sum(
        p["stats"].get("views") or 0
        for p, post in zip(posts_data, all_posts)
        if post.is_organic
    )
    if organic_reach_manual:
        total_actual += organic_reach_manual

    # Экономия = (факт - план) ÷ 2
    MARKET_CPV = 2.0
    overreach = total_actual - planned_reach
    total_savings = max(overreach / MARKET_CPV, 0)

    result = await analyze_campaign(
        project_name=project_name or "Без названия",
        posts_data=list(posts_data),
        total_planned_reach=planned_reach,
        total_actual_reach=total_actual,
        total_budget=budget,
        total_savings=total_savings,
        total_organic_reach=total_organic_reach,
        total_placement_budget=budget,  # в диалоговом режиме пользователь вводит бюджет размещений
    )

    return result, total_actual, breakdown


def _detect_platform(url: str) -> str:
    url = url.lower()
    if "vk.com" in url or "vk.ru" in url:
        return "vk"
    if "t.me" in url or "telegram" in url:
        return "telegram"
    if "instagram.com" in url:
        return "instagram"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "tiktok.com" in url or "vt.tiktok.com" in url:
        return "tiktok"
    if "x.com" in url or "twitter.com" in url:
        return "twitter"
    return "unknown"
