# The shared MCP server: Aperture's tools over streamable HTTP, behind the
# console's edge at /mcp. It holds no credential of its own - every request
# brings its caller's token - so it needs nothing but a route to the API.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app/mcp

# Dependency layer first, from the lock, so a code edit does not re-resolve.
COPY mcp/pyproject.toml mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mcp/aperture_mcp ./aperture_mcp
COPY mcp/README.md ./
RUN uv sync --frozen --no-dev

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aperture
USER aperture

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2)" || exit 1

CMD ["aperture-mcp", "--http"]
