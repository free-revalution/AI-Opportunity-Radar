#!/usr/bin/env bash
#
# restore_postgres.sh — load a .sql dump into the Postgres container.
#
# Usage:
#   restore_postgres.sh --file PATH [--container NAME] [--dry-run] [--help]
#
# Environment overrides (read first):
#   BACKUP_CONTAINER_NAME  default radar-postgres
#
# --dry-run prints the docker command without executing it.
#
# WARNING: this DROPS and recreates the schema. Only run on a fresh
# database or after you have taken a fresh backup. Verify with --dry-run
# first.

set -euo pipefail

CONTAINER="${BACKUP_CONTAINER_NAME:-radar-postgres}"
SQL_FILE=""
DRY_RUN=0

print_usage() {
    cat <<'USAGE'
restore_postgres.sh — load a SQL dump into the Postgres container.

Flags:
  --file PATH         path to the .sql file (required)
  --container NAME    override the docker container name (default: radar-postgres)
  --dry-run           print the docker command without executing it
  --help              show this help

Environment:
  BACKUP_CONTAINER_NAME  default container name
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --file)
            SQL_FILE="$2"
            shift 2
            ;;
        --container)
            CONTAINER="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            echo "unknown flag: $1" >&2
            print_usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "${SQL_FILE}" ]]; then
    echo "error: --file is required" >&2
    print_usage >&2
    exit 2
fi
if [[ ! -f "${SQL_FILE}" ]]; then
    echo "error: file not found: ${SQL_FILE}" >&2
    exit 1
fi

DOCKER_CMD=(docker exec -i "${CONTAINER}" psql -U radar -d radar)

if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "DRY-RUN: would execute the following:"
    printf '  cat %q | ' "${SQL_FILE}"
    printf '%q' "${DOCKER_CMD[*]}"
    echo
    exit 0
fi

echo "restoring ${SQL_FILE} → container ${CONTAINER}"
cat "${SQL_FILE}" | "${DOCKER_CMD[@]}"
echo "done — restore complete; verify with: docker exec ${CONTAINER} psql -U radar -d radar -c '\\dt'"