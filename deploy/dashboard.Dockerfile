FROM node:24.22.2-bookworm-slim AS build
WORKDIR /ui
COPY dashboard/package*.json ./
RUN npm ci --ignore-scripts
COPY dashboard/ ./
RUN npm run build
WORKDIR /ui/server
RUN npm ci --ignore-scripts && npm run build && npm prune --omit=dev --ignore-scripts

FROM node:24.22.2-bookworm-slim
WORKDIR /app
COPY --from=build /ui/server/package*.json ./
COPY --from=build /ui/server/node_modules ./node_modules
COPY --from=build /ui/server/dist ./dist
COPY --from=build /ui/server/public ./public
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
USER node
EXPOSE 3000
CMD ["node", "dist/index.js"]
