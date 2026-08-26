#!/usr/bin/env bash
#
# backup_postgres.sh — dump the Postgres container to a timestamped .sql file.
#
# Usage:
#   backup_postgres.sh [--container NAME] [--output-dir DIR] [--dry-run] [--help]
#
# Environment overrides (read first):
#   BACKUP_CONTAINER_NAME  default radar-postgres
#   BACKUP_OUTPUT_DIR      default ./backups
#
# --dry-run prints the docker command without executing it.
#
# Phase 12 ships this script as the only sanctioned way to back up the
# Postgres volume. Wire it to a nightly cron in production:
#
#   0 3 * * * cd /srv/radar && make backup
#
# The output file is plain SQL; it can be piped straight into
# `restore_postgres.sh` on a fresh host.

set -euo pipefail

CONTAINER="${BACKUP_CONTAINER_NAME:-radar-postgres}"
OUTPUT_DIR="${BACKUP_OUTPUT_DIR:-./backups}"
DRY_RUN=0

print_usage() {
    cat <<'USAGE'
backup_postgres.sh — dump the Postgres container to a SQL file.

Flags:
  --container NAME    override the docker container name (default: radar-postgres)
  --output-dir DIR    override the output directory (default: ./backups)
  --dry-run           print the docker command without executing it
  --help              show this help

Environment:
  BACKUP_CONTAINER_NAME  default container name
  BACKUP_OUTPUT_DIR      default output directory
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --container)
            CONTAINER="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
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

# UTC timestamp — stable across hosts / DST.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUTFILE="${OUTPUT_DIR%/}/radar-${TS}.sql"

DOCKER_CMD=(docker exec "${CONTAINER}" pg_dump -U radar -d radar --no-owner --clean --if-exists)

if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "DRY-RUN: would execute the following:"
    printf '  mkdir -p %q\n' "${OUTPUT_DIR}"
    printf '  '
    printf '%q ' "${DOCKER_CMD[@]}"
    printf '> %q\n' "${OUTFILE}"
    exit 0
fi

mkdir -p "${OUTPUT_DIR}"

echo "backing up container ${CONTAINER} → ${OUTFILE}"
"${DOCKER_CMD[@]}" > "${OUTFILE}"

bytes="$(wc -c < "${OUTFILE}" | tr -d ' ')"
echo "done — ${bytes} bytes written to ${OUTFILE}"