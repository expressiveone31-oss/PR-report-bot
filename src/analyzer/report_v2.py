"""Детерминированная сборка внутреннего отчёта /sumup v2."""

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

MARKET_CPV = 1.5
PLATFORM_NAMES = {
    "vk": "ВКонтакте",
    "telegram": "Telegram",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "twitter": "X (Twitter)",
    "threads": "Threads",
    "unknown": "Площадка не определена",
}
MONTHS_RU = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@dataclass
class ReportMetrics:
    paid_plan: int
    paid_actual: int
    organic_actual: int
    total_actual: int
    paid_overperformance: int
    paid_completion_pct: float
    paid_overperformance_pct: float
    placement_budget: float
    planned_cpv: float
    actual_cpv: float
    organic_budget_equivalent: float
    paid_posts: int
    paid_channels: int
    organic_posts: int
    organic_channels: int
    control_total: Optional[int]
    control_difference: Optional[int]


def _num(value: int | float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,.0f}".replace(",", " ")


def _money(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _cpv(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:.2f}".replace(".", ",") + " ₽"


def _plural(value: int, one: str, few: str, many: str) -> str:
    value = abs(int(value))
    if value % 10 == 1 and value % 100 != 11:
        return one
    if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        return few
    return many


def _publication_count(value: int) -> str:
    return f"{value} {_plural(value, 'публикация', 'публикации', 'публикаций')}"


def _channel_count(value: int) -> str:
    return f"{value} {_plural(value, 'канале', 'каналах', 'каналах')}"


def _channel_name(post: dict) -> str:
    name = str(post.get("name") or "").strip()
    if name and not name.startswith(("http://", "https://")):
        return name
    channel_url = str(post.get("channel_url") or "").strip()
    if channel_url and not channel_url.startswith(("http://", "https://")):
        return channel_url
    return PLATFORM_NAMES.get(post.get("platform"), "Площадка не определена")


def _channel_key(post: dict) -> str:
    name = _channel_name(post).casefold()
    raw_name = str(post.get("name") or "").strip()
    if raw_name and not raw_name.startswith(("http://", "https://")):
        return f"{post.get('platform', 'unknown')}:{name}"
    channel_url = str(post.get("channel_url") or "").strip().rstrip("/").casefold()
    if channel_url:
        platform = post.get("platform")
        if platform == "telegram":
            parts = channel_url.split("/")
            return "/".join(parts[:4])
        if platform == "vk":
            import re
            match = re.search(r"(?:wall|clip|video)(-?\d+)_", channel_url)
            if match:
                return f"vk:{match.group(1)}"
        return channel_url
    return f"{post.get('platform', 'unknown')}:{name}"


def _parse_date(raw: str | date | datetime | None) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    value = str(raw).strip()
    if value.isdigit():
        try:
            timestamp = int(value)
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in (
        "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%a %b %d %H:%M:%S %z %Y", "%d/%m/%Y", "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _display_date(raw: str | date | datetime | None) -> str:
    parsed = _parse_date(raw)
    return f"{parsed.day} {MONTHS_RU[parsed.month]}" if parsed else "нет данных"


def _post_link(post: dict) -> str:
    name = html.escape(_channel_name(post))
    url = str(post.get("post_url") or "").strip()
    if not url:
        return name
    return f'<a href="{html.escape(url, quote=True)}">{name}</a>'


def _publication_links(posts: list[dict]) -> list[str]:
    return [f"• {_post_link(post)}" for post in posts]


def resolve_fact_sources(posts_data: list[dict]) -> None:
    """API имеет приоритет, факт из МП используется как fallback.

    Правила «API вернул валидное число»:
    - views is None → API не дал ответа, fallback на МП;
    - views == 0 при непустом mp_actual_reach → трактуем как «API молчит»
      (частый кейс: Instagram/VK возвращают 0 когда пост недоступен или
      квота ключа исчерпана). Берём число из МП.
    - views == 0 и в МП тоже пусто → честно ставим 0 (реально 0 просмотров).
    - views > 0 → используем API-значение как источник истины.
    """
    for post in posts_data:
        stats = post.setdefault("stats", {})
        api_views = stats.get("views")
        mp_views = post.get("mp_actual_reach")

        api_valid = api_views is not None and api_views > 0
        mp_valid = mp_views is not None and mp_views > 0

        if api_valid:
            stats["views"] = int(api_views)
            post["fact_source"] = "API"
        elif mp_valid:
            stats["views"] = int(mp_views)
            post["fact_source"] = "МП"
        elif api_views == 0:
            # API вернул 0, в МП тоже пусто — честный ноль.
            stats["views"] = 0
            post["fact_source"] = "API"
        else:
            stats["views"] = None
            post["fact_source"] = "нет данных"


def calculate_metrics(
    posts_data: list[dict],
    planned_reach: int,
    placement_budget: float,
    control_total: Optional[int] = None,
) -> ReportMetrics:
    paid = [p for p in posts_data if not p.get("is_organic")]
    organic = [p for p in posts_data if p.get("is_organic")]
    paid_actual = sum((p.get("stats") or {}).get("views") or 0 for p in paid)
    organic_actual = sum((p.get("stats") or {}).get("views") or 0 for p in organic)
    total_actual = paid_actual + organic_actual
    paid_plan = int(planned_reach or sum(p.get("planned_reach") or 0 for p in paid))
    over = paid_actual - paid_plan
    completion = paid_actual / paid_plan * 100 if paid_plan else 0
    over_pct = over / paid_plan * 100 if paid_plan else 0
    planned_cpv = placement_budget / paid_plan if paid_plan else 0
    actual_cpv = placement_budget / total_actual if total_actual else 0
    return ReportMetrics(
        paid_plan=paid_plan,
        paid_actual=paid_actual,
        organic_actual=organic_actual,
        total_actual=total_actual,
        paid_overperformance=over,
        paid_completion_pct=completion,
        paid_overperformance_pct=over_pct,
        placement_budget=placement_budget,
        planned_cpv=planned_cpv,
        actual_cpv=actual_cpv,
        organic_budget_equivalent=organic_actual * MARKET_CPV,
        paid_posts=len(paid),
        paid_channels=len({_channel_key(p) for p in paid}),
        organic_posts=len(organic),
        organic_channels=len({_channel_key(p) for p in organic}),
        control_total=control_total,
        control_difference=(total_actual - control_total) if control_total is not None else None,
    )


def _build_paid_table(posts: list[dict]) -> str:
    blocks: list[str] = []
    for post in posts:
        plan = int(post.get("planned_reach") or 0)
        fact = (post.get("stats") or {}).get("views")
        if fact is None:
            fact_sentence = "Фактический охват — нет данных."
            delta_sentence = "Отклонение от плана посчитать не удалось."
        elif plan:
            diff = fact - plan
            pct = diff / plan * 100
            fact_sentence = f"Охват по плану — {_num(plan)}, фактический охват — {_num(fact)} просмотров."
            delta_sentence = (
                f"{diff:+,} просмотров ({pct:+.0f}%) от запланированного показателя"
                .replace(",", " ")
            )
        else:
            fact_sentence = f"Плановый охват не указан, фактический охват — {_num(fact)} просмотров."
            delta_sentence = "Отклонение от плана посчитать нельзя."
        blocks.append(
            f"{_post_link(post)} | {_display_date(post.get('date'))} | {fact_sentence}\n"
            f"{delta_sentence}"
        )
    return "\n\n".join(blocks)


def _build_organic_table(posts: list[dict]) -> str:
    blocks = []
    for post in posts:
        blocks.append(
            f"{_post_link(post)} | {_display_date(post.get('date'))} | "
            f"Охват — {_num((post.get('stats') or {}).get('views'))} просмотров."
        )
    return "\n\n".join(blocks)


def _superresults(posts_data: list[dict]) -> list[str]:
    candidates: list[tuple[float, str]] = []
    moderate: list[tuple[float, str]] = []
    for post in posts_data:
        if post.get("is_organic"):
            continue
        stats = post.get("stats") or {}
        plan = post.get("planned_reach") or 0
        url = post.get("post_url") or "нет ссылки"
        parts: list[str] = []
        max_ratio = 0.0
        views = stats.get("views")
        if views is not None and plan:
            ratio = views / plan
            if ratio >= 2:
                parts.append(f"{_num(views)} просмотров при плане {_num(plan)} ({ratio:.1f}× плана)".replace(".", ","))
                max_ratio = max(max_ratio, ratio)
            elif ratio >= 1.1:
                moderate.append((ratio, f"{_post_link(post)} — {_num(views)} просмотров при плане {_num(plan)} ({ratio:.1f}×, умеренное превышение)".replace(".", ",")))
        avg = stats.get("channel_avg") or {}
        n = avg.get("posts_analyzed") or 0
        metric_pairs = [
            ("лайков", stats.get("likes"), avg.get("avg_likes")),
            ("комментариев", stats.get("comments"), avg.get("avg_comments")),
            ("репостов", stats.get("reposts") or stats.get("forwards"), avg.get("avg_reposts") or avg.get("avg_forwards")),
            ("реакций", stats.get("reactions_count"), avg.get("avg_reactions")),
        ]
        for label, value, usual in metric_pairs:
            if value is not None and usual and value >= 1.5 * usual:
                ratio = value / usual
                parts.append(f"{_num(value)} {label} вместо средних {_num(usual)} (по {n} постам, {ratio:.1f}×)".replace(".", ","))
                max_ratio = max(max_ratio, ratio)
        if parts:
            candidates.append((max_ratio, f"{_post_link(post)} — " + "; ".join(parts)))
    selected = candidates or moderate
    selected.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in selected[:5]]


def _engagement_rows(posts_data: list[dict]) -> list[str]:
    rows: list[tuple[float, str]] = []
    for post in posts_data:
        if post.get("is_organic"):
            continue
        stats = post.get("stats") or {}
        avg = stats.get("channel_avg") or {}
        reposts = stats.get("reposts")
        if reposts is None:
            reposts = stats.get("forwards")
        avg_reposts = avg.get("avg_reposts")
        if avg_reposts is None:
            avg_reposts = avg.get("avg_forwards")
        metrics = [
            ("лайков", stats.get("likes"), avg.get("avg_likes")),
            ("комментариев", stats.get("comments"), avg.get("avg_comments")),
            ("репостов/пересылок", reposts, avg_reposts),
            ("реакций", stats.get("reactions_count"), avg.get("avg_reactions")),
        ]
        comparisons: list[tuple[str, str]] = []
        max_score = 0.0
        for label, current, usual in metrics:
            if current is None or not usual:
                continue
            ratio = current / usual
            if ratio >= 1.25:
                comparisons.append(("больше", label))
            elif ratio <= 0.6:
                comparisons.append(("меньше", label))
            else:
                continue
            score = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
            max_score = max(max_score, score)
        if comparisons:
            phrases = [
                f"{direction} {label}" + (" чем обычно" if index == 0 else "")
                for index, (direction, label) in enumerate(comparisons)
            ]
            rows.append((max_score, f"{_post_link(post)} — " + ", ".join(phrases) + "."))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in rows[:15]]


