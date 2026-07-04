from export_corridors import corridor_sql, base_network_sql


def test_corridor_sql_restricts_to_motorway_trunk():
    sql = corridor_sql()
    assert "tag_id IN (101, 102, 104, 105)" in sql  # motorway/trunk only, no primary
    assert "ST_AsGeoJSON" in sql and "ST_Simplify" in sql
    assert "el.tonnes >= :mt" in sql


def test_corridor_sql_accepts_custom_classes():
    sql = corridor_sql((101, 104))
    assert "tag_id IN (101, 104)" in sql


def test_base_network_sql_dissolves_edges_no_tonnes():
    sql = base_network_sql()
    assert "tag_id IN (101, 102, 104, 105)" in sql
    assert "edge_loads" not in sql and "tonnes" not in sql  # every edge, unweighted
    assert "ST_LineMerge" in sql and "GROUP BY" in sql  # dissolved to one line per class
