#!/bin/sh
# The API container's entrypoint: bring the schema up to date, then serve.
#
# Migrating here, once, before uvicorn forks its workers means an upgrade is
# just a new image - no separate `make migrate` - and the workers never race
# each other through the same revision. Two API containers starting together
# are serialised by an advisory lock in alembic/env.py.
#
# Set AP_MIGRATE_ON_START=false when migrations run as their own deploy step.
set -e

if [ "${AP_MIGRATE_ON_START:-true}" = "true" ]; then
    alembic upgrade head
fi

# Uvicorn with several workers: the gateway is IO-bound, so processes buy
# concurrency without any of them owning a GPU.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --workers 4 --timeout-keep-alive 65 --no-access-log \
    --proxy-headers --forwarded-allow-ips "*"
