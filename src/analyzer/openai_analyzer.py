"""
OpenAI модуль — анализирует собранные данные и формулирует акценты для отчёта.
"""

import asyncio
from openai import AsyncOpenAI
from src.config import OPENAI_API_KEY, OPENAI_MODEL

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Ты — аналитик команды Digital PR агентства. Твоя задача — сформировать структурированный отчёт по рекламной кампании строго в трёх разделах.
Пиши коротко, хлёстко, с конкретными цифрами. Никаких вводных слов — сразу по делу.

ОБЩИЕ РЕЗУЛЬТАТЫ

Выведи ровно эти 4 пункта в указанном порядке, используя данные из поля "ОБЩИЕ ЦИФРЫ КАМПАНИИ":

• Вместо {план} просмотров у нас {факт} просмотров! Это в {факт/план} раз выше планируемого охвата!
• Итоговый CPV по проекту: {факт_cpv} ₽ при плановом {план_cpv} ₽ — в {план_cpv/факт_cpv} раза ниже
• Виральный охват по проекту составил {факт−план} просмотров (фактический минус плановый), что эквивалентно экономии {(факт−план)/2} рублей (при средней цене просмотра по рекламному рынку в 2 рубля)
• Суммарный органический охват — {органика} просмотров

Правила:
- Множители округляй до одного знака после запятой
- Цифры разделяй запятыми: 6,170,892
- НЕ добавляй другие пункты в этот раздел

СВЕРХРЕЗУЛЬТАТЫ

Отбор постов — СТРОГО по двум критериям (оба применяй независимо):

КРИТЕРИЙ А — аномальные просмотры:
  Пост попадает если факт_просмотров ≥ 2 × плановый_охват_поста
  Формулировка: "Пост [ссылка] — X просмотров при плане Y (в N раз выше плана)"

КРИТЕРИЙ Б — аномальные лайки / комментарии / репосты / реакции / форварды:
  Пост попадает если ЛЮБОЙ из показателей ≥ 1.5 × его норма_канала
  Проверяй каждый показатель отдельно: лайки vs норма лайков; комменты vs норма комментов; репосты vs норма репостов; реакции vs норма реакций; форварды vs норма пересылок
  Каждое превышение = отдельная строка в разделе
  Формулировка: "Пост [ссылка] — X лайков вместо средних Y (среднее по последним N постам канала)"
  Для комментариев: "Пост [ссылка] — X комментариев вместо средних Y (среднее по последним N постам канала)"

Правила отбора:
- Максимум 5 строк в разделе
- Если кандидатов больше 5 — выбирай те, где множитель превышения наибольший
- Если ни один пост не даёт ×2 по просмотрам И ни один не даёт ×1.5 по лайкам/комментам/репостам — тогда и только тогда берём посты с превышением ×1.1–1.49, помечая их "(умеренное превышение)"
- Если показатель НИЖЕ нормы — НЕ упоминай его здесь
- Используй ТОЛЬКО данные из поля "Норма канала" — не придумывай нормы

АНАЛИТИКА ПО ЛАЙКАМ, КОММЕНТАРИЯМ И РЕПОСТАМ

Дай обобщённую оценку по каждому показателю отдельно: лайки, комментарии, репосты/форварды, реакции.

Правила оценки (применяй к каждому посту отдельно, затем обобщай по всему посеву):
- БОЛЬШЕ: показатель поста ≥ 1.25 × норма_канала
- МЕНЬШЕ: показатель поста ≤ 0.6 × норма_канала
- СТОЛЬКО ЖЕ: показатель поста от 0.61 до 1.24 × норма_канала

Алгоритм обобщения:
1. По каждому показателю посчитай сколько постов дали "больше", "меньше", "столько же"
2. Если большинство постов "больше" — итог "больше", если большинство "меньше" — итог "меньше", иначе — "столько же"
3. Если посты расходятся — итог "по-разному" и объясни

