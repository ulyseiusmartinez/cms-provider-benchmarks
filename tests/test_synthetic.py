from cmsbench import synthetic, transform


def test_deterministic_and_clean():
    a = synthetic.generate([2021, 2022], n_providers=500, seed=7)
    b = synthetic.generate([2021, 2022], n_providers=500, seed=7)
    assert a[2022].equals(b[2022])
    df, qc = transform.clean(a[2022], 2022, "synthetic")
    assert qc.rows_rejected == 0 and df["npi"].is_unique


def test_provider_overlap_between_years():
    d = synthetic.generate([2021, 2022], n_providers=1000, seed=1)
    overlap = len(set(d[2021]["npi"]) & set(d[2022]["npi"])) / 1000
    assert 0.8 < overlap < 1.0   # some churn, mostly persistent
