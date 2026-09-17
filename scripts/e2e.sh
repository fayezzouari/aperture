#!/usr/bin/env bash
# Run the end-to-end check against a running stack.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --no-project --with httpx --with onnx --with numpy --with pillow \
  --with-editable ./mcp \
  python scripts/e2e.py "$@"
