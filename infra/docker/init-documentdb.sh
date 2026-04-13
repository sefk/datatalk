#!/bin/bash
# Initialize DocumentDB extension and create LibreChat user.
# This runs on first Postgres startup only (docker-entrypoint-initdb.d).
# It runs against the default 'postgres' database since pg_cron requires it.

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;
    SELECT documentdb_api.create_user(
        '{"createUser": "librechat", "pwd": "librechat_dev", "roles": [{"role": "clusterAdmin", "db": "admin"}, {"role": "readWriteAnyDatabase", "db": "admin"}]}'::documentdb_core.bson
    );
EOSQL