def _generalized_engagement(posts_data: list[dict]) -> str:
    """Обобщает вовлечённость по всему paid-посеву по старой методологии."""
    metric_specs = (
        ("лайков/реакций", "likes"),
        ("комментариев", "comments"),
        ("репостов/пересылок", "reposts"),
    )
    outcomes: list[str] = []

    for label, metric in metric_specs:
        classified: list[dict] = []
        for post in posts_data:
            if post.get("is_organic"):
                continue
            stats = post.get("stats") or {}
            avg = stats.get("channel_avg") or {}

            if metric == "likes":
                current = stats.get("likes")
                usual = avg.get("avg_likes")
                if current is None:
                    current = stats.get("reactions_count")
                if usual is None:
                    usual = avg.get("avg_reactions")
            elif metric == "reposts":
                current = stats.get("reposts")
                usual = avg.get("avg_reposts")
                if current is None:
                    current = stats.get("forwards")
                if usual is None:
                    usual = avg.get("avg_forwards")
            else:
                current = stats.get("comments")
                usual = avg.get("avg_comments")

            if current is None or not usual:
                continue
            ratio = current / usual
            category = "more" if ratio >= 1.25 else "less" if ratio <= 0.6 else "same"
            classified.append({
                "category": category,
                "ratio": ratio,
                "current": current,
                "usual": usual,
                "post": post,
            })

        if not classified:
            continue

        counts = {
            category: sum(1 for item in classified if item["category"] == category)
            for category in ("more", "less", "same")
        }
        max_count = max(counts.values())
        winners = [category for category, count in counts.items() if count == max_count]
        if len(winners) != 1:
            outcomes.append(f"количество {label} различалось от канала к каналу")
            continue

        winner = winners[0]
        if winner == "same":
            outcomes.append(f"обычное количество {label}")
            continue

        matching = [item for item in classified if item["category"] == winner]
        example = (
            max(matching, key=lambda item: item["ratio"])
            if winner == "more"
            else min(matching, key=lambda item: item["ratio"])
        )
        direction = "больше" if winner == "more" else "меньше"
        outcomes.append(
            f"{direction} {label} (сильнее всего у {_post_link(example['post'])} — "
            f"{_num(example['current'])} вместо примерно {_num(example['usual'])} обычно)"
        )

    if not outcomes:
        return "Недостаточно данных о средних показателях каналов для сравнения вовлечённости."
    if len(outcomes) == 1:
        joined = outcomes[0]
    else:
        joined = ", ".join(outcomes[:-1]) + " и " + outcomes[-1]
    return (
        "Если сравнивать посты посева с обычными публикациями на тех же каналах, "
        f"они в среднем набирали {joined}."
    )


