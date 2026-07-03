import time
import requests
import numpy as np
import pandas as pd

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
FIVE = ["DE", "FR", "ES", "NL", "PL"]


def _url(code, filters):
    parts = [f"{BASE}/{code}?format=JSON&lang=EN"]
    for k, v in filters.items():
        vals = v if isinstance(v, (list, tuple)) else [v]
        parts += [f"{k}={x}" for x in vals]
    return "&".join(parts)


def fetch_dataset(code, filters, retries=6, wait=30):
    url = _url(code, filters)
    for _ in range(retries):
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        doc = r.json()
        if isinstance(doc.get("warning"), dict) and doc["warning"].get("status") == 413:
            time.sleep(wait)  # async: server is building the extract, retry
            continue
        return doc
    raise TimeoutError(f"{code} still asynchronous after {retries} attempts")


def jsonstat_to_frame(doc):
    dims = doc["id"]
    size = doc["size"]
    # row-major strides: last dimension varies fastest
    stride = [1] * len(dims)
    for k in range(len(dims) - 2, -1, -1):
        stride[k] = stride[k + 1] * size[k + 1]

    pos_to_code = {}
    for d in dims:
        idx = doc["dimension"][d]["category"]["index"]
        if isinstance(idx, dict):
            pos_to_code[d] = {p: c for c, p in idx.items()}  # invert code->pos
        else:
            pos_to_code[d] = dict(enumerate(idx))

    values = doc["value"]
    status = doc.get("status", {}) or {}
    items = values.items() if isinstance(values, dict) else enumerate(values)

    rows = []
    for key, val in items:
        if val is None:
            continue
        i = int(key)
        rec = {d: pos_to_code[d][(i // stride[k]) % size[k]] for k, d in enumerate(dims)}
        rec["value"] = val
        rec["flag"] = status.get(str(i))
        rows.append(rec)
    return pd.DataFrame(rows)


def _keep_totals(df):
    for dim, keep in [("nst07", "TOTAL"), ("tra_type", "TOTAL")]:
        if dim in df.columns:
            df = df[df[dim] == keep]
    return df


def _to_nuts2(df, geo_col="geo"):
    df = df.dropna(subset=["value"]).copy()
    df = df[df[geo_col].str.len() >= 4]          # drop country / NUTS-1 rows
    df["nuts2"] = df[geo_col].str[:4]            # NUTS-2 = first 4 chars of any deeper code
    df["country"] = df[geo_col].str[:2]
    df = df[df["country"].isin(FIVE)]
    g = df.groupby(["nuts2", "country"], as_index=False)["value"].sum()
    return g.rename(columns={"value": "tonnes"})


def _fetch_regional(code, year):
    doc = fetch_dataset(code, {"unit": "THS_T", "time": year, "geoLevel": "nuts2"})
    df = _keep_totals(jsonstat_to_frame(doc))
    if df.empty:  # some regional tables only expose NUTS-3; pull that and truncate up
        doc = fetch_dataset(code, {"unit": "THS_T", "time": year, "geoLevel": "nuts3"})
        df = _keep_totals(jsonstat_to_frame(doc))
    return df


def stage_loading(year):
    return _to_nuts2(_fetch_regional("road_go_ta_rl", year))


def stage_unloading(year):
    return _to_nuts2(_fetch_regional("road_go_ta_ru", year))


def _load_unload_cols(df):
    load = [c for c in df.columns if "load" in c and "unload" not in c]
    unload = [c for c in df.columns if "unload" in c]
    return load[0], unload[0]


def assemble_country_pairs(ia_df, loading):
    # off-diagonal (international) from ia_rc, diagonal (domestic) derived from
    # loaded_total - international_outbound so it only depends on verified tables
    ia = _keep_totals(ia_df).dropna(subset=["value"]).copy()
    lc, uc = _load_unload_cols(ia)
    bil = ia.groupby([lc, uc], as_index=False)["value"].sum()  # sum over reporters

    outbound = bil.groupby(lc)["value"].sum().to_dict()  # to anywhere, incl. outside the 5
    loaded_total = loading.groupby("country")["tonnes"].sum().to_dict()

    off = bil[(bil[lc].isin(FIVE)) & (bil[uc].isin(FIVE)) & (bil[lc] != bil[uc])]
    off = off.rename(columns={lc: "orig", uc: "dest", "value": "tonnes"})[["orig", "dest", "tonnes"]]

    diag = []
    for c in FIVE:
        dom = max(0.0, loaded_total.get(c, 0.0) - outbound.get(c, 0.0))
        diag.append((c, c, dom))
    diag = pd.DataFrame(diag, columns=["orig", "dest", "tonnes"])
    return pd.concat([off, diag], ignore_index=True)


def stage_country_pairs(year, loading):
    doc = fetch_dataset("road_go_ia_rc", {"unit": "THS_T", "time": year})
    return assemble_country_pairs(jsonstat_to_frame(doc), loading)


def stage_all(year):
    loading = stage_loading(year)
    unloading = stage_unloading(year)
    country_pairs = stage_country_pairs(year, loading)
    return loading, unloading, country_pairs


if __name__ == "__main__":
    # synthetic JSON-stat mirroring a regional table: dims [nst07, geo, time]
    doc = {
        "id": ["nst07", "geo", "time"],
        "size": [2, 4, 1],
        "dimension": {
            "nst07": {"category": {"index": {"TOTAL": 0, "GT01": 1}}},
            "geo": {"category": {"index": {"DE111": 0, "DE112": 1, "DE122": 2, "FR101": 3}}},
            "time": {"category": {"index": {"2022": 0}}},
        },
        # TOTAL: DE111=100, DE112=40, DE122=60, FR101=70 ; GT01: DE111=9 ; DE112 suppressed
        "value": {"0": 100, "1": 40, "2": 60, "3": 70, "4": 9},
        "status": {"5": "c"},
    }
    df = jsonstat_to_frame(doc)
    assert len(df) == 5 and df["value"].sum() == 279, "parser/index decode wrong"

    reg = _to_nuts2(_keep_totals(df))
    got = dict(zip(reg["nuts2"], reg["tonnes"]))
    assert got == {"DE11": 140.0, "DE12": 60.0, "FR10": 70.0}, got  # DE111+DE112 -> DE11
    print("parser + NUTS-3->NUTS-2 aggregation OK")
    print(reg.to_string(index=False), "\n")

    # country-pair assembly: ia_rc-shaped frame + loading totals
    ia = pd.DataFrame({
        "c_load":   ["DE", "DE", "FR", "PL", "DE"],
        "c_unload": ["FR", "PL", "DE", "DE", "IT"],  # IT is outside the 5 -> outbound only
        "nst07": "TOTAL", "tra_type": "TOTAL",
        "value": [30.0, 20.0, 25.0, 15.0, 50.0],
    })
    loading = pd.DataFrame({
        "nuts2": ["DE11", "FR10", "PL11"], "country": ["DE", "FR", "PL"],
        "tonnes": [200.0, 120.0, 90.0],
    })
    cp = assemble_country_pairs(ia, loading)
    d = {(r.orig, r.dest): r.tonnes for r in cp.itertuples()}
    # DE domestic = loaded 200 - outbound(30+20+50)=100 -> 100
    assert d[("DE", "DE")] == 100.0, d
    assert d[("DE", "FR")] == 30.0 and d[("PL", "DE")] == 15.0
    assert ("DE", "IT") not in d  # dropped: IT not in the five
    print("country-pair assembly (incl. derived domestic diagonal) OK")
    print(cp.sort_values(["orig", "dest"]).to_string(index=False))
