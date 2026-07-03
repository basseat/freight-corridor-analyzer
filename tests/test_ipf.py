import pandas as pd
import pytest

from ipf import reconstruct_od

LOADING = pd.DataFrame({
    'nuts2': ['A1', 'A2', 'B1', 'B2'],
    'country': ['A', 'A', 'B', 'B'],
    'tonnes': [70, 30, 40, 60],
})
UNLOADING = pd.DataFrame({
    'nuts2': ['A1', 'A2', 'B1', 'B2'],
    'country': ['A', 'A', 'B', 'B'],
    'tonnes': [55, 45, 65, 35],
})
COUNTRY_PAIRS = pd.DataFrame({
    'orig': ['A', 'A', 'B', 'B'],
    'dest': ['A', 'B', 'A', 'B'],
    'tonnes': [100.0, 40.0, 30.0, 80.0],
})
CTY = {'A1': 'A', 'A2': 'A', 'B1': 'B', 'B2': 'B'}


def test_reconstruct_od_converges():
    od = reconstruct_od(LOADING, UNLOADING, COUNTRY_PAIRS)
    assert od.attrs['marginal_error'] < 1e-4


def test_reconstruct_od_recovers_country_pair_blocks():
    od = reconstruct_od(LOADING, UNLOADING, COUNTRY_PAIRS)
    od['oc'] = od.orig_nuts2.map(CTY)
    od['dc'] = od.dest_nuts2.map(CTY)
    blocks = od.groupby(['oc', 'dc']).tonnes.sum()
    assert blocks[('A', 'A')] == pytest.approx(100, abs=1e-2)
    assert blocks[('A', 'B')] == pytest.approx(40, abs=1e-2)
    assert blocks[('B', 'A')] == pytest.approx(30, abs=1e-2)
    assert blocks[('B', 'B')] == pytest.approx(80, abs=1e-2)