def _chronology(posts_data: list[dict]) -> list[str]:
    paid_dates = [(_parse_date(p.get("date")), p) for p in posts_data if not p.get("is_organic")]
    organic_dates = [(_parse_date(p.get("date")), p) for p in posts_data if p.get("is_organic")]
    paid_known = [d for d, _ in paid_dates if d]
    organic_known = [d for d, _ in organic_dates if d]
    with_dates = [(d, p) for d, p in paid_dates + organic_dates if d]
    strongest = None
    if with_dates:
        strongest = max(with_dates, key=lambda item: (item[1].get("stats") or {}).get("views") or 0)
    all_known = paid_known + organic_known
    period = (
        f"Период посева: с {_display_date(min(all_known))} по {_display_date(max(all_known))}"
        if all_known else "Период посева: нет данных"
    )
    return [
        period,
        f"Дата запуска: {_display_date(min(paid_known)) if paid_known else 'нет данных'}",
        f"Первые органические публикации: {_display_date(min(organic_known)) if organic_known else 'нет данных'}",
        (
            f"Самая результативная публикация вышла: {_display_date(strongest[0])} "
            f"({_post_link(strongest[1])}, {_num((strongest[1].get('stats') or {}).get('views'))} просмотров)"
            if strongest else "Самая результативная публикация: дата не определена"
        ),
        f"Данные зафиксированы: {_display_date(date.today())}",
    ]


