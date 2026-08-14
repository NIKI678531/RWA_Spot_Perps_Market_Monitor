FROM python:3.12-slim

ARG BACKEND_ENV_FILE=backend/.env.example

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# CJK fonts for the bilingual xlsx/docx report output. Reports are generated with
# openpyxl/python-docx, which need no native rendering libraries — only the fonts
# so 中文 column headers and narrative text resolve when the file is opened.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend /app/backend
COPY ${BACKEND_ENV_FILE} /app/backend/.env

RUN chmod +x /app/backend/scripts/start.sh \
    && adduser -u 5678 --disabled-password --gecos "" appuser \
    && chown -R appuser /app

WORKDIR /app/backend
EXPOSE 8000

USER appuser

CMD ["/app/backend/scripts/start.sh"]
