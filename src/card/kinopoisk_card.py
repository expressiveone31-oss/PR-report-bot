"""
Kinopoisk report-card generator (Python port).

Рендерит брендированную карточку-результат (чёрный фон, оранжево-лаймовая
«вспышка», editorial-разбивка по площадкам) в PNG bytes. Готово к отправке
из aiogram через BufferedInputFile / answer_photo.

Порт с Node.js версии kinopoiskCard.js. SVG-шаблон, координаты, градиенты
и всё поведение — 1-в-1 с оригиналом.

Требования:
- cairosvg (Python)
- libcairo в системе: brew install cairo (macOS) / apt install libcairo2 (linux)
- Шрифт с кириллицей должен быть установлен в системе.
  По умолчанию используем sans-serif fallback (в macOS/Linux берётся системный).
"""

import io
import logging
from typing import Optional, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Brand tokens (Kinopoisk brandbook) ------------------------------------
TOKENS = {
    "BLACK":  "#000000",
    "ORANGE": "#FF5500",   # основной акцент — только на главном
    "LIME":   "#BBFF00",   # «оживляет» градиент, вторичный
    "WHITE":  "#FFFFFF",
    "GREY":   "#9A9A9A",
    "GREY2":  "#6E6E6E",
    "LINE":   "#242424",
    "FONT":   "Kinopoisk Sans, Inter, Helvetica Neue, Arial, sans-serif",
}

# --- Fixed layout constants (same as JS version) ---------------------------
W = 1200
PAD = 100
RIGHT = W - PAD          # 1100
ROW_TOP = 812            # y where the breakdown rows start
ROW_H = 138              # height of one breakdown row
BOTTOM = 136             # space reserved below the last row (footer)


@dataclass
class CardRow:
    """Одна строка разбивки по площадкам."""
    name: str
    reach: str                        # число справа, форматированное как строка
    tag: Optional[str] = None         # серая подпись под названием
    highlight: bool = False           # оранжевая засечка + оранжевое число


@dataclass
class CardData:
    """Данные для одной карточки-отчёта."""
    kicker: str = ""                             # надзаголовок (АНИМЕ НА КИНОПОИСКЕ)
    title_lines: list[str] = field(default_factory=list)   # 1-2 строки заголовка
    hero: str = ""                                # большое число
    subtitle: str = ""                            # серая строка под числом
    rows: list[CardRow] = field(default_factory=list)
    footer: str = ""
    breakdown_label: str = "РАЗБИВКА ПО ПЛОЩАДКАМ"
    reach_label: str = "ОХВАТ"


# --- Helpers ---------------------------------------------------------------


