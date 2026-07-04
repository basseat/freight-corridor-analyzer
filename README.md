# Freight Corridor Analyzer

Maps the most-used long-distance road freight corridors across Germany, France,
Spain, Netherlands, and Poland, using real Eurostat data. Extends the earlier
**Green Miles** project from tonnage totals into a routable network view.

## Why IPF

Eurostat publishes no origin-destination matrix for road freight (statistical
confidentiality). Instead we reconstruct one via iterative proportional fitting
(IPF) from three published marginals:

- `road_go_ta_rl` — tonnes loaded, by region
- `road_go_ta_ru` — tonnes unloaded, by region
- `road_go_ia_rc` — international tonnes, by country pair

The domestic diagonal (tonnes staying inside a country) is derived as
`loaded_total − international_outbound`, so it depends only on published
tables rather than a fourth confidential source.

Region granularity is NUTS-2 for v1; NUTS-3 is a documented later limitation.
All tonnage is in THS_T (thousand tonnes).

## Pipeline

`dags/freight_assignment.py` is an Airflow TaskFlow DAG:

1. `stage_eurostat` — pull the three JSON-stat tables, clean, aggregate
   NUTS-3 → NUTS-2, write to parquet (`src/stage_clean.py`)
2. `ipf_reconstruct` — run IPF against the three marginals to build the
   O-D matrix (`src/ipf.py`)
3. `validate_od` — fail the run if the matrix is empty or IPF didn't
   converge within `MAX_MARGINAL_ERROR`
4. `load_od` — upsert into Postgres, keyed by vintage year (skipped if
   `FREIGHT_DB_URI` is unset)

Tasks pass parquet paths between each other, not XCom payloads.

`dags/build_network.py` is the second DAG, building the routable road
network:

1. `download` — pull each of the five Geofabrik country extracts
   (`.osm.pbf`), mapped over `DE`/`FR`/`ES`/`NL`/`PL`
2. `filter_country` — `osmium tags-filter` down to motorway/trunk/primary
   ways per country
3. `merge` — `osmium merge` the five filtered extracts into one network
4. `load` — `osm2pgrouting` loads the merged extract into PostGIS and
   builds the routable topology (`ways`, `ways_vertices_pgr`)

The `load` task declares `Asset("postgres://network/ways_topology")` as
an outlet. A later routing DAG (`map_centroids` → `route_od_pairs` →
`compute_edge_loads`) will schedule off that same asset instead of a cron
schedule, so routing only reruns once the network topology is rebuilt.
(In Airflow 3.x this is the `Asset` API — the older `Dataset` name from
2.x no longer exists.)

`src/network.py` keeps the OS-process invocations (`osmium`,
`osm2pgrouting`) as pure command builders (`filter_cmd`, `merge_cmd`,
`osm2pgrouting_cmd`) so they're unit-testable without actually running
the binaries.

`dags/route_freight.py` is the third DAG, turning the O-D matrix into
the "most-used routes" output. It is scheduled off the
`Asset("postgres://network/ways_topology")` that `build_network`
produces, so it reruns whenever the network topology is rebuilt:

1. `map_centroids` — pull NUTS-2 region polygons from Eurostat GISCO,
   load them into PostGIS, then snap each region's centroid to its
   nearest routable vertex (`ways_vertices_pgr`) via a KNN (`<->`) query
2. `route` — `pgr_dijkstra` (combinations / one-to-many form) over every
   snapped O-D pair for the target vintage, expanding each shortest path
   to the edges it traverses and attaching that pair's tonnage
3. `edge_loads` — sum tonnage per edge into `edge_loads` (edge geometry +
   total tonnes), the routable "most-used corridors" layer for Tableau

The vintage routed defaults to the `freight_processed_vintage` Airflow
Variable set by `freight_assignment.load_od` (override with
`params.vintage`). `src/routing.py` keeps every SQL statement in pure
builder functions (`centroid_nodes_sql`, `route_od_sql`, `edge_loads_sql`)
so they're unit-testable, and the vintage is `int`-coerced before it's
interpolated into SQL.

