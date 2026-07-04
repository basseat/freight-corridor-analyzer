import os

import pytest
from sqlalchemy import create_engine, text

from routing import snap_centroids, route_od_pairs, compute_edge_loads

DB_URI = os.environ.get("ROUTING_IT_DB_URI")
pytestmark = pytest.mark.skipif(
    not DB_URI,
    reason="set ROUTING_IT_DB_URI to a PostGIS+pgRouting DB to run the integration check")

# synthetic 5-node chain (nodes 1..5 along a line) plus a slow direct edge 1->5.
# region XX1 sits by node 1, XX2 by node 5; shortest path must take the chain.
# ways / ways_vertices_pgr mirror the osm2pgrouting 3.x schema (id, geom).
SEED = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

DROP TABLE IF EXISTS ways_vertices_pgr, ways, nuts2_regions, freight_od_matrix,
    centroid_nodes, od_routes, edge_loads CASCADE;

CREATE TABLE ways_vertices_pgr (id bigint PRIMARY KEY, geom geometry(Point, 4326));
INSERT INTO ways_vertices_pgr (id, geom) VALUES
    (1, ST_SetSRID(ST_MakePoint(0.0, 50.0), 4326)),
    (2, ST_SetSRID(ST_MakePoint(0.1, 50.0), 4326)),
    (3, ST_SetSRID(ST_MakePoint(0.2, 50.0), 4326)),
    (4, ST_SetSRID(ST_MakePoint(0.3, 50.0), 4326)),
    (5, ST_SetSRID(ST_MakePoint(0.4, 50.0), 4326));

-- cost_s/reverse_cost_s are the drive-time weight and carry the one-way sign
-- (all +, i.e. two-way here). Chain edges are quick, the direct 105 is slow.
-- tag_id 101 = motorway, so every node qualifies as a major-road snap target.
CREATE TABLE ways (
    id bigint PRIMARY KEY, source bigint, target bigint,
    cost_s double precision, reverse_cost_s double precision,
    tag_id int, geom geometry(LineString, 4326));
INSERT INTO ways (id, source, target, cost_s, reverse_cost_s, tag_id, geom) VALUES
    (101, 1, 2, 10, 10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.1,50.0)), 4326)),
    (102, 2, 3, 10, 10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.1,50.0), ST_MakePoint(0.2,50.0)), 4326)),
    (103, 3, 4, 10, 10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.2,50.0), ST_MakePoint(0.3,50.0)), 4326)),
    (104, 4, 5, 10, 10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.3,50.0), ST_MakePoint(0.4,50.0)), 4326)),
    (105, 1, 5, 1000, 1000, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.4,50.0)), 4326));

CREATE TABLE nuts2_regions (nuts2 text PRIMARY KEY, the_geom geometry(Geometry, 4326));
INSERT INTO nuts2_regions (nuts2, the_geom) VALUES
    ('XX1', ST_SetSRID(ST_MakePoint(0.02, 50.01), 4326)),
    ('XX2', ST_SetSRID(ST_MakePoint(0.38, 50.01), 4326));

CREATE TABLE freight_od_matrix (
    orig_nuts2 text, dest_nuts2 text, tonnes double precision, vintage int);
INSERT INTO freight_od_matrix VALUES
    ('XX1', 'XX2', 100, 2022),
    ('XX2', 'XX1', 40, 2022),
    ('XX1', 'XX2', 999, 2021);
"""


@pytest.fixture()
def engine():
    eng = create_engine(DB_URI)
    with eng.begin() as c:
        c.exec_driver_sql(SEED)
    return eng


def test_centroids_snap_to_nearest_terminal_node(engine):
    snap_centroids(DB_URI)
    with engine.connect() as c:
        nodes = dict(c.execute(text("SELECT nuts2, node FROM centroid_nodes")).all())
    assert nodes == {"XX1": 1, "XX2": 5}


def test_edge_loads_sum_tonnage_on_shortest_path(engine):
    snap_centroids(DB_URI)
    route_od_pairs(DB_URI, 2022)
    compute_edge_loads(DB_URI)
    with engine.connect() as c:
        loads = dict(c.execute(text("SELECT id, tonnes FROM edge_loads")).all())
    # 100 (XX1->XX2) + 40 (XX2->XX1) ride the 4 chain edges; the slow direct
    # edge 105 is never chosen and the 2021 vintage (999) is excluded
    assert loads == {101: 140.0, 102: 140.0, 103: 140.0, 104: 140.0}


# nodes 1-2-3 in a line; edge 12 is a SHORT direct 1<->3 but one-way (3->1 only,
# encoded as negative forward cost). Routing 1->3 must detour via node 2.
SEED_ONEWAY = """
DROP TABLE IF EXISTS ways_vertices_pgr, ways, nuts2_regions, freight_od_matrix,
    centroid_nodes, od_routes, edge_loads CASCADE;

