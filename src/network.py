import os
import subprocess
from urllib.parse import urlsplit

import requests

GEOFABRIK = {
    "DE": "germany",
    "FR": "france",
    "ES": "spain",
    "NL": "netherlands",
    "PL": "poland",
}
BASE_URL = "https://download.geofabrik.de/europe"
HIGHWAY_TAGS = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link"
MAPCONFIG = os.environ.get("NETWORK_MAPCONFIG", "/usr/share/osm2pgrouting/mapconfig.xml")
NETWORK_TOPOLOGY_URI = "postgres://network/ways_topology"


def extract_url(country):
    return f"{BASE_URL}/{GEOFABRIK[country]}-latest.osm.pbf"


def download_extract(country, dest_dir, chunk_size=1 << 20):
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{country}.osm.pbf")
    with requests.get(extract_url(country), stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size):
                f.write(chunk)
    return dest


def filter_cmd(src_pbf, dest_pbf):
    return ["osmium", "tags-filter", "--overwrite", "-o", dest_pbf, src_pbf, HIGHWAY_TAGS]


def filter_highways(src_pbf, dest_pbf):
    subprocess.run(filter_cmd(src_pbf, dest_pbf), check=True)
    return dest_pbf


def merge_cmd(pbf_paths, dest_pbf):
    return ["osmium", "merge", "--overwrite", "-o", dest_pbf, *pbf_paths]


def merge_pbfs(pbf_paths, dest_pbf):
    subprocess.run(merge_cmd(pbf_paths, dest_pbf), check=True)
    return dest_pbf


def osm2pgrouting_cmd(pbf_path, db_uri, mapconfig=None):
    u = urlsplit(db_uri)
    return [
        "osm2pgrouting", "-f", pbf_path, "-c", mapconfig or MAPCONFIG,
        "-h", u.hostname, "-p", str(u.port or 5432),
        "-U", u.username, "-W", u.password or "", "-d", u.path.lstrip("/"),
        "--clean",
    ]


def load_topology(pbf_path, db_uri, mapconfig=None):
    subprocess.run(osm2pgrouting_cmd(pbf_path, db_uri, mapconfig), check=True)
