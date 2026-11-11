FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends patch ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scripts/prepare-contextforge.py ./scripts/prepare-contextforge.py
COPY integrations/contextforge ./integrations/contextforge
RUN python scripts/prepare-contextforge.py
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra contextforge \
    && useradd --uid 10001 --create-home gateway \
    && mkdir /app/data && chown gateway:gateway /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER gateway
EXPOSE 4444
CMD ["uvicorn", "aog.integrations.contextforge.app:app", "--host", "0.0.0.0", "--port", "4444", "--workers", "1"]