CREATE TABLE ways_vertices_pgr (id bigint PRIMARY KEY, geom geometry(Point, 4326));
INSERT INTO ways_vertices_pgr (id, geom) VALUES
    (1, ST_SetSRID(ST_MakePoint(0.0, 50.0), 4326)),
    (2, ST_SetSRID(ST_MakePoint(0.1, 50.0), 4326)),
    (3, ST_SetSRID(ST_MakePoint(0.2, 50.0), 4326));

CREATE TABLE ways (
    id bigint PRIMARY KEY, source bigint, target bigint,
    cost_s double precision, reverse_cost_s double precision,
    tag_id int, geom geometry(LineString, 4326));
INSERT INTO ways (id, source, target, cost_s, reverse_cost_s, tag_id, geom) VALUES
    (10, 1, 2,  10,  10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.1,50.0)), 4326)),
    (11, 2, 3,  10,  10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.1,50.0), ST_MakePoint(0.2,50.0)), 4326)),
    (12, 1, 3,  -5,   5, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.2,50.0)), 4326));

CREATE TABLE nuts2_regions (nuts2 text PRIMARY KEY, the_geom geometry(Geometry, 4326));
INSERT INTO nuts2_regions (nuts2, the_geom) VALUES
    ('A', ST_SetSRID(ST_MakePoint(0.0, 50.0), 4326)),
    ('B', ST_SetSRID(ST_MakePoint(0.2, 50.0), 4326));

CREATE TABLE freight_od_matrix (
    orig_nuts2 text, dest_nuts2 text, tonnes double precision, vintage int);
INSERT INTO freight_od_matrix VALUES ('A', 'B', 100, 2022);
"""


@pytest.fixture()
def oneway_engine():
    eng = create_engine(DB_URI)
    with eng.begin() as c:
        c.exec_driver_sql(SEED_ONEWAY)
    return eng


def test_routing_respects_one_way_edges(oneway_engine):
    snap_centroids(DB_URI)
    route_od_pairs(DB_URI, 2022)
    compute_edge_loads(DB_URI)
    with oneway_engine.connect() as c:
        loads = dict(c.execute(text("SELECT id, tonnes FROM edge_loads")).all())
    # the shorter direct edge 12 is one-way against travel; A->B must detour 1->2->3
    assert loads == {10: 100.0, 11: 100.0}


# node 9 (on a primary road, tag 106) sits right next to the region centroid,
# but nodes 1/2 are on a motorway (tag 101). Snapping must skip the closer
# primary node and pick the motorway node.
SEED_MAJOR = """
DROP TABLE IF EXISTS ways_vertices_pgr, ways, nuts2_regions, freight_od_matrix,
    centroid_nodes, od_routes, edge_loads CASCADE;

CREATE TABLE ways_vertices_pgr (id bigint PRIMARY KEY, geom geometry(Point, 4326));
INSERT INTO ways_vertices_pgr (id, geom) VALUES
    (1, ST_SetSRID(ST_MakePoint(0.0, 50.0), 4326)),
    (2, ST_SetSRID(ST_MakePoint(0.1, 50.0), 4326)),
    (9, ST_SetSRID(ST_MakePoint(0.02, 50.001), 4326));

CREATE TABLE ways (
    id bigint PRIMARY KEY, source bigint, target bigint,
    cost_s double precision, reverse_cost_s double precision,
    tag_id int, geom geometry(LineString, 4326));
INSERT INTO ways (id, source, target, cost_s, reverse_cost_s, tag_id, geom) VALUES
    (20, 1, 2, 10, 10, 101, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.1,50.0)), 4326)),
    (21, 1, 9, 10, 10, 106, ST_SetSRID(ST_MakeLine(ST_MakePoint(0.0,50.0), ST_MakePoint(0.02,50.001)), 4326));

CREATE TABLE nuts2_regions (nuts2 text PRIMARY KEY, the_geom geometry(Geometry, 4326));
INSERT INTO nuts2_regions (nuts2, the_geom) VALUES
    ('C', ST_SetSRID(ST_MakePoint(0.02, 50.0), 4326));
"""


@pytest.fixture()
def major_engine():
    eng = create_engine(DB_URI)
    with eng.begin() as c:
        c.exec_driver_sql(SEED_MAJOR)
    return eng


def test_centroid_snaps_to_major_road_not_closer_minor_node(major_engine):
    snap_centroids(DB_URI)
    with major_engine.connect() as c:
        node = c.execute(text("SELECT node FROM centroid_nodes WHERE nuts2='C'")).scalar()
    # node 9 (primary) is far closer to C, but only 1/2 are motorway -> snap to 1
    assert node == 1