Формат раздела — одна связная фраза по образцу:
"Если сравнивать посты посева с обычными публикациями на каналах, посты в среднем набирали [оценка] лайков [пример если отклонение], [оценка] репостов [пример], [оценка] комментариев [пример]"

Примеры:
- "больше лайков (сильнее всего в канале Рифмы и Панчи — 2,900 вместо ≈361 обычно)"
- "меньше репостов (сильнее всего в канале ВПШ — 116 вместо ≈236 обычно)"
- "обычное количество реакций"

Правила:
- Если показатель "больше" или "меньше" — обязательно давай пример с наибольшим отклонением
- Если показатель "столько же" — пример не нужен, пиши просто "обычное количество X"
- Используй НАЗВАНИЕ канала из поля "name" — никогда не используй ссылку вместо названия
- Если в поле "name" стоит ссылка (начинается с http) — напиши просто "одном из каналов"
- Используй ТОЛЬКО данные из поля "Норма канала" — не придумывай
- Для TG-каналов: "лайки" = реакции, "репосты" = форварды

ОБЩИЕ ЗАПРЕТЫ:
- Не добавляй вводных фраз типа "Отчёт по кампании..." или "Подводя итоги..."
- Не придумывай цифры которых нет в данных
- Не изменяй и не сокращай ссылки — копируй дословно из таблицы POST_ID
- Не перечисляй органические посты по одному
- Не выноси цитаты комментариев в разделы 1–3

