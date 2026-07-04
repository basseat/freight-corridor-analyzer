import sys
import json

from sqlalchemy import create_engine, text

# the clean corridor layer: motorway + trunk only (primary segments near region
# centroids are the centroid-connector artifact, see README)
CORRIDOR_TAGS = (101, 102, 104, 105)


def corridor_sql(classes=CORRIDOR_TAGS):
    tags = ", ".join(str(int(t)) for t in classes)
    return f"""
SELECT w.id, coalesce(w.name, '') AS name, cfg.tag_value AS road_class,
       round(el.tonnes::numeric, 1) AS tonnes,
       ST_AsGeoJSON(ST_Simplify(el.geom, :simp)) AS geojson
FROM edge_loads el
JOIN ways w ON w.id = el.id
JOIN configuration cfg ON cfg.tag_id = w.tag_id
WHERE w.tag_id IN ({tags}) AND el.tonnes >= :mt
"""


def base_network_sql(classes=CORRIDOR_TAGS):
    # a faint backdrop so the coloured corridors read as continuous over the
    # dense mesh: dissolve every edge of a class into one merged line, so it's
    # a couple of marks (fast, small) rather than hundreds of thousands
    tags = ", ".join(str(int(t)) for t in classes)
    return f"""
SELECT cfg.tag_value AS road_class,
       ST_AsGeoJSON(ST_Simplify(ST_LineMerge(ST_Collect(w.geom)), :simp)) AS geojson
FROM ways w
JOIN configuration cfg ON cfg.tag_id = w.tag_id
WHERE w.tag_id IN ({tags})
GROUP BY cfg.tag_value
"""


def _write_geojson(db_uri, sql, params, out_path, props):
    # stream a GeoJSON FeatureCollection Tableau opens directly (Connect ->
    # Spatial file); props maps a row to its feature properties
    eng = create_engine(db_uri)
    n = 0
    with eng.connect().execution_options(stream_results=True) as c, open(out_path, "w") as f:
        f.write('{"type": "FeatureCollection", "features": [')
        for r in c.execute(text(sql), params):
            if r.geojson is None:  # ST_Simplify collapsed a sub-tolerance stub
                continue
            feat = {"type": "Feature", "geometry": json.loads(r.geojson), "properties": props(r)}
            f.write(("," if n else "") + json.dumps(feat))
            n += 1
        f.write("]}")
    return n


def export_corridors(db_uri, out_path, classes=CORRIDOR_TAGS, simplify=0.0003, min_tonnes=0.0):
    return _write_geojson(
        db_uri, corridor_sql(classes), {"simp": simplify, "mt": min_tonnes}, out_path,
        lambda r: {"id": r.id, "name": r.name, "road_class": r.road_class, "tonnes": float(r.tonnes)})


def export_base_network(db_uri, out_path, classes=(101, 104), simplify=0.0015):
    # main-line skeleton (motorway + trunk, no ramps) at a coarse tolerance —
    # it is only background context, so keep it lean
    return _write_geojson(
        db_uri, base_network_sql(classes), {"simp": simplify}, out_path,
        lambda r: {"road_class": r.road_class})


if __name__ == "__main__":
    db_uri, out_path = sys.argv[1], sys.argv[2]
    arg3 = sys.argv[3] if len(sys.argv) > 3 else ""
    if arg3 == "--base":
        n = export_base_network(db_uri, out_path)
    else:
        n = export_corridors(db_uri, out_path, min_tonnes=float(arg3) if arg3 else 0.0)
    print(f"wrote {n} features to {out_path}")
