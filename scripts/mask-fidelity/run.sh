#!/usr/bin/env bash
# Is the mask on screen the mask the model returned?
#
# The browser decodes RLE itself, so there are two implementations of the same
# format and they have to agree exactly. This runs the real frontend module
# (bundled, not copied) against masks encoded by the real server encoder.
set -euo pipefail
cd "$(dirname "$0")/../.."

BUILD="scripts/mask-fidelity/.build"
mkdir -p "$BUILD"

echo "generating cases with the server's encoder..."
docker compose cp scripts/mask-fidelity/cases.py runtime:/tmp/cases.py >/dev/null
docker compose exec -T runtime python /tmp/cases.py > "$BUILD/cases.json"

echo "bundling the frontend mask module..."
(cd frontend && npx esbuild src/lib/masks.ts --bundle --format=esm \
  --outfile="../$BUILD/masks.mjs" >/dev/null)

node scripts/mask-fidelity/check.mjs "$BUILD/cases.json"
