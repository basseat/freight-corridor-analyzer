import pandas as pd

from stage_clean import jsonstat_to_frame, _agg_region, assemble_country_pairs

# regional table shaped like road_go_ta_rl: region-of-loading in c_load at
# NUTS-3, geo is the reporter; EU27_2020 is the all-reporters total.
REGIONAL_DOC = {
    "id": ["c_load", "unit", "geo", "time"],
    "size": [5, 1, 2, 1],
    "dimension": {
        "c_load": {"category": {"index": {"DE111": 0, "DE112": 1, "FR101": 2, "DEZZZ": 3, "DE11": 4}}},
        "unit": {"category": {"index": {"THS_T": 0}}},
        "geo": {"category": {"index": {"EU27_2020": 0, "DE": 1}}},
        "time": {"category": {"index": {"2021": 0}}},
    },
    # DE111/DE112 by EU27 -> DE11=150; DE reporter rows ignored; DEZZZ dropped;
    # DE11 NUTS-2 aggregate row ignored (we sum NUTS-3); FR101 by EU27 -> FR10=70
    "value": {"0": 100, "1": 90, "2": 50, "3": 45, "4": 70, "6": 8, "8": 999},
}


def test_jsonstat_decodes_multidim_index():
    df = jsonstat_to_frame(REGIONAL_DOC)
    assert len(df) == 7
    assert {"c_load", "geo", "value"} <= set(df.columns)
    assert df["value"].sum() == 100 + 90 + 50 + 45 + 70 + 8 + 999


def test_agg_region_sums_nuts3_to_nuts2_via_eu27_reporter():
    reg = _agg_region(jsonstat_to_frame(REGIONAL_DOC), "c_load")
    got = dict(zip(reg["nuts2"], reg["tonnes"]))
    # DE11 = DE111 + DE112 (EU27 reporter); DE-reporter rows, DEZZZ (extra-regio)
    # and the suppressed-style NUTS-2 aggregate row are all excluded
    assert got == {"DE11": 150.0, "FR10": 70.0}


def test_assemble_country_pairs_eu27_reporter_and_domestic_diagonal():
    ia = pd.DataFrame({
        "c_load":   ["DE", "DE", "FR", "PL", "DE", "DE", "DE"],
        "c_unload": ["FR", "PL", "DE", "DE", "IT", "DE", "FR"],  # IT outside 5; DE->DE; last row wrong reporter
        "geo":      ["EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "EU27_2020", "DE"],
        "value":    [30.0, 20.0, 25.0, 15.0, 50.0, 12.0, 777.0],
    })
    loading = pd.DataFrame({
        "nuts2": ["DE11", "FR10", "PL11"], "country": ["DE", "FR", "PL"],
        "tonnes": [200.0, 120.0, 90.0],
    })
    cp = assemble_country_pairs(ia, loading)
    d = {(r.orig, r.dest): r.tonnes for r in cp.itertuples()}
    assert d[("DE", "DE")] == 100.0  # loaded 200 - outbound(30+20+50)=100
    assert d[("DE", "FR")] == 30.0
    assert d[("PL", "DE")] == 15.0
    assert ("DE", "IT") not in d      # IT not in the five (off-diagonal); still counts as outbound
