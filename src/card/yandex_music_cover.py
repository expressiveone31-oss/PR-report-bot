"""Рендер брендированной обложки Яндекс Музыки в PNG."""

import base64
import html
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


W = 1080
H = 1080
MARGIN = 72
ASSET_DIR = Path(__file__).parent / "assets" / "yandex_music"
BACKGROUNDS = {
    "blue": "bg-blue.png",
    "pink": "bg-pink.png",
    "green": "bg-green.png",
}
YELLOW = "#FFFF00"
# YS-шрифты ставятся из assets в Docker; DejaVu — системный fallback.
FONT_DISPLAY = "YS Music Poster, DejaVu Sans, sans-serif"
FONT_SUB = "YS Text, DejaVu Sans, sans-serif"


@dataclass
class YandexMusicMetric:
    name: str
    value: str
    detail: str = ""


@dataclass
class YandexMusicCoverData:
    title_lines: list[str] = field(default_factory=list)
    subtitle: str = ""
    background: str = "blue"
    align: str = "center"
    hero: str = ""
    hero_label: str = ""
    metrics: list[YandexMusicMetric] = field(default_factory=list)
    highlight: str = ""


def _escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def _fit_title(lines: list[str]) -> int:
    max_chars = max(1, *(len(line) for line in lines))
    size = 176
    while size > 90 and max_chars * size * 0.56 > W - 2 * MARGIN:
        size -= 4
    return size


def _background_data_uri(name: str) -> str:
    filename = BACKGROUNDS.get(name, BACKGROUNDS["blue"])
    path = ASSET_DIR / "backgrounds" / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Music cover background not found: {path}. "
            "Добавь ассеты в src/card/assets/yandex_music/backgrounds/."
        )
    payload = path.read_bytes()
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def build_music_cover_svg(data: YandexMusicCoverData) -> str:
    lines = [line.strip() for line in data.title_lines if line.strip()][:3]
    if not lines:
        lines = ["Яндекс Музыка"]
    align = "left" if data.align == "left" else "center"
    size = min(_fit_title(lines), 76) if data.hero else min(_fit_title(lines), 108)
    line_height = round(size * 0.92)
    block_height = (len(lines) - 1) * line_height
    anchor = "start" if align == "left" else "middle"
    x = MARGIN if align == "left" else W // 2
    first_y = round((H * 0.18 if data.hero else H * 0.46) - block_height / 2 + size * 0.34)
    title_svg = "".join(
        f'<text x="{x}" y="{first_y + index * line_height}" text-anchor="{anchor}" '
        f'font-family="{FONT_DISPLAY}" font-weight="900" font-size="{size}" '
        f'fill="{YELLOW}" letter-spacing="-1">{_escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    subtitle_svg = ""
    if data.subtitle.strip():
        if align == "center":
            subtitle_svg = (
                f'<text x="{W // 2}" y="{285 if data.hero else H - 60}" text-anchor="middle" '
                f'font-family="{FONT_SUB}" font-weight="700" font-size="28" fill="{YELLOW}">'
                f'{_escape(data.subtitle.strip())}</text>'
            )
        else:
            subtitle_svg = (
                f'<text x="{MARGIN}" y="{first_y + block_height + 50}" '
                f'font-family="{FONT_SUB}" font-weight="700" font-size="28" fill="{YELLOW}">'
                f'{_escape(data.subtitle.strip())}</text>'
            )

    hero_svg = ""
    metrics_svg = ""
    highlight_svg = ""
    if data.hero:
        hero_x = MARGIN if align == "left" else W // 2
        hero_anchor = "start" if align == "left" else "middle"
        hero_svg = (
            f'<text x="{hero_x}" y="455" text-anchor="{hero_anchor}" font-family="{FONT_DISPLAY}" '
            f'font-weight="900" font-size="132" fill="{YELLOW}" letter-spacing="-2">{_escape(data.hero)}</text>'
            f'<text x="{hero_x}" y="500" text-anchor="{hero_anchor}" font-family="{FONT_SUB}" '
            f'font-weight="700" font-size="28" fill="#FFFFFF">{_escape(data.hero_label or "просмотров")}</text>'
        )
        metrics = data.metrics[:3]
        metric_parts = []
        metric_width = (W - 2 * MARGIN) / len(metrics) if metrics else 0
        for index, metric in enumerate(metrics):
            mx = MARGIN + index * metric_width
            value_size = 42 if len(metric.value) > 10 else 50
            detail = metric.detail[:32]
            metric_parts.append(
                f'<line x1="{mx}" y1="590" x2="{mx + metric_width - 22}" y2="590" '
                f'stroke="{YELLOW}" stroke-opacity="0.55" stroke-width="2"/>'
                f'<text x="{mx}" y="630" font-family="{FONT_SUB}" font-weight="700" font-size="20" '
                f'fill="{YELLOW}" letter-spacing="1">{_escape(metric.name)}</text>'
                f'<text x="{mx}" y="690" font-family="{FONT_SUB}" font-weight="700" font-size="{value_size}" '
                f'fill="#FFFFFF">{_escape(metric.value)}</text>'
                f'<text x="{mx}" y="725" font-family="{FONT_SUB}" font-weight="500" font-size="18" '
                f'fill="{YELLOW}">{_escape(detail)}</text>'
            )
        metrics_svg = "".join(metric_parts)
        if data.highlight:
            highlight_svg = (
                f'<line x1="{MARGIN}" y1="830" x2="{W - MARGIN}" y2="830" '
                f'stroke="{YELLOW}" stroke-opacity="0.55" stroke-width="2"/>'
                f'<text x="{MARGIN}" y="865" font-family="{FONT_SUB}" font-weight="700" font-size="21" '
                f'fill="{YELLOW}">ХАЙЛАЙТ ПОСЕВА</text>'
                f'<text x="{MARGIN}" y="915" font-family="{FONT_SUB}" font-weight="500" font-size="26" '
                f'fill="#FFFFFF">{_escape(data.highlight)}</text>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<image href="{_background_data_uri(data.background)}" width="{W}" height="{H}" '
        f'preserveAspectRatio="xMaxYMid slice"/>'
        f'<rect width="{W}" height="{H}" fill="#110503" fill-opacity="0.38"/>'
        f'{title_svg}{subtitle_svg}{hero_svg}{metrics_svg}{highlight_svg}</svg>'
    )


def render_music_cover(data: YandexMusicCoverData, scale: float = 1.0) -> bytes:
    """Рендерит квадратную PNG-инфографику 1080×1080."""
    if sys.platform == "darwin" and Path("/opt/homebrew/lib").is_dir():
        fallback_paths = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":")
        if "/opt/homebrew/lib" not in fallback_paths:
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
                path for path in ["/opt/homebrew/lib", *fallback_paths] if path
            )
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError(f"cairosvg / cairo library недоступны: {exc}") from exc

    return cairosvg.svg2png(
        bytestring=build_music_cover_svg(data).encode("utf-8"),
        output_width=int(W * scale),
    )
