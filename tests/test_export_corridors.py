from export_corridors import corridor_sql


def test_corridor_sql_restricts_to_motorway_trunk():
    sql = corridor_sql()
    assert "tag_id IN (101, 102, 104, 105)" in sql  # motorway/trunk only, no primary
    assert "ST_AsGeoJSON" in sql and "ST_Simplify" in sql
    assert "el.tonnes >= :mt" in sql


def test_corridor_sql_accepts_custom_classes():
    sql = corridor_sql((101, 104))
    assert "tag_id IN (101, 104)" in sql
