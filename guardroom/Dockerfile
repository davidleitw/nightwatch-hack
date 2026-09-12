FROM ghcr.io/astral-sh/uv:0.12.13 AS uv
FROM python:3.13-slim

WORKDIR /app
COPY --from=uv /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/control/server/.venv/bin:$PATH" \
    GUARDROOM_CONFIG=/app/guardroom/shop-web.config.json \
    NIGHTWATCH_INVESTIGATION_DB=/app/guardroom/.run/investigations.sqlite3

COPY control/pyproject.toml control/README.md /app/control/
COPY control/monitor /app/control/monitor
COPY control/nightwatch_agent /app/control/nightwatch_agent
COPY control/server /app/control/server
RUN uv sync --project /app/control/server --locked --no-dev --no-cache

COPY contracts/schemas /app/contracts/schemas
COPY console/schema-draft /app/console/schema-draft
COPY guardroom/shop-web.config.json /app/guardroom/shop-web.config.json
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin guardroom \
    && mkdir -p /app/guardroom/.run /app/control/tmp \
    && chown 10001:10001 /app/guardroom/.run

USER 10001:10001
EXPOSE 9999
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/control/server", "--host", "0.0.0.0", "--port", "9999", "--workers", "1"]