def _flight_dates(posts_data: list[dict]) -> tuple[str, str]:
    dates = [
        parsed
        for post in posts_data
        for parsed in [_parse_date(post.get("date"))]
        if parsed
    ]
    if not dates:
        return "нет данных", "нет данных"
    return _display_date(min(dates)), _display_date(max(dates))


async def _brief_summary(project_name: str, metrics: ReportMetrics, superresults: list[str]) -> str:
    facts = {
        "project": project_name,
        "paid_plan": metrics.paid_plan,
        "paid_actual": metrics.paid_actual,
        "organic_actual": metrics.organic_actual,
        "total_actual": metrics.total_actual,
        "paid_overperformance": metrics.paid_overperformance,
        "organic_budget_equivalent": round(metrics.organic_budget_equivalent),
        "planned_cpv": round(metrics.planned_cpv, 2),
        "actual_cpv": round(metrics.actual_cpv, 2),
        "strongest_results": superresults[:2],
    }
    prompt = (
        "Напиши краткий вывод к внутреннему отчёту Digital PR: 3–5 предложений, "
        "делово и живо. Используй только факты из JSON. Разделяй paid-перевыполнение "
        "и органику. Экономия относится только к органике: это бюджет, который "
        "потребовался бы при рыночном CPV 1,5 ₽. Не используй Markdown и списки.\n\n"
        + json.dumps(facts, ensure_ascii=False)
    )
    try:
        logger.info("Generating brief summary via OpenAI: model=%s", OPENAI_MODEL)
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=45,
        )
        result = (response.choices[0].message.content or "").strip()
        if result:
            logger.info("Brief summary generated: %s chars", len(result))
            return result
        raise ValueError("OpenAI вернул пустой краткий вывод")
    except Exception as exc:
        logger.warning("Brief summary failed, using deterministic fallback: %s", exc)
        return (
            f"Проект собрал {_num(metrics.total_actual)} просмотров: "
            f"{_num(metrics.paid_actual)} на paid-размещениях и {_num(metrics.organic_actual)} органически. "
            f"Фактический CPV с учётом органики составил {_cpv(metrics.actual_cpv)}."
        )


