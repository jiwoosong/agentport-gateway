FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY examples ./examples
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home gateway \
    && mkdir /app/data && chown gateway:gateway /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER gateway
EXPOSE 4444
CMD ["aog", "serve", "--host", "0.0.0.0"]
