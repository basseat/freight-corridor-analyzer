import os
import sys
import logging

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowException
from airflow.models import Variable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stage_clean import stage_all
from ipf import reconstruct_od

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("FREIGHT_DATA_DIR", "/tmp/freight")
DB_URI = os.environ.get("FREIGHT_DB_URI")  # unset -> load step is skipped
MAX_MARGINAL_ERROR = 1.0  # thousand tonnes; IPF must close the marginals below this

default_args = {"retries": 3, "retry_delay": pendulum.duration(minutes=5)}


@dag(
    schedule="@monthly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["freight", "eurostat"],
    params={"year": None, "force": False},
)
def freight_assignment():

    @task.short_circuit
    def check_release(**ctx):
        p = ctx["params"]
        # Eurostat annual road freight is typically finalised ~18 months out;
        # a robust variant reads max(time) from the SDMX structure endpoint.
        target = int(p["year"]) if p["year"] else pendulum.now("UTC").year - 2
        processed = int(Variable.get("freight_processed_vintage", default_var=0))
        if target <= processed and not p["force"]:
            log.info("vintage %s already processed; skipping", target)
            return False
        return {"year": target}

    @task(retries=3)
    def stage_eurostat(rel):
        year = rel["year"]
        out = os.path.join(DATA_DIR, str(year))
        os.makedirs(out, exist_ok=True)
        loading, unloading, country_pairs = stage_all(year)
        paths = {}
        for name, df in [("loading", loading), ("unloading", unloading),
                         ("country_pairs", country_pairs)]:
            path = os.path.join(out, f"{name}.parquet")
            df.to_parquet(path, index=False)
            paths[name] = path
        paths["year"] = year
        return paths

    @task
    def ipf_reconstruct(paths):
        import pandas as pd
        year = paths["year"]
        od = reconstruct_od(
            pd.read_parquet(paths["loading"]),
            pd.read_parquet(paths["unloading"]),
            pd.read_parquet(paths["country_pairs"]),
        )
        od_path = os.path.join(DATA_DIR, str(year), "od_matrix.parquet")
        od.to_parquet(od_path, index=False)
        return {
            "od_path": od_path,
            "year": year,
            "rows": len(od),
            "iterations": od.attrs.get("iterations"),
            "marginal_error": float(od.attrs.get("marginal_error", float("nan"))),
        }

    @task
    def validate_od(meta):
        if meta["rows"] == 0:
            raise AirflowException("empty O-D matrix")
        err = meta["marginal_error"]
        if not (err <= MAX_MARGINAL_ERROR):
            raise AirflowException(
                f"IPF did not converge: marginal error {err} > {MAX_MARGINAL_ERROR}")
        log.info("O-D matrix OK: %s rows, error %.2e, %s iters",
                 meta["rows"], err, meta["iterations"])
        return meta

    @task
    def load_od(meta):
        year = meta["year"]
        if not DB_URI:
            log.warning("FREIGHT_DB_URI unset; skipping Postgres load for %s", year)
            return
        import pandas as pd
        from sqlalchemy import create_engine, text
        engine = create_engine(DB_URI)
        od = pd.read_parquet(meta["od_path"])
        od["vintage"] = year
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS freight_od_matrix (
                    orig_nuts2 text, dest_nuts2 text,
                    tonnes double precision, vintage int
                )"""))
            conn.execute(text("DELETE FROM freight_od_matrix WHERE vintage = :v"),
                         {"v": year})  # idempotent per-vintage reload
            od.to_sql("freight_od_matrix", conn, if_exists="append", index=False)
        Variable.set("freight_processed_vintage", year)
        log.info("loaded %s O-D rows for vintage %s", len(od), year)

    rel = check_release()
    paths = stage_eurostat(rel)
    meta = ipf_reconstruct(paths)
    load_od(validate_od(meta))


dag = freight_assignment()