Работай только с теми данными, которые явно переданы."""


def _format_post_data(post_data: dict, post_idx: int) -> str:
    """Форматирует данные одного поста для промпта."""
    lines = []
    name = post_data.get("name", "")
    platform = post_data.get("platform", "")
    is_organic = post_data.get("is_organic", False)

    tag = "[ОРГАНИКА]" if is_organic else "[PAID]"
    # Ссылку НЕ вставляем в текст — передаём только ID поста
    # Это предотвращает искажение ссылок моделью
    display_name = name if name and not name.startswith("http") else f"Пост #{post_idx + 1}"
    lines.append(f"{tag} {display_name} ({platform}) [POST_ID:{post_idx}]")

    planned = post_data.get("planned_reach")
    if planned:
        lines.append(f"  План: {planned:,} просмотров")

    stats = post_data.get("stats", {})
    if stats:
        if stats.get("views") is not None:
            lines.append(f"  Факт просмотров: {stats['views']:,}")
        if stats.get("likes") is not None:
            lines.append(f"  Лайки: {stats['likes']:,}")
        reposts = stats.get("reposts") or stats.get("forwards")
        if reposts is not None:
            lines.append(f"  Репосты/пересылки: {reposts:,}")
        if stats.get("comments") is not None:
            lines.append(f"  Комментарии: {stats['comments']:,}")
        if stats.get("reactions_count") is not None:
            lines.append(f"  Реакции: {stats['reactions_count']:,}")
        if stats.get("saves") is not None:
            lines.append(f"  Сохранения: {stats['saves']:,}")
        if stats.get("channel_subscribers") is not None:
            lines.append(f"  Подписчики канала: {stats['channel_subscribers']:,}")
        # Норма канала — реальные средние из API, строго по каждой метрике отдельно
        channel_avg = stats.get("channel_avg", {})
        if channel_avg:
            n = channel_avg.get("posts_analyzed", 0)
            label = f"(среднее по последним {n} постам канала)"
            if channel_avg.get("avg_views"):
                lines.append(f"  Норма канала — просмотры: {channel_avg['avg_views']:,} {label}")
            if channel_avg.get("avg_likes"):
                lines.append(f"  Норма канала — лайки: {channel_avg['avg_likes']:,} {label}")
            if channel_avg.get("avg_reposts"):
                lines.append(f"  Норма канала — репосты: {channel_avg['avg_reposts']:,} {label}")
            if channel_avg.get("avg_forwards"):
                lines.append(f"  Норма канала — пересылки: {channel_avg['avg_forwards']:,} {label}")
            if channel_avg.get("avg_comments"):
                lines.append(f"  Норма канала — комментарии: {channel_avg['avg_comments']:,} {label}")
            if channel_avg.get("avg_saves"):
                lines.append(f"  Норма канала — сохранения: {channel_avg['avg_saves']:,} {label}")

        # Комментарии — только если их достаточно для вывода
        comments_count = stats.get("comments", 0) or 0
        top_comments = stats.get("top_comments", [])
        if top_comments and comments_count >= 5:
            lines.append(f"  Топ комментарии из {comments_count} ({len(top_comments)} шт.):")
            for c in top_comments:
                short = c[:150] + "…" if len(c) > 150 else c
                lines.append(f"    — «{short}»")
        elif top_comments:
            # Есть тексты но мало комментов — передаём без акцента
            lines.append(f"  Комментарии ({comments_count} шт., недостаточно для акцента)")

    actual_cpv = post_data.get("actual_cpv")
    planned_cpv = post_data.get("planned_cpv")
    if actual_cpv is not None:
        lines.append(f"  CPV факт: {actual_cpv:.2f} ₽")
    if planned_cpv is not None:
        lines.append(f"  CPV план: {planned_cpv:.2f} ₽")

    if stats.get("error"):
        lines.append(f"  [Ошибка получения данных: {stats['error']}]")

    return "\n".join(lines)


async def analyze_campaign(
    project_name: str,
    posts_data: list[dict],
    total_planned_reach: int,
    total_actual_reach: int,
    total_budget: float,
    total_savings: float = 0.0,
    total_organic_reach: int = 0,
    total_placement_budget: float = 0.0,
) -> str:
    """
    Принимает агрегированные данные по кампании и возвращает текст с акцентами.

    posts_data — список словарей с полями:
      name, platform, is_organic, post_url,
      planned_reach, actual_cpv, planned_cpv,
      stats: {views, likes, reposts, comments, reactions_count, saves,
              forwards, channel_subscribers, error}
    """
    posts_text = "\n\n".join(_format_post_data(p, i) for i, p in enumerate(posts_data))

    # Маппинг ID → ссылка — передаём отдельно чтобы модель не искажала ссылки
    links_map = "\n".join(
        f"POST_ID:{i} = {p.get('post_url', '')}"
        for i, p in enumerate(posts_data)
        if p.get("post_url")
    )

    organic_str = ""
    if total_organic_reach > 0:
        organic_str = f"\n- Органический охват (бесплатный): {total_organic_reach:,}"
    savings_str = ""
    if total_savings > 0:
        overreach = total_actual_reach - total_planned_reach
        savings_str = f"\n- Расчётная экономия бюджета: {total_savings:,.0f} ₽ (сверхплановый охват {overreach:,} ÷ 2 руб. рыночный CPV)"

    # CPV считается только от бюджета размещений (без менеджмента и доп. расходов)
    placement_budget = total_placement_budget if total_placement_budget else total_budget
    actual_cpv = placement_budget / total_actual_reach if total_actual_reach > 0 else 0
    planned_cpv = placement_budget / total_planned_reach if total_planned_reach > 0 else 0
    cpv_ratio = planned_cpv / actual_cpv if actual_cpv > 0 else 0

    # --- Предварительный расчёт кандидатов для раздела 2 ---
    # Делаем это в Python, чтобы GPT не мог пропустить ни одного кандидата
    superresults = []

    for post in posts_data:
        if post.get("is_organic"):
            continue
        url = post.get("post_url", "")
        name = post.get("name", url)
        stats = post.get("stats", {})
        plan = post.get("planned_reach") or 0
        avg = stats.get("channel_avg") or {}
        n_posts = avg.get("posts_analyzed", 20)

        # Критерий А — просмотры ≥ 2× план
        views = stats.get("views")
        if views and plan and views >= 2 * plan:
            ratio = views / plan
            superresults.append((ratio, f"Пост {url} — {views:,} просмотров при плане {plan:,} (в {ratio:.1f} раз выше плана)"))

        # Критерий Б — лайки ≥ 1.5× норма
        likes = stats.get("likes")
        avg_likes = avg.get("avg_likes")
        if likes is not None and avg_likes and likes >= 1.5 * avg_likes:
            ratio = likes / avg_likes
            superresults.append((ratio, f"Пост {url} — {likes:,} лайков вместо средних {avg_likes:,} (среднее по последним {n_posts} постам канала)"))

        # Критерий Б — комментарии ≥ 1.5× норма
        comments = stats.get("comments")
        avg_comments = avg.get("avg_comments")
        if comments is not None and avg_comments and comments >= 1.5 * avg_comments:
            ratio = comments / avg_comments
            superresults.append((ratio, f"Пост {url} — {comments:,} комментариев вместо средних {avg_comments:,} (среднее по последним {n_posts} постам канала)"))

        # Критерий Б — репосты ≥ 1.5× норма
        reposts = stats.get("reposts") or stats.get("forwards")
        avg_reposts = avg.get("avg_reposts") or avg.get("avg_forwards")
        if reposts is not None and avg_reposts and reposts >= 1.5 * avg_reposts:
            ratio = reposts / avg_reposts
            superresults.append((ratio, f"Пост {url} — {reposts:,} репостов вместо средних {avg_reposts:,} (среднее по последним {n_posts} постам канала)"))

        # Критерий Б — реакции ≥ 1.5× норма
        reactions = stats.get("reactions_count")
        avg_reactions = avg.get("avg_reactions")
        if reactions is not None and avg_reactions and reactions >= 1.5 * avg_reactions:
            ratio = reactions / avg_reactions
            superresults.append((ratio, f"Пост {url} — {reactions:,} реакций вместо средних {avg_reactions:,} (среднее по последним {n_posts} постам канала)"))

    # Если ничего нет — fallback: берём с ×1.1
    if not superresults:
        for post in posts_data:
            if post.get("is_organic"):
                continue
            url = post.get("post_url", "")
            stats = post.get("stats", {})
            plan = post.get("planned_reach") or 0
            avg = stats.get("channel_avg") or {}
            n_posts = avg.get("posts_analyzed", 20)
            views = stats.get("views")
            if views and plan and views >= 1.1 * plan:
                ratio = views / plan
                superresults.append((ratio, f"Пост {url} — {views:,} просмотров при плане {plan:,} (в {ratio:.1f} раз выше плана) (умеренное превышение)"))

    # Топ-5 по множителю
    superresults.sort(key=lambda x: x[0], reverse=True)
    superresults_text = "\n".join(line for _, line in superresults[:5])

    user_message = f"""Проект: {project_name}

