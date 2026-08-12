"""Генерация короткого поста для внешнего рабочего чата из /sumup."""

import logging
import re

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)

MAX_REPORT_CHARS = 50_000

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Ты превращаешь внутренний отчёт о посеве в формальный пост для
внешнего рабочего чата. Тебе переданы ручное описание проекта и внутренний отчёт.

Используй только факты, числа, названия и ссылки из входных данных. Не добавляй
причин, оценок, механик кампании или названий тайтлов от себя. Не используй эмодзи,
метафоры, крючки, разговорные слова и оценочные формулировки.

СТРУКТУРА ОБЯЗАТЕЛЬНА:
1. Первым абзацем поставь ручное описание проекта почти дословно. Исправь только
   очевидные опечатки и лишние пробелы.
2. Отдельным абзацем: плановый paid-охват, фактический paid-охват и перевыполнение
   плана. При 178% выполнения пиши «превысили план на 78%», а не «на 178%».
3. Укажи 1–3 самых сильных paid-результата с названиями, ссылками, фактом и планом.
4. Добавь одну деталь вовлечённости только если в отчёте есть точное сравнение с
   обычными показателями канала. Иначе пропусти этот абзац.
5. Укажи фактический CPV и плановый CPV, если оба есть.
6. В конце добавь «Все вышедшие публикации:» и ВСЕ paid-ссылки из раздела
   «ВСЕ ПУБЛИКАЦИИ». Если есть раздел «ОРГАНИКА», добавь «Органические публикации:»
   со ВСЕМИ organic-ссылками.

Telegram legacy Markdown: ссылки только в виде [Название](ссылка), жирный шрифт —
только *так*. Верни только готовый пост."""


def word_count(text: str) -> int:
    return len([word for word in text.split() if word])


def _report_value(report: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", report, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _report_section(report: str, heading: str) -> list[str]:
    match = re.search(
        rf"(?:^|\n)\s*{re.escape(heading)}\s*\n+(.*?)(?=\n\s*[А-ЯЁ][А-ЯЁ\s-]{{3,}}\s*\n|\Z)",
        report,
        flags=re.DOTALL,
    )
    if not match:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip().startswith("•")]


def _report_section_lines(report: str, heading: str) -> list[str]:
    match = re.search(
        rf"(?:^|\n)\s*{re.escape(heading)}\s*\n+(.*?)(?=\n\s*[А-ЯЁ][А-ЯЁ\s-]{{3,}}\s*\n|\Z)",
        report,
        flags=re.DOTALL,
    )
    if not match:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def _fallback_external_post(report: str, project_description: str) -> str:
    """Собирает фактический пост, когда OpenAI временно недоступен."""
    project = _report_value(report, "Проект") or "проекта"
    paid_plan = _report_value(report, "Плановый paid-охват")
    paid_actual = _report_value(report, "Фактический paid-охват")
    completion = _report_value(report, "Выполнение paid-плана")
    actual_cpv = _report_value(report, "Фактический CPV с учётом органики")
    planned_cpv = _report_value(report, "Плановый CPV")

    lines = [project_description.strip() or f"Результаты посева по {project}."]
    if paid_plan and paid_actual:
        lines.extend(["", f"Вместо {paid_plan} просмотров получили {paid_actual}."])
    if completion:
        match = re.search(r"(-?\d+(?:[,.]\d+)?)%", completion)
        if match:
            overperformance = float(match.group(1).replace(",", ".")) - 100
            if overperformance >= 0:
                lines.append(f"Превысили план на {overperformance:.0f}%.")
            else:
                lines.append(f"Выполнение плана составило {completion}.")

    strong_results = [
        line for line in _report_section_lines(report, "СВЕРХРЕЗУЛЬТАТЫ")
        if "при плане" in line.casefold()
    ][:3]
    if strong_results:
        first_name, separator, first_result = strong_results[0].partition(" — ")
        if separator:
            lines.extend(["", f"Больше всего просмотров принесла публикация у {first_name}: {first_result}."])
        if len(strong_results) > 1:
            lines.extend(["", "Также выше плана отработали:"])
            lines.extend(f"• {line}" for line in strong_results[1:])

    if actual_cpv:
        cpv_line = f"Фактический CPV: {actual_cpv}"
        if planned_cpv:
            cpv_line += f" при плановом {planned_cpv}"
        lines.extend(["", cpv_line + "."])

    paid_links = _report_section(report, "ВСЕ ПУБЛИКАЦИИ")
    organic_links = _report_section(report, "ОРГАНИКА")
    if paid_links:
        lines.extend(["", "Все вышедшие публикации:", *paid_links])
    if organic_links:
        lines.extend(["", "Органические публикации:", *organic_links])
    return "\n".join(lines)


async def generate_external_post(internal_report: str, project_description: str = "") -> str:
    """Возвращает Telegram Markdown-пост из полного внутреннего отчёта."""
    internal_report = (internal_report or "").strip()
    if not internal_report:
        raise ValueError("Внутренний отчёт пуст")
    if len(internal_report) > MAX_REPORT_CHARS:
        raise ValueError(
            f"Отчёт слишком длинный: {len(internal_report):,} символов. "
            f"Лимит — {MAX_REPORT_CHARS:,}."
        )

    logger.info("Generating external post: input_chars=%s model=%s", len(internal_report), OPENAI_MODEL)
    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"РУЧНОЕ ОПИСАНИЕ ПРОЕКТА:\n{project_description.strip()}\n\n"
                    f"ВНУТРЕННИЙ ОТЧЁТ:\n{internal_report}"
                ),
            },
            ],
            temperature=0.55,
            timeout=90,
        )
    except Exception as exc:
        logger.warning("External post generation failed, using deterministic fallback: %s", exc)
        return _fallback_external_post(internal_report, project_description)
    result = (response.choices[0].message.content or "").strip()
    if not result:
        raise ValueError("OpenAI вернул пустой пост")

    logger.info("External post generated: words=%s", word_count(result))
    return result
