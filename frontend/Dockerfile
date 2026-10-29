# syntax=docker/dockerfile:1

# ---- build: Node only lives here ------------------------------------------------------
FROM node:24-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Type-checks, then writes the static site to dist/. The API is reached at /api.
RUN npm run build

# ---- runtime: static files behind an unprivileged nginx --------------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime

ENV API_UPSTREAM=api:8000 \
    NGINX_RESOLVER=127.0.0.11
COPY deploy/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 8080
# Healthy only when the API answers through the proxy, not merely when nginx is up.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/api/health/ready || exit 1
