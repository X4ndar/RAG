#!/usr/bin/env bash
set -euo pipefail

# Run Alembic inside the backend container. This is the only supported way
# to manage migrations — the in-repo DATABASE_URL targets the in-network
# postgres hostname, so host-side alembic calls would silently resolve to a
# different (or nonexistent) database.
#
# Usage:
#   scripts/migrate.sh                       # upgrade head
#   scripts/migrate.sh upgrade head
#   scripts/migrate.sh downgrade -1
#   scripts/migrate.sh revision --autogenerate -m "add tenants table"
#   scripts/migrate.sh current
#   scripts/migrate.sh history

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE="docker compose --env-file ${ROOT_DIR}/.env -f ${ROOT_DIR}/docker/docker-compose.yml"

if [ "$#" -eq 0 ]; then
    set -- upgrade head
fi

# Make sure the backend service is running before we exec.
if ! ${COMPOSE} ps --services --filter status=running | grep -qx backend; then
    echo "==> backend container not running; starting stack..."
    ${COMPOSE} up -d backend
fi

echo "==> alembic $*"
exec ${COMPOSE} exec -T backend alembic "$@"
