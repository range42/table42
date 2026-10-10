#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
exec uv run --locked uvicorn app.main:create_app --factory --host "${RANGE42_HOST:-127.0.0.1}" --port "${RANGE42_PORT:-8042}"
