FROM node:24.22.2-bookworm-slim AS adapters
WORKDIR /adapters
COPY workers/acp/package*.json ./
RUN npm ci --ignore-scripts --omit=dev

FROM agentport-core:0.2.0
USER root
COPY --from=adapters /usr/local/bin/node /usr/local/bin/node
COPY --from=adapters /adapters/node_modules /opt/acp/node_modules
ENV PATH="/opt/acp/node_modules/.bin:$PATH"
USER gateway
EXPOSE 8090
CMD ["uvicorn", "aog.worker:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
