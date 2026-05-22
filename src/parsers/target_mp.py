"""
Парсер таргет/перфоманс МП.

Формат: двухстрочный заголовок, колонки разбиты на прогноз/факт.
Метрики: CPM, CPV, охват, показы, переходы, CPC, % отказа, время на сайте.
"""

import csv
import io
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TargetRow:
    channel: str                    # название канала/платформы
    target: str                     # описание таргета/аудитории

    cost_plan: Optional[float] = None
    cost_fact: Optional[float] = None

    impressions_plan: Optional[float] = None
    impressions_fact: Optional[float] = None

    cpm_plan: Optional[float] = None
    cpm_fact: Optional[float] = None

    reach_plan: Optional[float] = None
    reach_fact: Optional[float] = None

    cpv_plan: Optional[float] = None
    cpv_fact: Optional[float] = None

    clicks_plan: Optional[float] = None
    clicks_fact: Optional[float] = None      # по кабинету
    clicks_fact_metrika: Optional[float] = None  # по метрике

    cpc_plan: Optional[float] = None
    cpc_fact: Optional[float] = None         # по кабинету
    cpc_fact_metrika: Optional[float] = None

    bounce_rate: Optional[float] = None      # % отказа (число, не строка)
    time_on_site: Optional[str] = None       # "1:20", "0:07" и т.д.

    participants: Optional[float] = None     # участники / целевые действия
    cpa: Optional[float] = None             # стоимость участника/действия

    is_total: bool = False


@dataclass
class TargetMediaPlan:
    project_name: str = ""
    rows: list[TargetRow] = field(default_factory=list)

    @property
    def total_row(self) -> Optional[TargetRow]:
        for r in self.rows:
            if r.is_total:
                return r
        return None

    @property
    def channel_rows(self) -> list[TargetRow]:
        return [r for r in self.rows if not r.is_total]


