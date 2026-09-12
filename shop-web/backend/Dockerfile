FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.12-slim
WORKDIR /app

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/shop.db \
    LOG_DIR=/logs \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH" \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

COPY app ./app
COPY tests ./tests

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /logs \
    && chown -R app:app /app /data /logs

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