ОБЩИЕ ЦИФРЫ КАМПАНИИ:
- Плановый охват: {total_planned_reach:,}
- Фактический охват (все публикации): {total_actual_reach:,}
- Множитель перевыполнения: в {total_actual_reach / total_planned_reach:.1f} раза
- Бюджет размещений: {placement_budget:,.0f} ₽
- Факт CPV: {actual_cpv:.2f} ₽
- Плановый CPV: {planned_cpv:.2f} ₽
- CPV ниже плана в: {cpv_ratio:.1f} раза
- Виральный охват (факт − план): {total_actual_reach - total_planned_reach:,} просмотров
- Расчётная экономия: {total_savings:,.0f} ₽
- Органический охват: {total_organic_reach:,} просмотров

ПУБЛИКАЦИИ:
{posts_text}

ССЫЛКИ НА ПОСТЫ (копируй дословно, не изменяй ни символ):
{links_map}

ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 2 (вставь их дословно, не перефразируй, не сокращай список):
{superresults_text}

Сформируй отчёт строго в трёх разделах как указано в инструкции.

ОФОРМЛЕНИЕ — строго такое, без линий и символов-разделителей:

ОБЩИЕ РЕЗУЛЬТАТЫ

• пункт 1
• пункт 2
• пункт 3
• пункт 4

