import os
import sys
import logging

import pendulum
from airflow.decorators import dag, task
from airflow.sdk import Asset, get_current_context
from airflow.exceptions import AirflowException
from airflow.models import Variable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from network import NETWORK_TOPOLOGY_URI
from routing import load_nuts2_regions, snap_centroids, route_od_pairs, compute_edge_loads

log = logging.getLogger(__name__)

# the O-D matrix (freight_od_matrix) and the routable network (ways,
# ways_vertices_pgr) must live in the same database to be joined in-SQL
DB_URI = os.environ.get("ROUTING_DB_URI") or os.environ.get("NETWORK_DB_URI")
NUTS_YEAR = int(os.environ.get("NUTS_YEAR", "2021"))
TOPOLOGY_ASSET = Asset(NETWORK_TOPOLOGY_URI)

default_args = {"retries": 3, "retry_delay": pendulum.duration(minutes=5)}


@dag(
    schedule=[TOPOLOGY_ASSET],  # runs when build_network refreshes the topology
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["freight", "routing"],
    params={"vintage": None},
)
def route_freight():

    @task
    def map_centroids():
        if not DB_URI:
            raise AirflowException("ROUTING_DB_URI / NETWORK_DB_URI must be set")
        n = load_nuts2_regions(DB_URI, NUTS_YEAR)
        snap_centroids(DB_URI)
        log.info("snapped %s NUTS-2 centroids to network nodes", n)
        return n

    @task
    def route(_centroids):
        p = get_current_context()["params"]
        vintage = p["vintage"] or int(Variable.get("freight_processed_vintage", default_var=0))
        if not vintage:
            raise AirflowException("no vintage to route: set params.vintage or run freight_assignment first")
        route_od_pairs(DB_URI, vintage)
        log.info("routed O-D pairs for vintage %s", vintage)
        return vintage

    @task
    def edge_loads(vintage):
        compute_edge_loads(DB_URI)
        log.info("edge loads computed for vintage %s", vintage)
        return {"vintage": vintage}

    edge_loads(route(map_centroids()))


dag = route_freight()
