#!/usr/bin/env bash
# Stand up a throwaway PostGIS+pgRouting cluster, run the routing integration
# test against it, then tear it down. Nothing touches an existing server.
#
# Requires Homebrew postgresql@17 + postgis + pgrouting. Override via env:
#   PGBIN   postgres bin dir   (default /opt/homebrew/opt/postgresql@17/bin)
#   PGPORT  cluster port       (default 5433)
#   PYBIN   python interpreter (default ./.venv/bin/python)
set -euo pipefail

PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@17/bin}"
PGPORT="${PGPORT:-5433}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYBIN="${PYBIN:-$REPO_ROOT/.venv/bin/python}"
WORKDIR="$(mktemp -d)"
PGDATA="$WORKDIR/pgdata"
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

cleanup() {
    "$PGBIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "initdb -> $PGDATA"
"$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust --locale=en_US.UTF-8 >/dev/null

echo "starting cluster on :$PGPORT"
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k /tmp" -l "$PGDATA/server.log" start >/dev/null
for _ in $(seq 1 30); do
    "$PGBIN/pg_isready" -p "$PGPORT" -h localhost >/dev/null 2>&1 && break
    sleep 1
done

"$PGBIN/createdb" -p "$PGPORT" -h localhost -U postgres freight_it
"$PGBIN/psql" -p "$PGPORT" -h localhost -U postgres -d freight_it \
    -c "CREATE EXTENSION postgis; CREATE EXTENSION pgrouting;" >/dev/null
echo "cluster ready (postgis+pgrouting), running integration test"

ROUTING_IT_DB_URI="postgresql+psycopg2://postgres@localhost:$PGPORT/freight_it" \
    "$PYBIN" -m pytest "$REPO_ROOT/tests/test_routing_pg.py" -v
