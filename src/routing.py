import json

import requests
from sqlalchemy import create_engine, text

FIVE = ["DE", "FR", "ES", "NL", "PL"]
GISCO = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson"


def gisco_url(year=2021, resolution="01M"):
    return f"{GISCO}/NUTS_RG_{resolution}_{year}_4326_LEVL_2.geojson"


def parse_nuts2_features(geojson, countries=FIVE):
    out = []
    for f in geojson["features"]:
        p = f["properties"]
        if p.get("LEVL_CODE") == 2 and p.get("CNTR_CODE") in countries:
            out.append((p["NUTS_ID"], json.dumps(f["geometry"])))
    return out


def fetch_nuts2_features(year=2021, resolution="01M"):
    r = requests.get(gisco_url(year, resolution), timeout=600)
    r.raise_for_status()
    return parse_nuts2_features(r.json())


def centroid_nodes_sql():
    # snap each NUTS-2 centroid to its nearest routable vertex (KNN via <->)
    return """
DROP TABLE IF EXISTS centroid_nodes;
CREATE TABLE centroid_nodes AS
SELECT r.nuts2, n.id AS node, (r.c <-> n.geom) AS dist
FROM (SELECT nuts2, ST_Centroid(the_geom) AS c FROM nuts2_regions) r
CROSS JOIN LATERAL (
    SELECT id, geom FROM ways_vertices_pgr
    ORDER BY geom <-> r.c LIMIT 1
) n;
ALTER TABLE centroid_nodes ADD PRIMARY KEY (nuts2);
"""


def route_od_sql(vintage):
    v = int(vintage)
    combos = (
        "SELECT DISTINCT o.node AS source, d.node AS target "
        "FROM freight_od_matrix m "
        "JOIN centroid_nodes o ON o.nuts2 = m.orig_nuts2 "
        "JOIN centroid_nodes d ON d.nuts2 = m.dest_nuts2 "
        f"WHERE m.vintage = {v} AND o.node <> d.node"
    )
    return f"""
DROP TABLE IF EXISTS od_routes;
CREATE TABLE od_routes AS
WITH od AS (
    SELECT o.node AS source, d.node AS target, m.tonnes
    FROM freight_od_matrix m
    JOIN centroid_nodes o ON o.nuts2 = m.orig_nuts2
    JOIN centroid_nodes d ON d.nuts2 = m.dest_nuts2
    WHERE m.vintage = {v} AND o.node <> d.node
),
paths AS (
    SELECT start_vid, end_vid, edge
    FROM pgr_dijkstra(
        'SELECT id, source, target, cost, reverse_cost FROM ways',
        '{combos}',
        true)
    WHERE edge <> -1
)
SELECT p.edge, od.tonnes
FROM paths p
JOIN od ON od.source = p.start_vid AND od.target = p.end_vid;
"""


def edge_loads_sql():
    # sum tonnage routed over each edge = the "most-used routes" output
    return """
DROP TABLE IF EXISTS edge_loads;
CREATE TABLE edge_loads AS
SELECT w.id, w.geom, SUM(r.tonnes) AS tonnes
FROM od_routes r
JOIN ways w ON w.id = r.edge
GROUP BY w.id, w.geom;
CREATE INDEX ON edge_loads USING gist (geom);
"""


def run_script(db_uri, sql):
    engine = create_engine(db_uri)
    with engine.begin() as c:
        c.exec_driver_sql(sql)


def load_nuts2_regions(db_uri, year=2021):
    feats = fetch_nuts2_features(year)
    engine = create_engine(db_uri)
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS nuts2_regions ("
            "nuts2 text PRIMARY KEY, the_geom geometry(Geometry, 4326))"))
        c.execute(text("TRUNCATE nuts2_regions"))
        c.execute(
            text("INSERT INTO nuts2_regions (nuts2, the_geom) VALUES "
                 "(:nuts2, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))"),
            [{"nuts2": n, "geom": g} for n, g in feats])
    return len(feats)


def snap_centroids(db_uri):
    run_script(db_uri, centroid_nodes_sql())


def route_od_pairs(db_uri, vintage):
    run_script(db_uri, route_od_sql(vintage))


def compute_edge_loads(db_uri):
    run_script(db_uri, edge_loads_sql())


if __name__ == "__main__":
    # offline check: GISCO filtering + SQL builders, no DB/network needed
    sample = {"features": [
        {"properties": {"NUTS_ID": "DE11", "CNTR_CODE": "DE", "LEVL_CODE": 2},
         "geometry": {"type": "Point", "coordinates": [9.1, 48.8]}},
        {"properties": {"NUTS_ID": "DE111", "CNTR_CODE": "DE", "LEVL_CODE": 3},
         "geometry": {"type": "Point", "coordinates": [9.2, 48.8]}},  # NUTS-3 -> dropped
        {"properties": {"NUTS_ID": "ITC4", "CNTR_CODE": "IT", "LEVL_CODE": 2},
         "geometry": {"type": "Point", "coordinates": [9.2, 45.5]}},  # not in the five
        {"properties": {"NUTS_ID": "PL91", "CNTR_CODE": "PL", "LEVL_CODE": 2},
         "geometry": {"type": "Point", "coordinates": [21.0, 52.2]}},
    ]}
    feats = parse_nuts2_features(sample)
    assert [n for n, _ in feats] == ["DE11", "PL91"], feats
    print("GISCO NUTS-2 filtering OK ->", [n for n, _ in feats])

    assert "'2022'" not in route_od_sql(2022) and "vintage = 2022" in route_od_sql(2022)
    try:
        route_od_sql("2022; DROP TABLE ways")
        raise AssertionError("vintage must be int-coerced")
    except ValueError:
        pass
    print("vintage is int-injected (no SQL injection) OK")
    print(gisco_url(2021))
