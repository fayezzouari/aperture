# The inference runtime. Three targets:
#   cpu   - slim, onnxruntime on CPU. The default, good for development.
#   gpu   - CUDA base with onnxruntime-gpu. Use this on GPU hosts.
#   torch - cpu plus torch and ultralytics, so a .pt serves as it is.
#
# The split exists because torch is ~2.5 GB that an ONNX replica never touches,
# not because ONNX is required: run the torch target and `.pt` weights are
# served directly, with placement routing them there automatically.
FROM python:3.12-slim AS cpu

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    AP_CACHE_DIR=/var/cache/aperture

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY core/pyproject.toml /app/core/
COPY core/aperture_core/__init__.py /app/core/aperture_core/__init__.py
COPY runtime/pyproject.toml /app/runtime/
RUN cd /app/runtime && uv venv /app/.venv && uv pip install --python /app/.venv/bin/python -r pyproject.toml

COPY core /app/core
COPY runtime /app/runtime
RUN uv pip install --python /app/.venv/bin/python --no-deps -e /app/core -e /app/runtime

WORKDIR /app/runtime

RUN mkdir -p /var/cache/aperture \
    && useradd --uid 10002 --create-home --shell /usr/sbin/nologin aperture \
    && chown -R aperture:aperture /app /var/cache/aperture
USER aperture

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=4s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:9100/healthz || exit 1

# One process, one event loop: the batcher must see every request to batch them.
# Scale by adding replicas, not workers.
CMD ["uvicorn", "runtime.main:app", "--host", "0.0.0.0", "--port", "9100", \
     "--no-access-log", "--timeout-keep-alive", "65"]


# --------------------------------------------------------------------- gpu ---
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS gpu

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    AP_CACHE_DIR=/var/cache/aperture

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3-pip \
       curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY core /app/core
COPY runtime /app/runtime

RUN uv venv --python python3.11 /app/.venv \
    && uv pip install --python /app/.venv/bin/python -r /app/runtime/pyproject.toml \
    && uv pip install --python /app/.venv/bin/python "onnxruntime-gpu>=1.18" \
    && uv pip uninstall --python /app/.venv/bin/python onnxruntime \
    && uv pip install --python /app/.venv/bin/python --no-deps -e /app/core -e /app/runtime

WORKDIR /app/runtime
RUN mkdir -p /var/cache/aperture

EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=4s --start-period=40s --retries=6 \
    CMD curl -fsS http://localhost:9100/healthz || exit 1

CMD ["uvicorn", "runtime.main:app", "--host", "0.0.0.0", "--port", "9100", \
     "--no-access-log", "--timeout-keep-alive", "65"]


# ------------------------------------------------------------------- torch ---
# For serving .pt directly: TorchScript archives, pickled modules, Ultralytics
# checkpoints (whose classes have to be importable to unpickle), and RF-DETR
# state_dicts (which rfdetr rebuilds from the layer shapes). Both torch packages
# come from the CPU wheel index - mixing indexes is what breaks torchvision's
# custom ops.
FROM cpu AS torch

USER root
# ultralytics pulls opencv-python, which links the X client libraries and fails
# to import on a headless server. The headless build is the same library without
# the GUI backends, so it is swapped in rather than installing an X stack here.
RUN uv pip install --python /app/.venv/bin/python \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && uv pip install --python /app/.venv/bin/python ultralytics rfdetr \
    && uv pip uninstall --python /app/.venv/bin/python opencv-python \
    && uv pip install --python /app/.venv/bin/python opencv-python-headless \
    && rm -rf /root/.cache
USER aperture