СВЕРХРЕЗУЛЬТАТЫ

строка 1
строка 2
...

АНАЛИТИКА ПО ЛАЙКАМ, КОММЕНТАРИЯМ И РЕПОСТАМ

текст

Раздел 2: используй строки из блока "ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 2" дословно."""

    # Запускаем акценты и анализ комментариев параллельно
    accent_coro = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
        timeout=120,
    )

    # Собираем посты с комментариями для отдельного блока
    posts_with_comments = [
        p for p in posts_data
        if p.get("stats", {}).get("top_comments")
    ]

    comments_coro = None
    if posts_with_comments:
        comments_coro = _analyze_comments(posts_with_comments)

    if comments_coro:
        accent_resp, comments_text = await asyncio.gather(accent_coro, comments_coro)
    else:
        accent_resp = await accent_coro
        comments_text = None

    result = accent_resp.choices[0].message.content

    if comments_text:
        result += f"\n\n---\n\n{comments_text}"

    return result


async def _analyze_comments(posts_with_comments: list[dict]) -> str:
    """Отдельный блок — о чём писали люди в комментариях."""

    blocks = []
    # Сохраняем ссылки отдельно — передадим их в итоговый текст напрямую
    post_urls = []

    for p in posts_with_comments:
        post_url = p.get("post_url", "")
        platform = p.get("platform", "")
        comments_count = p.get("stats", {}).get("comments", 0) or 0
        top_comments = p.get("stats", {}).get("top_comments", [])
        if not top_comments:
            continue
        comments_text = "\n".join(f'  — «{c[:200]}»' for c in top_comments)
        post_urls.append(post_url)
        blocks.append(
            f"ПОСТ {len(post_urls)} ({platform}), {comments_count} комментариев:\n{comments_text}"
        )

    if not blocks:
        return ""

    user_message = (
        "Ниже — топ комментарии под постами рекламной кампании. "
        "Напиши короткий аналитический срез: о чём конкретно писали люди под каждым постом.\n\n"
        "ПРАВИЛА:\n"
        "- Пиши о конкретных темах обсуждения: что упоминали, о чём спорили, что цитировали\n"
        "- Если упоминается сериал, фильм, бренд, персонаж — назови их по имени\n"
        "- Если обсуждают конкретную сцену, цитату, момент — опиши что именно\n"
        "- Если есть юмор или мемы — скажи какой именно (например: 'шутят про X', 'мем про Y')\n"
        "- Тональность: нейтральная. Вместо 'критика' пиши 'не все поняли посыл'; вместо 'споры' пиши 'мнения разделились'\n"
        "- ЗАПРЕЩЕНО: расплывчатые фразы без конкретики — 'отсылки к личностным характеристикам', 'шутки и отсылки', 'позитивные сигналы'\n"
        "- Структура: 'ПОСТ N:' → 2-3 предложения с конкретикой. Используй именно 'ПОСТ N:' как заголовок\n\n"
        + "\n\n".join(blocks)
    )

    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "Ты аналитик Digital PR агентства. Пиши коротко, по делу, нейтрально на русском языке."},
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
        timeout=60,
    )

    # Подставляем ссылки вместо "ПОСТ N" — это гарантирует что ссылки не искажаются
    analysis = response.choices[0].message.content
    for i, url in enumerate(post_urls, 1):
        analysis = analysis.replace(f"ПОСТ {i}:", f"ПОСТ {i} ({url}):")

    return "О чём писали в комментариях:\n\n" + analysis