async def build_report_v2(
    project_name: str,
    posts_data: list[dict],
    planned_reach: int,
    placement_budget: float,
    control_total: Optional[int] = None,
) -> tuple[str, ReportMetrics]:
    resolve_fact_sources(posts_data)
    metrics = calculate_metrics(posts_data, planned_reach, placement_budget, control_total)
    paid = [p for p in posts_data if not p.get("is_organic")]
    organic = [p for p in posts_data if p.get("is_organic")]
    superresults = _superresults(posts_data)
    engagement_summary = _generalized_engagement(posts_data)

    from src.analyzer.openai_analyzer import _analyze_comments

    comments_available = [p for p in posts_data if (p.get("stats") or {}).get("top_comments")]
    comments_posts = sorted(
        comments_available,
        key=lambda post: (post.get("stats") or {}).get("comments") or 0,
        reverse=True,
    )[:5]
    summary_task = _brief_summary(project_name, metrics, superresults)
    comments_task = _analyze_comments(comments_posts) if comments_posts else None
    if comments_task:
        summary, comments_text = await asyncio.gather(summary_task, comments_task, return_exceptions=True)
        if isinstance(summary, Exception):
            logger.warning("Summary task failed: %s", summary)
            summary = "Краткий вывод сформировать не удалось."
        if isinstance(comments_text, Exception):
            logger.warning("Comments task failed: %s", comments_text)
            comments_text = ""
    else:
        summary = await summary_task
        comments_text = ""

    if comments_text:
        comments_text = comments_text.removeprefix("О чём писали в комментариях:\n\n")

    control_lines: list[str] = []
    if metrics.control_total is not None and metrics.control_difference:
        control_lines = [
            f"Контрольный итог в МП: {_num(metrics.control_total)}",
            f"Пересчитанный актуальный итог: {_num(metrics.total_actual)}",
            f"Расхождение: {metrics.control_difference:+,}".replace(",", " "),
        ]

    if metrics.paid_plan:
        if metrics.paid_overperformance >= 0:
            paid_result = (
                f"Перевыполнение paid: +{_num(metrics.paid_overperformance)} просмотров "
                f"(+{metrics.paid_overperformance_pct:.0f}%)"
            )
        else:
            paid_result = (
                f"Paid не добрал {_num(abs(metrics.paid_overperformance))} просмотров; "
                f"выполнение плана — {metrics.paid_completion_pct:.0f}%"
            )
    else:
        paid_result = "Перевыполнение paid: нет плановых данных"

    flight_start, flight_end = _flight_dates(posts_data)
    lines = [
        "<b>КРАТКИЙ ВЫВОД</b>",
        "",
        html.escape(str(summary)),
        "",
        "<b>ОБЩИЕ РЕЗУЛЬТАТЫ</b>",
        "",
        f"Проект: {html.escape(project_name or 'Без названия')}",
        f"Даты флайта: {flight_start} — {flight_end}",
        f"Плановый paid-охват: {_num(metrics.paid_plan)}",
        f"Фактический paid-охват: {_num(metrics.paid_actual)}",
        f"Общий охват с органикой: {_num(metrics.total_actual)}",
        f"Выполнение paid-плана: {metrics.paid_completion_pct:.0f}%",
        f"Paid: {_publication_count(metrics.paid_posts)} в {_channel_count(metrics.paid_channels)}",
        f"Органика: {_publication_count(metrics.organic_posts)} в {_channel_count(metrics.organic_channels)}",
        f"Бюджет размещений: {_money(metrics.placement_budget)}",
        f"Плановый CPV: {_cpv(metrics.planned_cpv)}",
        f"Фактический CPV с учётом органики: {_cpv(metrics.actual_cpv)}",
    ]
    if control_lines:
        lines.extend(["", *control_lines])

    lines.extend([
        "",
        "<b>ПЕРЕВЫПОЛНЕНИЕ И ЭКОНОМИЯ БЮДЖЕТА</b>",
        "",
        paid_result,
        f"Органический охват: {_num(metrics.organic_actual)} просмотров",
        (
            f"При рыночном CPV {str(MARKET_CPV).replace('.', ',')} ₽ для получения такого "
            f"органического охвата потребовалось бы дополнительно около "
            f"{_money(metrics.organic_budget_equivalent)} рекламного бюджета."
        ),
        "Формула: органический охват × 1,5 ₽.",
        "",
        "<b>ХРОНОЛОГИЯ</b>",
        "",
        *(_chronology(posts_data)),
        "",
        "<b>СВЕРХРЕЗУЛЬТАТЫ</b>",
        "",
        *(superresults or ["Сильных отклонений от плана и нормы канала не найдено."]),
        "",
        "<b>АНАЛИТИКА ПО ЛАЙКАМ, КОММЕНТАРИЯМ И РЕПОСТАМ</b>",
        "",
        engagement_summary,
        "",
        "<b>О ЧЁМ ПИСАЛИ В КОММЕНТАРИЯХ</b>",
        "",
        (
            (
                f"Комментарии проанализированы по 5 наиболее обсуждаемым публикациям "
                f"из {len(posts_data)}."
                if len(comments_posts) == 5
                else f"Комментарии проанализированы по {len(comments_posts)} "
                f"{_plural(len(comments_posts), 'публикации', 'публикациям', 'публикациям')} "
                f"из {len(posts_data)}."
            )
        ),
        html.escape(comments_text) if comments_text else "Тексты комментариев для анализа не получены.",
        "",
        "<b>ВСЕ ПУБЛИКАЦИИ</b>",
        "",
        *_publication_links(paid),
    ])
    if organic:
        lines.extend([
            "",
            "<b>ОРГАНИКА</b>",
            "",
            *_publication_links(organic),
        ])
    return "\n".join(lines), metrics
