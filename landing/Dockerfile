# Static landing page: Vite build served by nginx. The build context is the
# repo root because the screenshots the page embeds live in docs/screenshots
# (shared with the README), which Vite picks up through publicDir.
FROM node:22-alpine AS build

WORKDIR /app
COPY landing/package.json landing/package-lock.json* ./landing/
RUN cd landing && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
COPY landing/ ./landing/
COPY docs/screenshots/ ./docs/screenshots/
RUN cd landing && npm run build


FROM nginx:1.27-alpine

COPY --from=build /app/landing/dist /usr/share/nginx/html
COPY landing/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8081

# 127.0.0.1, not localhost: busybox wget tries ::1 first and nginx listens on
# IPv4 only, which makes a working container report itself unhealthy.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8081/healthz >/dev/null 2>&1 || exit 1
