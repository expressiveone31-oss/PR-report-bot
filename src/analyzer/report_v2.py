"""Детерминированная сборка внутреннего отчёта /sumup v2."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
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


def _parse_date(raw: str | None) -> Optional[date]:
    if not raw:
        return None
    value = str(raw).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _display_date(raw: str | None) -> str:
    parsed = _parse_date(raw)
    return parsed.strftime("%d.%m.%Y") if parsed else (str(raw).strip() if raw else "нет данных")


def resolve_fact_sources(posts_data: list[dict]) -> None:
    """API имеет приоритет, факт из МП используется как fallback."""
    for post in posts_data:
        stats = post.setdefault("stats", {})
        api_views = stats.get("views")
        mp_views = post.get("mp_actual_reach")
        if api_views is not None:
            stats["views"] = int(api_views)
            post["fact_source"] = "API"
        elif mp_views is not None:
            stats["views"] = int(mp_views)
            post["fact_source"] = "МП"
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
    lines = ["Канал | Ссылка | Дата | План | Факт | Δ к плану | Источник"]
    for post in posts:
        plan = int(post.get("planned_reach") or 0)
        fact = (post.get("stats") or {}).get("views")
        if fact is None:
            delta = "нет данных"
        elif plan:
            diff = fact - plan
            pct = diff / plan * 100
            delta = f"{diff:+,} / {pct:+.0f}%".replace(",", " ")
        else:
            delta = "нет плана"
        lines.append(
            " | ".join([
                _channel_name(post),
                post.get("post_url") or "нет ссылки",
                _display_date(post.get("date")),
                _num(plan),
                _num(fact),
                delta,
                post.get("fact_source") or "нет данных",
            ])
        )
    return "\n".join(lines)


def _build_organic_table(posts: list[dict]) -> str:
    lines = ["Канал | Ссылка | Дата | Охват | Источник"]
    for post in posts:
        lines.append(
            " | ".join([
                _channel_name(post),
                post.get("post_url") or "нет ссылки",
                _display_date(post.get("date")),
                _num((post.get("stats") or {}).get("views")),
                post.get("fact_source") or "нет данных",
            ])
        )
    return "\n".join(lines)


def _superresults(posts_data: list[dict]) -> list[str]:
    candidates: list[tuple[float, str]] = []
    moderate: list[tuple[float, str]] = []
    for post in posts_data:
        if post.get("is_organic"):
            continue
        stats = post.get("stats") or {}
        plan = post.get("planned_reach") or 0
        url = post.get("post_url") or "нет ссылки"
        name = _channel_name(post)
        parts: list[str] = []
        max_ratio = 0.0
        views = stats.get("views")
        if views is not None and plan:
            ratio = views / plan
            if ratio >= 2:
                parts.append(f"{_num(views)} просмотров при плане {_num(plan)} ({ratio:.1f}× плана)".replace(".", ","))
                max_ratio = max(max_ratio, ratio)
            elif ratio >= 1.1:
                moderate.append((ratio, f"{name} — {url} — {_num(views)} просмотров при плане {_num(plan)} ({ratio:.1f}×, умеренное превышение)".replace(".", ",")))
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
            candidates.append((max_ratio, f"{name} — {url} — " + "; ".join(parts)))
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
        name = _channel_name(post)
        metrics = [
            ("Лайки", stats.get("likes"), avg.get("avg_likes")),
            ("Комментарии", stats.get("comments"), avg.get("avg_comments")),
            ("Репосты/пересылки", stats.get("reposts") or stats.get("forwards"), avg.get("avg_reposts") or avg.get("avg_forwards")),
            ("Реакции", stats.get("reactions_count"), avg.get("avg_reactions")),
        ]
        for label, current, usual in metrics:
            if current is None or not usual:
                continue
            ratio = current / usual
            if ratio >= 1.25:
                deviation = f"{ratio:.1f}×, выше".replace(".", ",")
            elif ratio <= 0.6:
                deviation = f"{ratio:.1f}×, ниже".replace(".", ",")
            else:
                continue
            score = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
            rows.append((score, f"{name} | {label} | {_num(usual)} | {_num(current)} | {deviation}"))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in rows[:15]]


def _chronology(posts_data: list[dict]) -> list[str]:
    paid_dates = [(_parse_date(p.get("date")), p) for p in posts_data if not p.get("is_organic")]
    organic_dates = [(_parse_date(p.get("date")), p) for p in posts_data if p.get("is_organic")]
    paid_known = [d for d, _ in paid_dates if d]
    organic_known = [d for d, _ in organic_dates if d]
    with_dates = [(d, p) for d, p in paid_dates + organic_dates if d]
    strongest = None
    if with_dates:
        strongest = max(with_dates, key=lambda item: (item[1].get("stats") or {}).get("views") or 0)
    return [
        f"Дата запуска: {min(paid_known).strftime('%d.%m.%Y') if paid_known else 'нет данных'}",
        f"Первые органические публикации: {min(organic_known).strftime('%d.%m.%Y') if organic_known else 'нет данных'}",
        (
            f"Самая результативная публикация вышла: {strongest[0].strftime('%d.%m.%Y')} "
            f"({_channel_name(strongest[1])}, {_num((strongest[1].get('stats') or {}).get('views'))} просмотров)"
            if strongest else "Самая результативная публикация: дата не определена"
        ),
        f"Данные зафиксированы: {date.today().strftime('%d.%m.%Y')}",
    ]


def _flight_dates(posts_data: list[dict]) -> tuple[str, str]:
    dates = [
        parsed
        for post in posts_data
        if not post.get("is_organic")
        for parsed in [_parse_date(post.get("date"))]
        if parsed
    ]
    if not dates:
        return "нет данных", "нет данных"
    return min(dates).strftime("%d.%m.%Y"), max(dates).strftime("%d.%m.%Y")


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
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=45,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Brief summary failed: %s", exc)
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
    engagement = _engagement_rows(posts_data)

    from src.analyzer.openai_analyzer import _analyze_comments

    comments_posts = [p for p in posts_data if (p.get("stats") or {}).get("top_comments")]
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
        "КРАТКИЙ ВЫВОД",
        "",
        str(summary),
        "",
        "ОБЩИЕ РЕЗУЛЬТАТЫ",
        "",
        f"Проект: {project_name or 'Без названия'}",
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
        "PAID-ПОСТЫ",
        "",
        _build_paid_table(paid) if paid else "Нет paid-публикаций",
        "",
        "ОРГАНИКА",
        "",
        _build_organic_table(organic) if organic else "Органических публикаций нет",
        "",
        f"Итого органика: {_num(metrics.organic_actual)}",
        "",
        "ПЕРЕВЫПОЛНЕНИЕ И ЭКОНОМИЯ БЮДЖЕТА",
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
        "ХРОНОЛОГИЯ",
        "",
        *(_chronology(posts_data)),
        "",
        "СВЕРХРЕЗУЛЬТАТЫ",
        "",
        *(superresults or ["Сильных отклонений от плана и нормы канала не найдено."]),
        "",
        "АНАЛИТИКА ВОВЛЕЧЁННОСТИ",
        "",
        "Канал | Метрика | Обычно | В посеве | Отклонение",
        *(engagement or ["Заметных отклонений по доступным метрикам не найдено."]),
        "",
        "КОММЕНТАРИИ",
        "",
        f"Комментарии проанализированы по {len(comments_posts)} публикациям из {len(posts_data)}.",
        comments_text or "Тексты комментариев для анализа не получены.",
    ])
    return "\n".join(lines), metrics
