FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
COPY examples ./examples
ARG AOG_RUNTIME_EXTRA=""
RUN case "$AOG_RUNTIME_EXTRA" in ""|openshell) ;; *) echo "Core supports only the optional openshell SDK" >&2; exit 2 ;; esac \
    && uv sync --frozen --no-dev ${AOG_RUNTIME_EXTRA:+--extra "$AOG_RUNTIME_EXTRA"} \
    && useradd --uid 10001 --create-home gateway \
    && mkdir /app/data && chown gateway:gateway /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER gateway
EXPOSE 4455
CMD ["aog", "serve", "--host", "0.0.0.0", "--port", "4455"]
