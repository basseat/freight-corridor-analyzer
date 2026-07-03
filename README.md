# Freight Corridor Analyzer

Maps the most-used long-distance road freight corridors across Germany, France,
Spain, Netherlands, and Poland, using real Eurostat data. Extends the earlier
[Green Miles](../green_miles_problem) project from tonnage totals into a
routable network view.

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

A planned second DAG, `build_network`, builds the routable road network
(OSM extracts → osmium filter → osm2pgrouting → PostGIS) and gates
`freight_assignment`'s downstream routing tasks via an Airflow Dataset.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Tests

```bash
pytest
```

`tests/` wraps the self-checks already embedded in `src/ipf.py` and
`src/stage_clean.py` (run directly via `python src/ipf.py` /
`python src/stage_clean.py` for the same checks with printed output).

## Environment variables

- `FREIGHT_DATA_DIR` — parquet staging directory (default `/tmp/freight`)
- `FREIGHT_DB_URI` — Postgres URI for the `load_od` task; load is skipped
  if unset
