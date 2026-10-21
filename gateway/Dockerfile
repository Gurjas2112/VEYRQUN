# syntax=docker/dockerfile:1.7
# Build context: repository root (needs policies/lua).
#   docker build -f gateway/Dockerfile -t veyrqun/gateway .

ARG NODE_IMAGE=node:22-alpine@sha256:ce8d97f7cfc7c5386421eb932df20a29df99b1a7f469f6157d5c9956702ffadc

FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY gateway/package.json gateway/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY gateway/tsconfig.json gateway/tsconfig.build.json ./
COPY gateway/src ./src
RUN npm run build && npm prune --omit=dev --no-audit --no-fund

FROM ${NODE_IMAGE}
ENV NODE_ENV=production LUA_DIR=/app/policies/lua PORT=8080 HOST=0.0.0.0
WORKDIR /app/gateway
COPY --from=build --chown=root:root /src/node_modules ./node_modules
COPY --from=build --chown=root:root /src/dist ./dist
COPY --from=build --chown=root:root /src/package.json ./package.json
COPY --chown=root:root policies/lua /app/policies/lua
USER node
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=2s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["node", "dist/index.js"]
