#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
    printf 'Usage: sudo %s\nRemoves service data, downloaded models, and the private key.\n' "$0" >&2
    exit 2
fi

cd -- "$(dirname -- "$0")"

docker compose --profile reset config --quiet
if ! docker image inspect voxvault-server:latest >/dev/null 2>&1; then
    docker compose build voxvault
fi

docker compose down -v
docker compose --profile reset run --rm --no-deps reset-keys

printf 'VoxVault reset complete. Start again with: sudo docker compose up -d --build\n'
