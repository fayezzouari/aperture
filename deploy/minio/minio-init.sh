#!/bin/sh
# Create the three buckets Aperture needs and lock them down. Idempotent.
set -e

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

for bucket in aperture-weights aperture-captures aperture-reports; do
  if mc ls "local/$bucket" >/dev/null 2>&1; then
    echo "bucket $bucket already exists"
  else
    mc mb "local/$bucket"
    echo "created bucket $bucket"
  fi
  # Nothing in object storage is public: the API hands out short-lived
  # presigned URLs instead (R7.5).
  mc anonymous set none "local/$bucket" >/dev/null 2>&1 || true
done

# Weights are immutable once published, so versioning is cheap insurance
# against an operator deleting the wrong object.
mc version enable local/aperture-weights >/dev/null 2>&1 || true

echo "minio ready"