def _esc(s: Union[str, int, float, None]) -> str:
    """XML-эскейпинг для текста внутри <text>."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _hero_size(text: str) -> int:
    """Автоматический масштаб hero-числа под длину строки."""
    n = len(str(text or ""))
    if n <= 7:
        return 176
    if n <= 10:
        return 140
    return 112


def _as_card_data(data: Union[CardData, dict]) -> CardData:
    """Позволяем принимать и dataclass, и обычный dict."""
    if isinstance(data, CardData):
        return data
    if not isinstance(data, dict):
        raise TypeError(f"Expected CardData or dict, got {type(data).__name__}")

    rows_raw = data.get("rows", [])
    rows: list[CardRow] = []
    for r in rows_raw:
        if isinstance(r, CardRow):
            rows.append(r)
        else:
            rows.append(CardRow(
                name=str(r.get("name", "")),
                reach=str(r.get("reach", "")),
                tag=r.get("tag"),
                highlight=bool(r.get("highlight", False)),
            ))

    return CardData(
        kicker=str(data.get("kicker", "")),
        title_lines=list(data.get("title_lines") or data.get("titleLines") or []),
        hero=str(data.get("hero", "")),
        subtitle=str(data.get("subtitle", "")),
        rows=rows,
        footer=str(data.get("footer", "")),
        breakdown_label=str(
            data.get("breakdown_label") or data.get("breakdownLabel")
            or "РАЗБИВКА ПО ПЛОЩАДКАМ"
        ),
        reach_label=str(data.get("reach_label") or data.get("reachLabel") or "ОХВАТ"),
    )


# --- SVG generation --------------------------------------------------------


def build_svg(data: Union[CardData, dict]) -> str:
    """
    Собирает SVG-строку карточки. Порт buildSVG() из JS.
    """
    d = _as_card_data(data)
    T = TOKENS
    rows = d.rows or []
    H = ROW_TOP + len(rows) * ROW_H + BOTTOM
    title_lines = (d.title_lines or [])[:2]
    font = T["FONT"]

    # Title: 1 строка сидит ниже, 2 строки — стопкой
    title_svg = ""
    if len(title_lines) == 1:
        title_svg = (
            f'<text x="{PAD}" y="306" font-family="{font}" font-size="66" '
            f'font-weight="700" fill="{T["WHITE"]}">{_esc(title_lines[0])}</text>'
        )
    elif len(title_lines) == 2:
        title_svg = (
            f'<text x="{PAD}" y="256" font-family="{font}" font-size="66" '
            f'font-weight="700" fill="{T["WHITE"]}">{_esc(title_lines[0])}</text>'
            f'<text x="{PAD}" y="330" font-family="{font}" font-size="66" '
            f'font-weight="700" fill="{T["WHITE"]}">{_esc(title_lines[1])}</text>'
        )

    # Breakdown rows
    rows_parts: list[str] = []
    for i, r in enumerate(rows):
        y = ROW_TOP + i * ROW_H
        cy = y + ROW_H // 2
        # верхняя линия
        rows_parts.append(
            f'<line x1="{PAD}" y1="{y}" x2="{RIGHT}" y2="{y}" '
            f'stroke="{T["LINE"]}" stroke-width="1.5"/>'
        )
        # оранжевая засечка для highlight
        if r.highlight:
            rows_parts.append(
                f'<rect x="{PAD - 30}" y="{y + 26}" width="8" '
                f'height="{ROW_H - 52}" rx="4" fill="{T["ORANGE"]}"/>'
            )
        # название площадки
        name_weight = 700 if r.highlight else 500
        rows_parts.append(
            f'<text x="{PAD}" y="{cy - 6}" font-family="{font}" font-size="42" '
            f'font-weight="{name_weight}" fill="{T["WHITE"]}">{_esc(r.name)}</text>'
        )
        # тэг (кол-во публикаций)
        if r.tag:
            rows_parts.append(
                f'<text x="{PAD}" y="{cy + 40}" font-family="{font}" font-size="26" '
                f'font-weight="400" fill="{T["GREY"]}">{_esc(r.tag)}</text>'
            )
        # число справа
        reach_color = T["ORANGE"] if r.highlight else T["WHITE"]
        rows_parts.append(
            f'<text x="{RIGHT}" y="{cy + 14}" text-anchor="end" font-family="{font}" '
            f'font-size="52" font-weight="700" fill="{reach_color}">{_esc(r.reach)}</text>'
        )

    # нижняя линия после последней строки
    rows_parts.append(
        f'<line x1="{PAD}" y1="{ROW_TOP + len(rows) * ROW_H}" x2="{RIGHT}" '
        f'y2="{ROW_TOP + len(rows) * ROW_H}" stroke="{T["LINE"]}" stroke-width="1.5"/>'
    )
    rows_svg = "\n    ".join(rows_parts)

    hs = _hero_size(d.hero)

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <radialGradient id="flare" cx="86%" cy="95%" r="82%">
      <stop offset="0%"  stop-color="{T["ORANGE"]}" stop-opacity="0.68"/>
      <stop offset="26%" stop-color="#FF6A1A" stop-opacity="0.46"/>
      <stop offset="48%" stop-color="#DBB200" stop-opacity="0.34"/>
      <stop offset="66%" stop-color="{T["LIME"]}" stop-opacity="0.30"/>
      <stop offset="100%" stop-color="{T["LIME"]}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="limeGlow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{T["LIME"]}" stop-opacity="0.32"/>
      <stop offset="100%" stop-color="{T["LIME"]}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="haze" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{T["ORANGE"]}" stop-opacity="0.20"/>
      <stop offset="100%" stop-color="{T["ORANGE"]}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{T["ORANGE"]}"/>
      <stop offset="100%" stop-color="{T["LIME"]}"/>
    </linearGradient>
    <linearGradient id="streak" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{T["LIME"]}" stop-opacity="0"/>
      <stop offset="50%" stop-color="{T["LIME"]}" stop-opacity="0.14"/>
      <stop offset="100%" stop-color="{T["ORANGE"]}" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <rect x="0" y="0" width="{W}" height="{H}" fill="{T["BLACK"]}"/>

  <!-- cinematic flare: light out of darkness (orange -> lime) -->
  <ellipse cx="{W}" cy="{H}" rx="820" ry="680" fill="url(#flare)"/>
  <ellipse cx="1030" cy="{H - 370}" rx="360" ry="300" fill="url(#limeGlow)"/>
  <g transform="rotate(-28 700 760)"><ellipse cx="720" cy="770" rx="860" ry="66" fill="url(#streak)"/></g>
  <ellipse cx="330" cy="540" rx="430" ry="230" fill="url(#haze)"/>

  <!-- kicker -->
  <rect x="{PAD}" y="132" width="14" height="14" rx="3" fill="{T["ORANGE"]}"/>
  <text x="{PAD + 30}" y="146" font-family="{font}" font-size="26" font-weight="700" letter-spacing="3" fill="{T["ORANGE"]}">{_esc(d.kicker)}</text>

  {title_svg}

  <!-- hero total -->
  <text x="{PAD - 4}" y="560" font-family="{font}" font-size="{hs}" font-weight="700" fill="{T["ORANGE"]}" letter-spacing="-2">{_esc(d.hero)}</text>
  <text x="{PAD}" y="632" font-family="{font}" font-size="36" font-weight="400" fill="{T["GREY"]}">{_esc(d.subtitle)}</text>
  <rect x="{PAD}" y="678" width="480" height="8" rx="4" fill="url(#rule)"/>

  <!-- breakdown labels -->
  <text x="{PAD}" y="778" font-family="{font}" font-size="24" font-weight="700" letter-spacing="2" fill="{T["GREY2"]}">{_esc(d.breakdown_label)}</text>
  <text x="{RIGHT}" y="778" text-anchor="end" font-family="{font}" font-size="24" font-weight="700" letter-spacing="2" fill="{T["GREY2"]}">{_esc(d.reach_label)}</text>

  {rows_svg}

  <!-- footer -->
  <text x="{PAD}" y="{H - 48}" font-family="{font}" font-size="24" font-weight="400" fill="{T["GREY2"]}">{_esc(d.footer)}</text>
</svg>'''
    return svg


def render_card(
    data: Union[CardData, dict],
    scale: float = 1.0,
) -> bytes:
    """
    Рендерит карточку в PNG bytes.

    Args:
        data: CardData или dict с теми же полями
        scale: масштаб рендера. 1.0 = 1200px ширина, 2.0 = 2400px и т.д.

    Returns:
        bytes: PNG-байты, готовые к отправке через aiogram BufferedInputFile.

    Raises:
        RuntimeError: если cairosvg / cairo недоступны в системе.
    """
    try:
        import cairosvg  # ленивый импорт — чтобы не падать при import module
    except (ImportError, OSError) as e:
        raise RuntimeError(
            f"cairosvg / cairo library недоступны: {e}. "
            "Установи: pip install cairosvg + brew install cairo (или apt install libcairo2)."
        ) from e

    svg = build_svg(data)
    output_width = int(W * scale)

    png_bytes = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=output_width,
    )
    return png_bytes
