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


def export_corridors(db_uri, out_path, classes=CORRIDOR_TAGS, simplify=0.0003, min_tonnes=0.0):
    # stream a GeoJSON FeatureCollection Tableau can open directly (Connect ->
    # Spatial file). simplify is a degree tolerance (~0.0003 deg ~ 30 m).
    eng = create_engine(db_uri)
    n = 0
    with eng.connect().execution_options(stream_results=True) as c, open(out_path, "w") as f:
        f.write('{"type": "FeatureCollection", "features": [')
        for r in c.execute(text(corridor_sql(classes)), {"simp": simplify, "mt": min_tonnes}):
            feat = {
                "type": "Feature",
                "geometry": json.loads(r.geojson),
                "properties": {"id": r.id, "name": r.name,
                               "road_class": r.road_class, "tonnes": float(r.tonnes)},
            }
            f.write(("," if n else "") + json.dumps(feat))
            n += 1
        f.write("]}")
    return n


if __name__ == "__main__":
    db_uri, out_path = sys.argv[1], sys.argv[2]
    min_tonnes = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    n = export_corridors(db_uri, out_path, min_tonnes=min_tonnes)
    print(f"wrote {n} corridor features to {out_path}")
