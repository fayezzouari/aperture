# Aperture control plane: API, job worker and log sink all ship in this image.
# Which one you get is decided by the command, so they can never drift apart.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# ffmpeg is here for the video job (R3.8); curl for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer first: application edits do not re-resolve the environment.
COPY core/pyproject.toml core/README.md* /app/core/
COPY core/aperture_core/__init__.py /app/core/aperture_core/__init__.py
COPY backend/pyproject.toml /app/backend/
RUN cd /app/backend && uv venv /app/.venv && uv pip install --python /app/.venv/bin/python -r pyproject.toml

COPY core /app/core
COPY backend /app/backend
RUN uv pip install --python /app/.venv/bin/python --no-deps -e /app/core -e /app/backend

WORKDIR /app/backend

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aperture \
    && chown -R aperture:aperture /app
USER aperture

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=25s --retries=4 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Migrate, then serve with several uvicorn workers; see serve.sh.
CMD ["/app/backend/serve.sh"]


# --------------------------------------------------------------- converter ---
# Optional. Format conversion needs torch, ultralytics and the onnx tooling -
# gigabytes that the serving path has no use for - so they live in their own
# image, behind the `convert` compose profile, watching their own queue.
FROM base AS converter

USER root
# torch and torchvision must come from the SAME index: a PyPI torchvision against
# a cpu-index torch registers no C++ ops, and the first thing that needs one dies
# with "operator torchvision::nms does not exist".
RUN uv pip install --python /app/.venv/bin/python \
        "torch>=2.3" "torchvision>=0.18" --index-url https://download.pytorch.org/whl/cpu \
    && uv pip install --python /app/.venv/bin/python \
        "ultralytics>=8.3" "rfdetr>=1.6" "onnx>=1.16" "onnxconverter-common>=1.14" \
        "roboflow>=1.1" \
        "onnxruntime>=1.18" "onnxslim>=0.1.34" "onnxscript>=0.1" \
    && chown -R aperture:aperture /app
USER aperture

ENV YOLO_CONFIG_DIR=/tmp/ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -m app.jobs.heartbeat /tmp/aperture-converter.alive || exit 1

CMD ["arq", "app.jobs.worker.ConverterSettings"]
