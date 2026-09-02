#!/usr/bin/env bash
# Create a demo model with a synthetic detector, validate, publish and deploy it.
# Useful for a first look at the UI with something in it.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --no-project --with httpx --with onnx --with numpy --with pillow \
  python scripts/seed_demo.py "$@"
