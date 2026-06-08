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

ЕСЛИ ПЛАН ВЫПОЛНЕН (факт ≥ план):
• Вместо {план} просмотров у нас {факт} просмотров! Это в {факт/план} раз выше планируемого охвата!
• Итоговый CPV по проекту: {факт_cpv} ₽ при плановом {план_cpv} ₽ — в {план_cpv/факт_cpv} раза ниже
• Виральный охват по проекту составил {факт−план} просмотров (фактический минус плановый), что эквивалентно экономии {(факт−план)/2} рублей (при средней цене просмотра по рекламному рынку в 2 рубля)
• Суммарный органический охват — {органика} просмотров

ЕСЛИ ПЛАН НЕ ВЫПОЛНЕН (факт < план):
• Фактический охват составил {факт} просмотров при плане {план} — выполнение плана на {факт/план×100}%
• Итоговый CPV по проекту: {факт_cpv} ₽ при плановом {план_cpv} ₽ — в {факт_cpv/план_cpv} раза выше плана
• Суммарный органический охват — {органика} просмотров
(третий пункт про экономию НЕ пишем — план не выполнен)

Правила:
- Множители округляй до одного знака после запятой, используй ЗАПЯТУЮ как десятичный разделитель: «в 22,4 раза», НЕ «в 22.4 раз»
- Склонение: 1,0 → «раз», 1,1–1,4 → «раза», 1,5–1,9 → «раза», 2,0+ → «раза» (всегда «раза» кроме ровно 1)
- Цифры разделяй запятыми: 6,170,892
- НЕ добавляй другие пункты в этот раздел
- НИКОГДА не пиши отрицательные числа в виральном охвате или экономии

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
        # Ограничиваем выбросы: среднее не может быть больше 10× медианы просмотров
        channel_avg = stats.get("channel_avg", {})
        if channel_avg:
            n = channel_avg.get("posts_analyzed", 0)
            label = f"(среднее по последним {n} постам канала)"
            avg_views = channel_avg.get("avg_views") or 0

            def _cap(val, cap_multiplier=20):
                """Обрезаем аномальные значения: не более cap_multiplier × avg_views."""
                if val is None:
                    return None
                if avg_views and avg_views > 0 and val > avg_views * cap_multiplier:
                    return None  # явный выброс — не показываем
                return val

            if channel_avg.get("avg_views"):
                lines.append(f"  Норма канала — просмотры: {channel_avg['avg_views']:,} {label}")
            v = _cap(channel_avg.get("avg_likes"))
            if v:
                lines.append(f"  Норма канала — лайки: {v:,} {label}")
            v = _cap(channel_avg.get("avg_reposts"))
            if v:
                lines.append(f"  Норма канала — репосты: {v:,} {label}")
            v = _cap(channel_avg.get("avg_forwards"))
            if v:
                lines.append(f"  Норма канала — пересылки: {v:,} {label}")
            v = _cap(channel_avg.get("avg_comments"))
            if v:
                lines.append(f"  Норма канала — комментарии: {v:,} {label}")
            v = _cap(channel_avg.get("avg_saves"))
            if v:
                lines.append(f"  Норма канала — сохранения: {v:,} {label}")

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

    # CPV = бюджет размещений / (paid охват + органика)
    # Органика учитывается в CPV — так как увеличивает общий охват при том же бюджете
    placement_budget = total_placement_budget if total_placement_budget else total_budget
    total_reach_with_organic = total_actual_reach + total_organic_reach
    actual_cpv = placement_budget / total_reach_with_organic if total_reach_with_organic > 0 else 0
    planned_cpv = placement_budget / total_planned_reach if total_planned_reach > 0 else 0
    cpv_ratio = planned_cpv / actual_cpv if actual_cpv > 0 else 0
    reach_ratio = total_actual_reach / total_planned_reach if total_planned_reach > 0 else 0
    plan_exceeded = total_actual_reach >= total_planned_reach

    def _fmt_ratio_ru(r: float) -> str:
        s = f"{r:.1f}".replace(".", ",")
        return f"в {s} раза"

    def _fmt_money(v: float) -> str:
        return f"{v:,.2f}".replace(",", " ").replace(".", ",")

    def _fmt_cpv_diff(v: float) -> str:
        if v < 1:
            return f"{round(v * 100)} копеек"
        return f"{_fmt_money(v)} ₽"

    def _build_section1() -> str:
        reach_diff = total_actual_reach - total_planned_reach
        reach_abs = abs(reach_diff)
        reach_factor = max(reach_ratio, 1 / reach_ratio if reach_ratio > 0 else 0)

        # Строка про охват
        if total_planned_reach <= 0:
            reach_line = f"• Фактический охват по проекту составил {total_actual_reach:,} просмотров"
        elif reach_factor < 1.1:
            reach_line = (
                f"• Фактический охват равен запланированному: {total_actual_reach:,} просмотров "
                f"при плане {total_planned_reach:,}"
            )
        elif reach_ratio < 1 and reach_factor < 1.3:
            reach_line = (
                f"• Фактический охват составил {total_actual_reach:,} просмотров при плане "
                f"{total_planned_reach:,} — на {reach_abs:,} просмотров меньше плана"
            )
        elif reach_ratio >= 1 and reach_factor < 1.3:
            reach_line = (
                f"• Фактический охват составил {total_actual_reach:,} просмотров при плане "
                f"{total_planned_reach:,} — на {reach_abs:,} просмотров больше плана"
            )
        elif reach_ratio < 1:
            pct = total_actual_reach / total_planned_reach * 100
            reach_line = (
                f"• Фактический охват составил {total_actual_reach:,} просмотров при плане "
                f"{total_planned_reach:,} — выполнение плана на {pct:.0f}%"
            )
        else:
            reach_line = (
                f"• Вместо {total_planned_reach:,} просмотров у нас {total_actual_reach:,} просмотров! "
                f"Это {_fmt_ratio_ru(reach_ratio)} выше планируемого охвата!"
            )

        # Строка про CPV
        cpv_abs = abs(actual_cpv - planned_cpv)
        cpv_factor = max(cpv_ratio, 1 / cpv_ratio if cpv_ratio > 0 else 0)

        if planned_cpv <= 0 or actual_cpv <= 0:
            cpv_line = f"• Итоговый CPV по проекту: {_fmt_money(actual_cpv)} ₽"
        elif cpv_factor < 1.1:
            cpv_line = (
                f"• Итоговый CPV по проекту соответствует плану: {_fmt_money(actual_cpv)} ₽ "
                f"при плановом {_fmt_money(planned_cpv)} ₽"
            )
        elif cpv_factor < 1.3:
            direction = "меньше" if actual_cpv < planned_cpv else "больше"
            cpv_line = (
                f"• Итоговый CPV по проекту: {_fmt_money(actual_cpv)} ₽ при плановом "
                f"{_fmt_money(planned_cpv)} ₽ — на {_fmt_cpv_diff(cpv_abs)} {direction} плана"
            )
        elif actual_cpv < planned_cpv:
            cpv_line = (
                f"• Итоговый CPV по проекту: {_fmt_money(actual_cpv)} ₽ при плановом "
                f"{_fmt_money(planned_cpv)} ₽ — {_fmt_ratio_ru(planned_cpv / actual_cpv)} ниже"
            )
        else:
            cpv_line = (
                f"• Итоговый CPV по проекту: {_fmt_money(actual_cpv)} ₽ при плановом "
                f"{_fmt_money(planned_cpv)} ₽ — {_fmt_ratio_ru(actual_cpv / planned_cpv)} выше плана"
            )

        lines = [reach_line, cpv_line]
        if reach_diff > 0:
            lines.append(
                f"• Виральный охват по проекту составил {reach_diff:,} просмотров, "
                f"что эквивалентно экономии {total_savings:,.0f} рублей"
            )
        lines.append(f"• Суммарный органический охват — {total_organic_reach:,} просмотров")
        return "\n".join(lines)

    section1_text = _build_section1()

    # --- Предварительный расчёт кандидатов для раздела 2 ---
    # Группируем по URL: один пост — одна строка с перечислением всех превышений.
    # Делаем это в Python, чтобы GPT не мог пропустить ни одного кандидата.

    def _fmt_ratio(r: float) -> str:
        """Форматирует множитель: запятая как десятичный разделитель, склонение «раза»."""
        s = f"{r:.1f}".replace(".", ",")
        return f"в {s} раза"

    # post_url → {"max_ratio": float, "parts": [str], "views_ratio": float}
    post_hits: dict[str, dict] = {}

    for post in posts_data:
        if post.get("is_organic"):
            continue
        url = post.get("post_url", "")
        stats = post.get("stats", {})
        plan = post.get("planned_reach") or 0
        avg = stats.get("channel_avg") or {}
        n_posts = avg.get("posts_analyzed", 20)

        if url not in post_hits:
            post_hits[url] = {"max_ratio": 0.0, "parts": [], "views_ratio": 0.0}

        entry = post_hits[url]

        # Критерий А — просмотры ≥ 2× план
        views = stats.get("views")
        if views and plan and views >= 2 * plan:
            ratio = views / plan
            entry["parts"].insert(0, f"{views:,} просмотров при плане {plan:,} ({_fmt_ratio(ratio)} выше плана)")
            entry["views_ratio"] = ratio
            entry["max_ratio"] = max(entry["max_ratio"], ratio)

        # Критерий Б — лайки ≥ 1.5× норма
        likes = stats.get("likes")
        avg_likes = avg.get("avg_likes")
        if likes is not None and avg_likes and likes >= 1.5 * avg_likes:
            ratio = likes / avg_likes
            entry["parts"].append(f"{likes:,} лайков вместо средних {avg_likes:,} (среднее по {n_posts} постам канала)")
            entry["max_ratio"] = max(entry["max_ratio"], ratio)

        # Критерий Б — комментарии ≥ 1.5× норма
        comments = stats.get("comments")
        avg_comments = avg.get("avg_comments")
        if comments is not None and avg_comments and comments >= 1.5 * avg_comments:
            ratio = comments / avg_comments
            entry["parts"].append(f"{comments:,} комментариев вместо средних {avg_comments:,} (среднее по {n_posts} постам канала)")
            entry["max_ratio"] = max(entry["max_ratio"], ratio)

        # Критерий Б — репосты/форварды ≥ 1.5× норма
        reposts = stats.get("reposts") or stats.get("forwards")
        avg_reposts = avg.get("avg_reposts") or avg.get("avg_forwards")
        if reposts is not None and avg_reposts and reposts >= 1.5 * avg_reposts:
            ratio = reposts / avg_reposts
            label = "пересылок" if stats.get("forwards") else "репостов"
            entry["parts"].append(f"{reposts:,} {label} вместо средних {avg_reposts:,} (среднее по {n_posts} постам канала)")
            entry["max_ratio"] = max(entry["max_ratio"], ratio)

        # Критерий Б — реакции ≥ 1.5× норма
        reactions = stats.get("reactions_count")
        avg_reactions = avg.get("avg_reactions")
        if reactions is not None and avg_reactions and reactions >= 1.5 * avg_reactions:
            ratio = reactions / avg_reactions
            entry["parts"].append(f"{reactions:,} реакций вместо средних {avg_reactions:,} (среднее по {n_posts} постам канала)")
            entry["max_ratio"] = max(entry["max_ratio"], ratio)

    # Оставляем только посты с хотя бы одним превышением
    candidates = [(d["max_ratio"], url, d["parts"]) for url, d in post_hits.items() if d["parts"]]

    # Если ничего нет — fallback: просмотры ×1.1–1.99 (умеренное превышение)
    if not candidates:
        for post in posts_data:
            if post.get("is_organic"):
                continue
            url = post.get("post_url", "")
            stats = post.get("stats", {})
            plan = post.get("planned_reach") or 0
            views = stats.get("views")
            if views and plan and views >= 1.1 * plan:
                ratio = views / plan
                candidates.append((ratio, url, [
                    f"{views:,} просмотров при плане {plan:,} ({_fmt_ratio(ratio)} выше плана) (умеренное превышение)"
                ]))

    # Топ-5 по максимальному множителю
    candidates.sort(key=lambda x: x[0], reverse=True)
    superresults_lines = []
    for _, url, parts in candidates[:5]:
        superresults_lines.append(f"Пост {url} — " + "; ".join(parts))
    superresults_text = "\n".join(superresults_lines)

    viral_reach = total_actual_reach - total_planned_reach
    user_message = f"""Проект: {project_name}

ПУБЛИКАЦИИ:
{posts_text}

ССЫЛКИ НА ПОСТЫ (копируй дословно, не изменяй ни символ):
{links_map}

ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 1 (вставь их дословно, не пересчитывай):
{section1_text}

ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 2 (вставь их дословно, не перефразируй, не сокращай список):
{superresults_text}

Сформируй отчёт строго в трёх разделах как указано в инструкции.
Раздел 1: используй строки из блока "ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 1" дословно — не пересчитывай CPV, охват и экономию сам.
Раздел 2: используй строки из блока "ГОТОВЫЕ СТРОКИ ДЛЯ РАЗДЕЛА 2" дословно.

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

текст"""

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
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[COMMENTS DEBUG] Posts with top_comments in analyzer: {len(posts_with_comments)}")
    for i, p in enumerate(posts_with_comments):
        url = p.get("post_url", "unknown")
        comments_count = len(p.get("stats", {}).get("top_comments", []))
        logger.info(f"[COMMENTS DEBUG] Post {i+1}: {url} has {comments_count} top_comments")

    comments_coro = None
    if posts_with_comments:
        logger.info(f"[COMMENTS DEBUG] Creating comments_coro for {len(posts_with_comments)} posts")
        comments_coro = _analyze_comments(posts_with_comments)
    else:
        logger.warning(f"[COMMENTS DEBUG] No posts with comments - comments block will be skipped")

    if comments_coro:
        logger.info(f"[COMMENTS DEBUG] Running accent_coro and comments_coro in parallel")
        accent_resp, comments_text = await asyncio.gather(accent_coro, comments_coro)
        logger.info(f"[COMMENTS DEBUG] Comments text length: {len(comments_text) if comments_text else 0}")
    else:
        logger.info(f"[COMMENTS DEBUG] Running only accent_coro (no comments)")
        accent_resp = await accent_coro
        comments_text = None

    result = accent_resp.choices[0].message.content
    logger.info(f"[COMMENTS DEBUG] Main result length: {len(result)}")

    if comments_text:
        logger.info(f"[COMMENTS DEBUG] Appending comments block to result")
        result += f"\n\n---\n\n{comments_text}"
        logger.info(f"[COMMENTS DEBUG] Final result length with comments: {len(result)}")
    else:
        logger.warning(f"[COMMENTS DEBUG] No comments_text to append")

    return result