def _parse_num(value: str) -> Optional[float]:
    """Парсит число из ячейки: убирает р., ₽, %, пробелы, запятые как разделитель тысяч."""
    if not value:
        return None
    # Убираем символы валюты и процентов
    cleaned = re.sub(r"[р\.₽%\s\xa0]", "", value.strip())
    if not cleaned:
        return None
    # Запятая как десятичный разделитель
    if "," in cleaned and "." not in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_bounce(value: str) -> Optional[float]:
    """Парсит % отказа: '61,00%' → 61.0"""
    if not value:
        return None
    cleaned = re.sub(r"[%\s\xa0]", "", value.strip()).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_target_mp(content: str, project_name: str = "") -> TargetMediaPlan:
    """
    Парсит таргет/перфоманс МП с двухстрочным заголовком.
    Автоматически находит колонки по ключевым словам.
    """
    mp = TargetMediaPlan(project_name=project_name)
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if len(rows) < 3:
        return mp

    # Находим строку-заголовок: ищем строку с "Стоимость" или "Показы" или "CPM"
    header_row_idx = None
    for i, row in enumerate(rows[:5]):
        row_text = " ".join(row).lower()
        if any(kw in row_text for kw in ("стоимость", "показы", "cpm", "охват")):
            header_row_idx = i
            break

    if header_row_idx is None:
        logger.warning("target_mp: не нашли строку заголовка")
        return mp

    header1 = rows[header_row_idx]      # группы колонок
    header2 = rows[header_row_idx + 1]  # прогноз/факт подзаголовки

    # Определяем индекс колонки таргета — последняя непустая колонка перед "Стоимость"
    # В МП с 2 инфо-колонками (Каналы, Таргеты) — COL_TARGET=1
    # В МП с 3 инфо-колонками (Каналы, Оптимизации, Таргеты) — COL_TARGET=2
    COL_CHANNEL = 0
    COL_TARGET = 1
    for i, cell in enumerate(header1[1:], 1):
        c = cell.strip().lower()
        if any(kw in c for kw in ("стоимость", "показы", "cpm", "охват", "переход")):
            break
        if c:
            COL_TARGET = i

    # Строим маппинг колонок по заголовкам
    # Идём по header1, запоминаем текущую группу
    col_map = {}
    current_group = ""
    for i, cell in enumerate(header1):
        cell = cell.strip().lower()
        if cell:
            current_group = cell
        sub = header2[i].strip().lower() if i < len(header2) else ""

        # Маппинг группа + подзаголовок → поле
        key = f"{current_group}|{sub}"
        col_map[i] = key

    # Теперь находим индексы нужных полей
    def find_col(group_kw: str, sub_kw: str) -> Optional[int]:
        for idx, key in col_map.items():
            g, s = key.split("|", 1)
            if group_kw in g and sub_kw in s:
                return idx
        return None

    def find_cols(group_kw: str) -> list[int]:
        return [idx for idx, key in col_map.items() if group_kw in key.split("|")[0]]

    col_cost_plan  = find_col("стоимость", "прогноз")
    col_cost_fact  = find_col("стоимость", "факт")

    col_imp_plan   = find_col("показы", "прогноз")
    col_imp_fact   = find_col("показы", "факт")

    col_cpm_plan   = find_col("cpm", "прогноз")
    col_cpm_fact   = find_col("cpm", "факт")

    col_reach_plan = find_col("охват", "прогноз")
    col_reach_fact = find_col("охват", "факт")

    col_cpv_plan   = find_col("cpv", "прогноз")
    col_cpv_fact   = find_col("cpv", "факт")

    # Переходы: прогноз + два факта (кабинет и метрика)
    clicks_cols = find_cols("переходы")
    col_clicks_plan          = clicks_cols[0] if len(clicks_cols) > 0 else None
    col_clicks_fact          = clicks_cols[1] if len(clicks_cols) > 1 else None
    col_clicks_fact_metrika  = clicks_cols[2] if len(clicks_cols) > 2 else None

    # CPC: прогноз + два факта
    cpc_cols = find_cols("сpc") or find_cols("cpc")
    col_cpc_plan             = cpc_cols[0] if len(cpc_cols) > 0 else None
    col_cpc_fact             = cpc_cols[1] if len(cpc_cols) > 1 else None
    col_cpc_fact_metrika     = cpc_cols[2] if len(cpc_cols) > 2 else None

    # % отказа, время на сайте, участники, CPA — только факт, ищем по названию
    col_bounce       = None
    col_time         = None
    col_participants = None
    col_cpa          = None

    for header in (header1, header2):
        for i, cell in enumerate(header):
            c = cell.strip().lower()
            if "отказ" in c and col_bounce is None:
                col_bounce = i
            if "время" in c and col_time is None:
                col_time = i
            if ("участник" in c or "kpi" in c) and col_participants is None:
                col_participants = i
            if c == "cpa" and col_cpa is None:
                col_cpa = i

    logger.info(
        f"target_mp columns: cost={col_cost_plan}/{col_cost_fact}, "
        f"reach={col_reach_plan}/{col_reach_fact}, "
        f"clicks={col_clicks_plan}/{col_clicks_fact}/{col_clicks_fact_metrika}, "
        f"bounce={col_bounce}, time={col_time}, "
        f"participants={col_participants}, cpa={col_cpa}"
    )

    def get(row: list[str], idx: Optional[int]) -> str:
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    # Парсим строки данных
    current_channel = ""
    for row in rows[header_row_idx + 2:]:
        if not row or not any(c.strip() for c in row):
            continue

        # Название канала — если первая ячейка непустая
        first = row[0].strip()
        if first and not first.lower().startswith("итого"):
            current_channel = first

        target = get(row, COL_TARGET)

        # Пропускаем строки без фактических данных — прогнозные МП не обрабатываем
        cost_fact_raw = get(row, col_cost_fact)
        reach_fact_raw = get(row, col_reach_fact)
        if not cost_fact_raw and not reach_fact_raw:
            continue

        is_total = first.lower().startswith("итого") or (
            not first and target.lower().startswith("итого")
        )

        bounce_raw = get(row, col_bounce)
        bounce = _parse_bounce(bounce_raw)

        mp.rows.append(TargetRow(
            channel=current_channel,
            target=target,
            cost_plan=_parse_num(get(row, col_cost_plan)),
            cost_fact=_parse_num(get(row, col_cost_fact)),
            impressions_plan=_parse_num(get(row, col_imp_plan)),
            impressions_fact=_parse_num(get(row, col_imp_fact)),
            cpm_plan=_parse_num(get(row, col_cpm_plan)),
            cpm_fact=_parse_num(get(row, col_cpm_fact)),
            reach_plan=_parse_num(get(row, col_reach_plan)),
            reach_fact=_parse_num(get(row, col_reach_fact)),
            cpv_plan=_parse_num(get(row, col_cpv_plan)),
            cpv_fact=_parse_num(get(row, col_cpv_fact)),
            clicks_plan=_parse_num(get(row, col_clicks_plan)),
            clicks_fact=_parse_num(get(row, col_clicks_fact)),
            clicks_fact_metrika=_parse_num(get(row, col_clicks_fact_metrika)),
            cpc_plan=_parse_num(get(row, col_cpc_plan)),
            cpc_fact=_parse_num(get(row, col_cpc_fact)),
            cpc_fact_metrika=_parse_num(get(row, col_cpc_fact_metrika)),
            bounce_rate=bounce,
            time_on_site=get(row, col_time) or None,
            participants=_parse_num(get(row, col_participants)),
            cpa=_parse_num(get(row, col_cpa)),
            is_total=is_total,
        ))

    logger.info(
        f"target_mp parse done: {len(mp.channel_rows)} channels, "
        f"total_row={'yes' if mp.total_row else 'no'}"
    )
    return mp
