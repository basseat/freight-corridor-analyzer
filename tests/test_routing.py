import pytest

from routing import gisco_url, parse_nuts2_features, centroid_nodes_sql, route_od_sql, edge_loads_sql

SAMPLE = {"features": [
    {"properties": {"NUTS_ID": "DE11", "CNTR_CODE": "DE", "LEVL_CODE": 2},
     "geometry": {"type": "Point", "coordinates": [9.1, 48.8]}},
    {"properties": {"NUTS_ID": "DE111", "CNTR_CODE": "DE", "LEVL_CODE": 3},
     "geometry": {"type": "Point", "coordinates": [9.2, 48.8]}},   # NUTS-3 -> dropped
    {"properties": {"NUTS_ID": "ITC4", "CNTR_CODE": "IT", "LEVL_CODE": 2},
     "geometry": {"type": "Point", "coordinates": [9.2, 45.5]}},   # not in the five
    {"properties": {"NUTS_ID": "PL91", "CNTR_CODE": "PL", "LEVL_CODE": 2},
     "geometry": {"type": "Point", "coordinates": [21.0, 52.2]}},
]}


def test_gisco_url_encodes_level_and_srid():
    url = gisco_url(2021)
    assert "LEVL_2" in url and "4326" in url and url.endswith("2021_4326_LEVL_2.geojson")


def test_parse_keeps_only_level2_features_in_the_five():
    feats = parse_nuts2_features(SAMPLE)
    assert [n for n, _ in feats] == ["DE11", "PL91"]  # DE111 (L3) and ITC4 (IT) dropped


def test_route_od_sql_int_injects_vintage():
    sql = route_od_sql(2022)
    assert "m.vintage = 2022" in sql
    assert "pgr_dijkstra" in sql and "edge <> -1" in sql


def test_route_od_sql_rejects_non_int_vintage():
    with pytest.raises(ValueError):
        route_od_sql("2022; DROP TABLE ways")


def test_centroid_nodes_sql_snaps_via_knn():
    sql = centroid_nodes_sql()
    assert "ways_vertices_pgr" in sql and "<->" in sql and "ST_Centroid" in sql


def test_edge_loads_sql_aggregates_tonnes_per_edge():
    sql = edge_loads_sql()
    assert "SUM(r.tonnes)" in sql and "GROUP BY w.id" in sql and "od_routes" in sql