async def _analyze_comments(posts_with_comments: list[dict]) -> str:
    """Отдельный блок — о чём писали люди в комментариях."""
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[COMMENTS DEBUG] _analyze_comments called with {len(posts_with_comments)} posts")

    blocks = []
    # Сохраняем ссылки отдельно — передадим их в итоговый текст напрямую
    post_urls = []

    for p in posts_with_comments:
        post_url = p.get("post_url", "")
        platform = p.get("platform", "")
        comments_count = p.get("stats", {}).get("comments", 0) or 0
        top_comments = p.get("stats", {}).get("top_comments", [])
        logger.info(f"[COMMENTS DEBUG] Processing post {post_url}: {len(top_comments)} top_comments, {comments_count} total")
        if not top_comments:
            logger.warning(f"[COMMENTS DEBUG] Skipping post {post_url} - no top_comments")
            continue
        comments_text = "\n".join(f'  — «{c[:200]}»' for c in top_comments)
        post_urls.append(post_url)
        blocks.append(
            f"ПОСТ {len(post_urls)} ({platform}), {comments_count} комментариев:\n{comments_text}"
        )
        logger.info(f"[COMMENTS DEBUG] Added block for post {len(post_urls)}")

    if not blocks:
        logger.warning(f"[COMMENTS DEBUG] No blocks created - returning empty string")
        return ""
    
    logger.info(f"[COMMENTS DEBUG] Created {len(blocks)} blocks, sending to OpenAI")

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
