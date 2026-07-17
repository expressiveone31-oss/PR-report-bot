# Dockerfile для деплоя pr-report-bot на Railway.
#
# Задача: гарантированно поставить системные библиотеки, которые нужны
# cairosvg (для генерации PNG-карточек), и не полагаться на автодетект
# nixpacks/railpack, потому что с ним оказалось нестабильно.
#
# Python + все системные либы в одном образе, без multi-stage,
# чтобы не таскать между стадиями .env / session-файлы.

FROM python:3.11-slim

# Работаем в /app как в стандартном Railway-контейнере.
WORKDIR /app

# --- Системные зависимости ------------------------------------------------
# Что зачем:
#   - libcairo2 / libcairo2-dev: сама cairo, её ищет cairocffi (libcairo.so.2)
#   - libpango-1.0-0 / libpangocairo-1.0-0: рендер текста внутри cairo
#   - libgdk-pixbuf-2.0-0: растровые изображения (транзитивная зависимость)
#   - libffi-dev + pkg-config: обвязка для cairocffi при установке
#   - fonts-dejavu-core: шрифт с полной поддержкой кириллицы (иначе квадраты)
#   - fontconfig: чтобы cairo нашёл шрифты в системе
#   - gcc + python3-dev: TgCrypto собирается из C-исходников при pip install
#   - build-essential: базовый набор для сборки нативных python-пакетов
#
# --no-install-recommends чтобы не тащить лишнее и не раздувать образ.
# rm -rf /var/lib/apt/lists/* в конце — экономим место в слое.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        pkg-config \
        fonts-dejavu-core \
        fontconfig \
        gcc \
        python3-dev \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# --- Python зависимости ---------------------------------------------------
# Ставим отдельным слоем — Docker кеширует, пересобирается только при
# изменении requirements.txt (быстрые ре-деплои при правках кода).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- Код проекта ----------------------------------------------------------
COPY . .

# Обновляем шрифтовый кеш чтобы DejaVu стал виден cairo/pango.
RUN fc-cache -f

# --- Запуск ---------------------------------------------------------------
# Bot читает переменные окружения из .env локально и из Railway variables
# в проде. Ничего специального в CMD не нужно.
CMD ["python", "main.py"]
