#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
# Compose normally gives exported shell settings priority over .env.
unset VOXVAULT_PORT VOXVAULT_KEY_DIR_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
exec docker compose --env-file .env "$@"
