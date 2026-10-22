#!/bin/sh
# Packs the Claude Desktop extension: dist/aperture.mcpb.
#
#   mcp/bundle/build.sh [out-dir]
#
# A .mcpb is a zip with manifest.json at its root. This one is a uv bundle: it
# ships the package source, pyproject.toml and uv.lock, and Claude Desktop
# installs the locked dependencies itself, so one file works on every platform.
# The manifest's version follows pyproject.toml.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
mcp=$(dirname "$here")
out=${1:-"$mcp/dist"}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$mcp/pyproject.toml" | head -n 1)
sed "s/\"version\": \"[^\"]*\"/\"version\": \"$version\"/" "$here/manifest.json" > "$stage/manifest.json"
cp "$here/icon.png" "$mcp/pyproject.toml" "$mcp/uv.lock" "$mcp/README.md" "$stage/"
(cd "$mcp" && find aperture_mcp -name '*.py' | while read -r f; do
    mkdir -p "$stage/$(dirname "$f")" && cp "$f" "$stage/$f"
done)

mkdir -p "$out"
rm -f "$out/aperture.mcpb"
(cd "$stage" && zip -qrX "$out/aperture.mcpb" .)
echo "$out/aperture.mcpb ($version)"