> The O-D matrix (`freight_od_matrix`, from `freight_assignment`) and the
> routable network (`ways` / `ways_vertices_pgr`, from `build_network`)
> must live in the **same** database, since routing joins them in SQL.
> Point `FREIGHT_DB_URI`, `NETWORK_DB_URI`, and `ROUTING_DB_URI` at one
> PostGIS+pgRouting warehouse in a real deployment.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`build_network` additionally requires the `osmium` (osmium-tool) and
`osm2pgrouting` CLI binaries on PATH, and a PostGIS database with the
pgRouting extension enabled — none of these are pip-installable, so
install them via your OS package manager (e.g. `brew install osmium-tool
osm2pgrouting`, or `apt-get install osmium-tool osm2pgrouting`).

## Tests

```bash
pytest
```

`tests/` wraps the self-checks already embedded in `src/ipf.py` and
`src/stage_clean.py` (run directly via `python src/ipf.py` /
`python src/stage_clean.py` for the same checks with printed output).

### Routing integration check

The unit tests cover the SQL builders as strings but never touch a
database. `tests/test_routing_pg.py` runs the real `snap_centroids` →
`route_od_pairs` → `compute_edge_loads` chain against a live PostGIS +
pgRouting database, using a tiny synthetic graph it seeds itself (no OSM
download, no `osmium`/`osm2pgrouting` needed). It **skips** unless
`ROUTING_IT_DB_URI` is set.

On a Mac with Homebrew `postgresql@17` + `postgis` + `pgrouting`, the
whole thing is one command:

```bash
./scripts/run_integration.sh
```

It spins up a throwaway cluster on port 5433, enables the extensions,
runs the test, and tears the cluster down again — nothing touches an
existing server.

To do it manually against your own PostgreSQL (e.g. an EDB install at
`/Library/PostgreSQL/18`):

```bash
# 1. Install PostGIS + pgRouting into the server. For the EDB build,
#    launch Application Stack Builder (bundled with the installer) and
#    pick the "PostGIS Bundle" under Spatial Extensions — it includes
#    pgRouting. (Homebrew's `postgis` targets a Homebrew server, not EDB.)

# 2. Create a throwaway test DB and enable the extensions
/Library/PostgreSQL/18/bin/createdb -U postgres freight_it
/Library/PostgreSQL/18/bin/psql -U postgres -d freight_it \
  -c "CREATE EXTENSION postgis; CREATE EXTENSION pgrouting;"

# 3. Point the env var at it and run just the integration test
cd ~/Downloads/freight-corridor-analyzer
export ROUTING_IT_DB_URI="postgresql+psycopg2://postgres:YOURPW@localhost:5432/freight_it"
./.venv/bin/python -m pytest tests/test_routing_pg.py -v
```

The test asserts each centroid snaps to the nearest terminal node and
that tonnage from both O-D directions accumulates on the shortest-path
edges (never the deliberately-slow direct edge, and excluding an
out-of-vintage row) — i.e. the whole routing chain executes correctly
against real `pgr_dijkstra`.

A full **real-data** run (Tier B) additionally needs `osmium` and
`osm2pgrouting` on PATH plus the multi-GB Geofabrik extracts: run
`build_network` to populate `ways`, load an O-D vintage via
`freight_assignment`, then `route_freight`.

## Environment variables

- `FREIGHT_DATA_DIR` — parquet staging directory (default `/tmp/freight`)
- `FREIGHT_DB_URI` — Postgres URI for the `load_od` task; load is skipped
  if unset
- `NETWORK_DATA_DIR` — OSM extract staging directory (default `/tmp/network`)
- `NETWORK_DB_URI` — PostGIS URI for `osm2pgrouting`; `build_network`'s
  `load` task raises if unset (the network graph has no meaningful
  no-op skip path the way the tonnage load does)
- `NETWORK_MAPCONFIG` — path to the osm2pgrouting tag-mapping XML
  (default `/usr/share/osm2pgrouting/mapconfig.xml`, the standard
  package install location)
- `ROUTING_DB_URI` — database for `route_freight` (falls back to
  `NETWORK_DB_URI`); must hold both `freight_od_matrix` and the network
  tables
- `NUTS_YEAR` — GISCO NUTS classification vintage for region geometry
  (default `2021`)
