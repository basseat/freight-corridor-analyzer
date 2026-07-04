import time
import requests
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
        if "error" in doc:
            raise ValueError(f"{code}: {doc['error'][0].get('label')}")
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


REPORTER = "EU27_2020"  # the all-reporters aggregate; individual reporters get
                        # suppressed for confidentiality, so the aggregate is fuller


def _agg_region(df, region_col):
    # region_col (c_load/c_unload) is published at NUTS-3 for the EU27 aggregate
    # reporter; sum the NUTS-3 detail up to NUTS-2 (first 4 chars) for the five
    # countries, dropping ZZ extra-regio.
    df = df.dropna(subset=["value"]).copy()
    df = df[df["geo"] == REPORTER]
    df = df[df[region_col].str.len() >= 5]        # NUTS-3 detail rows
    df["nuts2"] = df[region_col].str[:4]
    df = df[~df["nuts2"].str.endswith("ZZ")]
    df["country"] = df["nuts2"].str[:2]
    df = df[df["country"].isin(FIVE)]
    g = df.groupby(["nuts2", "country"], as_index=False)["value"].sum()
    return g.rename(columns={"value": "tonnes"})


def _fetch_regional(code, year):
    return jsonstat_to_frame(fetch_dataset(code, {"unit": "THS_T", "time": year}))


def stage_loading(year):
    return _agg_region(_fetch_regional("road_go_ta_rl", year), "c_load")


def stage_unloading(year):
    return _agg_region(_fetch_regional("road_go_ta_ru", year), "c_unload")


def _load_unload_cols(df):
    load = [c for c in df.columns if "load" in c and "unload" not in c]
    unload = [c for c in df.columns if "unload" in c]
    return load[0], unload[0]


def assemble_country_pairs(ia_df, loading):
    # ia_rc: c_load/c_unload are countries, geo is the reporter. Sum over real
    # reporters; off-diagonal (international) restricted to the five, and the
    # domestic diagonal is derived as loaded_total - international_outbound so
    # it only depends on verified tables.
    ia = ia_df.dropna(subset=["value"]).copy()
    lc, uc = _load_unload_cols(ia)
    ia = ia[ia["geo"] == REPORTER]
    intl = ia[(~ia[lc].str.startswith("EU")) & (~ia[uc].str.startswith("EU"))
              & (ia[lc] != ia[uc])]
    bil = intl.groupby([lc, uc], as_index=False)["value"].sum()

    outbound = bil.groupby(lc)["value"].sum().to_dict()  # to anywhere abroad, incl. outside the 5
    loaded_total = loading.groupby("country")["tonnes"].sum().to_dict()

    off = bil[(bil[lc].isin(FIVE)) & (bil[uc].isin(FIVE))]
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
    # regional table shaped like road_go_ta_rl: region-of-loading in c_load at
    # NUTS-3, geo is the reporter; the EU27 aggregate reporter is the total, and
    # NUTS-3 rows sum up to NUTS-2.
    doc = {
        "id": ["c_load", "unit", "geo", "time"],
        "size": [5, 1, 2, 1],
        "dimension": {
            "c_load": {"category": {"index": {"DE111": 0, "DE112": 1, "FR101": 2, "DEZZZ": 3, "DE11": 4}}},
            "unit": {"category": {"index": {"THS_T": 0}}},
            "geo": {"category": {"index": {"EU27_2020": 0, "DE": 1}}},
            "time": {"category": {"index": {"2021": 0}}},
        },
        # flat index over [c_load,unit,geo,time] (strides 2,2,1,1); unit=time=0
        # DE111/DE112 by EU27 -> DE11=150; DE=reporter rows ignored; DEZZZ dropped;
        # DE11 (NUTS-2 aggregate row) ignored since we sum NUTS-3; FR101 by EU27 -> FR10=70
        "value": {"0": 100, "1": 90, "2": 50, "3": 45, "4": 70, "6": 8, "8": 999},
    }
    reg = _agg_region(jsonstat_to_frame(doc), "c_load")
    got = dict(zip(reg["nuts2"], reg["tonnes"]))
    assert got == {"DE11": 150.0, "FR10": 70.0}, got
    print("regional c_load (NUTS-3) -> NUTS-2 via EU27 aggregate reporter OK")
    print(reg.to_string(index=False), "\n")

    # country-pair assembly: ia_rc-shaped frame (c_load/c_unload = countries) + loading totals
    ia = pd.DataFrame({
        "c_load":   ["DE", "DE", "FR", "PL", "DE", "DE", "DE"],
        "c_unload": ["FR", "PL", "DE", "DE", "IT", "DE", "FR"],  # IT outside 5; DE->DE domestic; last row wrong reporter
        "geo":      ["EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "DE"],
        "value":    [30.0, 20.0, 25.0, 15.0, 50.0, 12.0, 777.0],
    })
    loading = pd.DataFrame({
        "nuts2": ["DE11", "FR10", "PL11"], "country": ["DE", "FR", "PL"],
        "tonnes": [200.0, 120.0, 90.0],
    })
    cp = assemble_country_pairs(ia, loading)
    d = {(r.orig, r.dest): r.tonnes for r in cp.itertuples()}
    assert d[("DE", "DE")] == 100.0, d          # loaded 200 - outbound(30+20+50)=100
    assert d[("DE", "FR")] == 30.0 and d[("PL", "DE")] == 15.0
    assert ("DE", "IT") not in d                # IT not in the five (off-diagonal), but counted in outbound
    print("country-pair assembly (EU27 reporter, derived domestic diagonal) OK")
    print(cp.sort_values(["orig", "dest"]).to_string(index=False))
