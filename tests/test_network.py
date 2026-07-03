from network import extract_url, filter_cmd, merge_cmd, osm2pgrouting_cmd


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_extract_url_maps_country_to_geofabrik_slug():
    assert extract_url("DE") == "https://download.geofabrik.de/europe/germany-latest.osm.pbf"
    assert extract_url("PL") == "https://download.geofabrik.de/europe/poland-latest.osm.pbf"


def test_filter_cmd_keeps_only_major_highways():
    cmd = filter_cmd("in.osm.pbf", "out.osm.pbf")
    assert cmd[0] == "osmium" and "tags-filter" in cmd
    tags = cmd[-1]
    assert "motorway" in tags and "trunk" in tags and "primary" in tags
    assert val(cmd, "-o") == "out.osm.pbf"


def test_merge_cmd_includes_all_inputs():
    cmd = merge_cmd(["a.osm.pbf", "b.osm.pbf"], "merged.osm.pbf")
    assert cmd[:2] == ["osmium", "merge"]
    assert "a.osm.pbf" in cmd and "b.osm.pbf" in cmd
    assert val(cmd, "-o") == "merged.osm.pbf"


def test_osm2pgrouting_cmd_parses_db_uri():
    cmd = osm2pgrouting_cmd("net.osm.pbf", "postgresql://user:pw@dbhost:5433/freight")
    assert cmd[0] == "osm2pgrouting"
    assert val(cmd, "-h") == "dbhost"
    assert val(cmd, "-p") == "5433"
    assert val(cmd, "-U") == "user"
    assert val(cmd, "-W") == "pw"
    assert val(cmd, "-d") == "freight"
