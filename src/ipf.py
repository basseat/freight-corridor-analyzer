import numpy as np
import pandas as pd


def ipf_matrix(row_targets, col_targets, row_blk, col_blk, block_targets,
               seed=None, max_iter=1000, tol=1e-6):
    T = seed.copy().astype(float) if seed is not None else np.outer(row_targets, col_targets)
    T[T < 0] = 0

    for it in range(max_iter):
        rs = T.sum(axis=1); rs[rs == 0] = 1
        T *= (row_targets / rs)[:, None]

        cs = T.sum(axis=0); cs[cs == 0] = 1
        T *= (col_targets / cs)[None, :]

        for (rb, cb), tgt in block_targets.items():
            mask = np.outer(row_blk == rb, col_blk == cb)
            cur = T[mask].sum()
            if cur > 0:
                T[mask] *= tgt / cur

        err = max(np.abs(T.sum(axis=1) - row_targets).max(),
                  np.abs(T.sum(axis=0) - col_targets).max())
        if err < tol:
            break
    return T, it + 1, err


def reconstruct_od(loading, unloading, country_pairs, seed_cost=None,
                   max_iter=1000, tol=1e-6, min_tonnes=1e-6):
    # loading/unloading: columns [nuts2, country, tonnes]
    # country_pairs: columns [orig, dest, tonnes]  (int'l off-diagonal + domestic diagonal)
    regions = sorted(set(loading.nuts2) | set(unloading.nuts2))
    idx = {r: k for k, r in enumerate(regions)}
    country = (loading.set_index('nuts2').country
               .combine_first(unloading.set_index('nuts2').country)).to_dict()

    O = np.zeros(len(regions)); D = np.zeros(len(regions))
    for _, r in loading.iterrows(): O[idx[r.nuts2]] = r.tonnes
    for _, r in unloading.iterrows(): D[idx[r.nuts2]] = r.tonnes

    countries = sorted(set(country_pairs.orig) | set(country_pairs.dest))
    F = {(a, b): 0.0 for a in countries for b in countries}
    for _, r in country_pairs.iterrows(): F[(r.orig, r.dest)] = r.tonnes

    # pre-balance regional marginals so each country's totals match the
    # country-pair universe (otherwise the three constraint sets are inconsistent)
    R = {c: sum(F[(c, b)] for b in countries) for c in countries}
    C = {c: sum(F[(a, c)] for a in countries) for c in countries}
    for c in countries:
        ri = [idx[r] for r in regions if country[r] == c]
        if O[ri].sum() > 0: O[ri] *= R[c] / O[ri].sum()
        if D[ri].sum() > 0: D[ri] *= C[c] / D[ri].sum()

    blk = np.array([country[r] for r in regions])
    block_targets = {(a, b): F[(a, b)] for a in countries for b in countries if F[(a, b)] > 0}

    seed = np.exp(-seed_cost) if seed_cost is not None else None
    T, iters, err = ipf_matrix(O, D, blk, blk, block_targets, seed, max_iter, tol)

    rows = [(regions[i], regions[j], T[i, j])
            for i in range(len(regions)) for j in range(len(regions))
            if T[i, j] > min_tonnes]
    od = pd.DataFrame(rows, columns=['orig_nuts2', 'dest_nuts2', 'tonnes'])
    od.attrs['iterations'] = iters
    od.attrs['marginal_error'] = err
    return od


if __name__ == '__main__':
    # synthetic 2-country / 4-region check: does IPF recover the marginals?
    loading = pd.DataFrame({
        'nuts2': ['A1', 'A2', 'B1', 'B2'],
        'country': ['A', 'A', 'B', 'B'],
        'tonnes': [70, 30, 40, 60],
    })
    unloading = pd.DataFrame({
        'nuts2': ['A1', 'A2', 'B1', 'B2'],
        'country': ['A', 'A', 'B', 'B'],
        'tonnes': [55, 45, 65, 35],
    })
    country_pairs = pd.DataFrame({
        'orig': ['A', 'A', 'B', 'B'],
        'dest': ['A', 'B', 'A', 'B'],
        'tonnes': [100.0, 40.0, 30.0, 80.0],
    })

    od = reconstruct_od(loading, unloading, country_pairs)
    print(f"converged in {od.attrs['iterations']} iters, "
          f"marginal error {od.attrs['marginal_error']:.2e}\n")

    mat = od.pivot(index='orig_nuts2', columns='dest_nuts2', values='tonnes').fillna(0)
    print(mat.round(2), "\n")

    # verify country-pair block totals are recovered exactly
    cty = {'A1': 'A', 'A2': 'A', 'B1': 'B', 'B2': 'B'}
    od['oc'] = od.orig_nuts2.map(cty); od['dc'] = od.dest_nuts2.map(cty)
    blocks = od.groupby(['oc', 'dc']).tonnes.sum()
    print("recovered country-pair blocks:")
    print(blocks.round(3).to_string())
    print("\ntargets: A->A 100, A->B 40, B->A 30, B->B 80")
