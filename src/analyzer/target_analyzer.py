"""
Анализатор таргет/перфоманс кампаний.
Генерирует клиентский отчёт по данным из TargetMediaPlan без API-запросов.
"""

import logging
from openai import AsyncOpenAI
from src.config import OPENAI_API_KEY, OPENAI_MODEL
from src.parsers.target_mp import TargetMediaPlan, TargetRow

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Ты — аналитик Digital PR агентства. Пишешь клиентский отчёт по таргетированной/перфоманс кампании.

Тон: деловой, живой, конкретный. Не сухая таблица — понятный вывод для клиента.
Никаких вводных фраз типа «Подводя итоги» или «Отчёт по кампании».
Сразу по делу, с цифрами.

СТРУКТУРА ОТЧЁТА — строго три раздела:

ОБЩИЕ РЕЗУЛЬТАТЫ
• Бюджет: факт vs план (выполнение в %)
• Охват/показы: факт vs план
• CPV или CPM: факт vs план — лучше или хуже
• Переходы: факт vs план (если есть)
• Общий вывод одной фразой

РЕЗУЛЬТАТЫ ПО КАНАЛАМ
Для каждого канала — одна строка с ключевыми метриками и коротким выводом.
Формат: [Канал] — [ключевые цифры] — [вывод]
Если есть CPA — выдели самый дешёвый и самый дорогой канал по CPA.
Выдели лучший и худший канал по качеству трафика (% отказов + время на сайте).

ВЫВОДЫ И РЕКОМЕНДАЦИИ
2-3 конкретных вывода: что сработало, что нет, что масштабировать.
Если есть CPA — сравни каналы по стоимости привлечения.
Опирайся на % отказов и время на сайте как показатели качества трафика.

ПРАВИЛА:
- Используй ТОЛЬКО цифры из данных — не придумывай
- Числа форматируй с разделителями: 1 000 000
- % отказов: меньше = лучше
- Время на сайте: больше = лучше
- CPA: меньше = лучше
- Если факт лучше плана — отмечай это позитивно
- Если данных нет (прочерк) — не упоминай этот показатель
"""


def _fmt(value, prefix="", suffix="", decimals=0) -> str:
    if value is None:
        return "—"
    if decimals:
        return f"{prefix}{value:,.{decimals}f}{suffix}".replace(",", " ")
    return f"{prefix}{int(value):,}{suffix}".replace(",", " ")


def _fmt_cpv(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f} руб.".replace(".", ",")


def _row_to_text(row: TargetRow) -> str:
    lines = [f"Канал: {row.channel}" + (f" / {row.target}" if row.target else "")]

    if row.cost_plan or row.cost_fact:
        lines.append(f"  Бюджет: план {_fmt(row.cost_plan, 'р.')} → факт {_fmt(row.cost_fact, 'р.')}")

    if row.impressions_plan or row.impressions_fact:
        lines.append(f"  Показы: план {_fmt(row.impressions_plan)} → факт {_fmt(row.impressions_fact)}")

    if row.cpm_plan or row.cpm_fact:
        lines.append(f"  CPM: план {_fmt(row.cpm_plan, 'р.')} → факт {_fmt(row.cpm_fact, 'р.')}")

    if row.reach_plan or row.reach_fact:
        lines.append(f"  Охват: план {_fmt(row.reach_plan)} → факт {_fmt(row.reach_fact)}")

    if row.cpv_plan or row.cpv_fact:
        lines.append(f"  CPV: план {_fmt_cpv(row.cpv_plan)} → факт {_fmt_cpv(row.cpv_fact)}")

    if row.clicks_plan or row.clicks_fact:
        clicks_str = f"план {_fmt(row.clicks_plan)}"
        if row.clicks_fact:
            clicks_str += f" → факт {_fmt(row.clicks_fact)} (кабинет)"
        if row.clicks_fact_metrika:
            clicks_str += f" / {_fmt(row.clicks_fact_metrika)} (метрика)"
        lines.append(f"  Переходы: {clicks_str}")

    if row.cpc_fact:
        cpc_str = f"факт {_fmt_cpv(row.cpc_fact)} (кабинет)"
        if row.cpc_fact_metrika:
            cpc_str += f" / {_fmt_cpv(row.cpc_fact_metrika)} (метрика)"
        if row.cpc_plan:
            cpc_str = f"план {_fmt_cpv(row.cpc_plan)} → " + cpc_str
        lines.append(f"  CPC: {cpc_str}")

    if row.bounce_rate is not None:
        lines.append(f"  % отказов: {row.bounce_rate:.2f}%".replace(".", ","))

    if row.time_on_site:
        lines.append(f"  Время на сайте: {row.time_on_site}")

    if row.participants is not None:
        lines.append(f"  Участники/целевые действия: {_fmt(row.participants)}")

    if row.cpa is not None:
        lines.append(f"  CPA: {_fmt_cpv(row.cpa)}")

    return "\n".join(lines)


async def analyze_target_campaign(mp: TargetMediaPlan) -> str:
    """Генерирует клиентский отчёт по таргет-кампании."""

    channel_blocks = []
    for row in mp.channel_rows:
        channel_blocks.append(_row_to_text(row))

    total = mp.total_row
    total_block = ""
    if total:
        total_block = "ИТОГО:\n" + _row_to_text(total)

    user_message = f"""Проект: {mp.project_name}

ДАННЫЕ ПО КАНАЛАМ:
{chr(10).join(channel_blocks)}

{total_block}

Сформируй отчёт строго в трёх разделах. Оформление без линий и символов-разделителей:

ОБЩИЕ РЕЗУЛЬТАТЫ

• пункт 1
• пункт 2
...

РЕЗУЛЬТАТЫ ПО КАНАЛАМ

строка по каждому каналу

ВЫВОДЫ И РЕКОМЕНДАЦИИ

текст"""

    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
        timeout=120,
    )

    return response.choices[0].message.content
