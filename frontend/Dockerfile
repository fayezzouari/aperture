# The web container is also the edge: nginx serves the built UI and proxies the
# API, so the browser only ever talks to one origin.
FROM node:22-alpine AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# The Claude Desktop extension, offered for download on the tokens page. Built
# here so the file a person downloads always matches the server this release runs.
FROM alpine:3.20 AS mcpb

RUN apk add --no-cache zip
WORKDIR /src/mcp
COPY mcp/pyproject.toml mcp/uv.lock mcp/README.md ./
COPY mcp/aperture_mcp ./aperture_mcp
COPY mcp/bundle ./bundle
RUN sh bundle/build.sh /out


FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=mcpb /out/aperture.mcpb /usr/share/nginx/html/downloads/aperture.mcpb
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

# 127.0.0.1, not localhost: busybox wget tries ::1 first and nginx listens on
# IPv4 only, which makes a working container report itself unhealthy.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
