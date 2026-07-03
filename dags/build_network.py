import os
import sys
import logging

import pendulum
from airflow.decorators import dag, task
from airflow.sdk import Asset
from airflow.exceptions import AirflowException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from network import (
    GEOFABRIK, download_extract, filter_highways, merge_pbfs, load_topology,
    NETWORK_TOPOLOGY_URI,
)

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("NETWORK_DATA_DIR", "/tmp/network")
DB_URI = os.environ.get("NETWORK_DB_URI")
TOPOLOGY_ASSET = Asset(NETWORK_TOPOLOGY_URI)

default_args = {"retries": 3, "retry_delay": pendulum.duration(minutes=10)}


@dag(
    schedule="@monthly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["freight", "network"],
)
def build_network():

    @task
    def download(country):
        return download_extract(country, os.path.join(DATA_DIR, "raw"))

    @task
    def filter_country(src_path):
        country = os.path.splitext(os.path.basename(src_path))[0]
        dest = os.path.join(DATA_DIR, "filtered", f"{country}.osm.pbf")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        return filter_highways(src_path, dest)

    @task
    def merge(filtered_paths):
        dest = os.path.join(DATA_DIR, "network_five.osm.pbf")
        return merge_pbfs(filtered_paths, dest)

    @task(outlets=[TOPOLOGY_ASSET])
    def load(pbf_path):
        if not DB_URI:
            raise AirflowException("NETWORK_DB_URI must be set to build the routable topology")
        load_topology(pbf_path, DB_URI)
        log.info("routable topology loaded into Postgres from %s", pbf_path)

    raw = download.expand(country=list(GEOFABRIK))
    filtered = filter_country.expand(src_path=raw)
    load(merge(filtered))


dag = build_network()
