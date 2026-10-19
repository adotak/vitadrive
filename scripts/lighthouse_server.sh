#!/usr/bin/env bash
set -euo pipefail

db_path="$(mktemp /tmp/vitadrive-lighthouse.XXXXXX)"
vitadrive --db "$db_path" demo --days 60 --seed 7
unset CLERK_PUBLISHABLE_KEY CLERK_SECRET_KEY CLERK_JWKS_URL CLERK_AUTHORIZED_PARTIES
export DATABASE_URL="$db_path"
export VITADRIVE_RATE_LIMITS=0
exec uvicorn vitadrive.api:create_app --factory --host 127.0.0.1 --port 8000
