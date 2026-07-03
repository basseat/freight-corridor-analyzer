import pandas as pd

from stage_clean import jsonstat_to_frame, _keep_totals, _to_nuts2, assemble_country_pairs

JSONSTAT_DOC = {
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


def test_jsonstat_to_frame_decodes_index():
    df = jsonstat_to_frame(JSONSTAT_DOC)
    assert len(df) == 5
    assert df["value"].sum() == 279


def test_nuts3_aggregates_to_nuts2():
    df = jsonstat_to_frame(JSONSTAT_DOC)
    reg = _to_nuts2(_keep_totals(df))
    got = dict(zip(reg["nuts2"], reg["tonnes"]))
    assert got == {"DE11": 140.0, "DE12": 60.0, "FR10": 70.0}  # DE111+DE112 -> DE11


def test_assemble_country_pairs_derives_domestic_diagonal():
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
    assert d[("DE", "DE")] == 100.0  # loaded 200 - outbound(30+20+50)=100
    assert d[("DE", "FR")] == 30.0
    assert d[("PL", "DE")] == 15.0
    assert ("DE", "IT") not in d  # dropped: IT not in the five
