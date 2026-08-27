#!/bin/sh
# Creates the secondary databases used by sibling containers on first run.
# Postgres image auto-runs every *.sh in /docker-entrypoint-initdb.d/ exactly
# once when the data directory is empty. Idempotent if the DB already exists.
set -e

echo "[radar-init] creating n8n database if missing"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE n8n OWNER $POSTGRES_USER'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
EOSQL

echo "[radar-init] done"