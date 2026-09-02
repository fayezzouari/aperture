#!/usr/bin/env bash
# Generate fully typed clients from the OpenAPI document (R3.9).
#
# The hand-written clients in sdk/python and sdk/typescript need no tooling and
# cover the inference API. This script produces exhaustive clients for the whole
# control plane, and needs network access for the generators themselves.
set -euo pipefail
cd "$(dirname "$0")/.."

SPEC=${SPEC:-openapi.json}
if [ ! -f "$SPEC" ]; then
  echo "no $SPEC - run 'make openapi' first (the stack must be up)" >&2
  exit 1
fi

mkdir -p sdk/generated

echo "==> TypeScript client -> sdk/generated/typescript"
npx --yes @hey-api/openapi-ts \
  --input "$SPEC" \
  --output sdk/generated/typescript \
  --client @hey-api/client-fetch

echo "==> Python client -> sdk/generated/python"
uvx --from openapi-python-client openapi-python-client generate \
  --path "$SPEC" \
  --output-path sdk/generated/python \
  --overwrite

echo
echo "Generated clients are in sdk/generated/. The hand-written ones in"
echo "sdk/python and sdk/typescript stay the recommended path for products that"
echo "only call predict()."
