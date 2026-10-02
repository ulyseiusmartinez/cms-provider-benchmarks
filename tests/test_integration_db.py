"""Needs a real Postgres: set DATABASE_URL (e.g. the docker-compose db). Skipped otherwise."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine():
    from cmsbench import analyze, load, synthetic, transform
    eng = load.get_engine(os.environ["DATABASE_URL"])
    load.init_db(eng)
    frames = synthetic.generate([2021, 2022], n_providers=3000, seed=3)
    for y, f in frames.items():
        df, _ = transform.clean(f, y, "synthetic")
        load.load_year(eng, df, y)
    return eng


def test_load_is_idempotent(engine):
    from cmsbench import load, synthetic, transform
    from sqlalchemy import text
    f = synthetic.generate([2021, 2022], n_providers=3000, seed=3)[2022]  # same as fixture
    df, _ = transform.clean(f, 2022, "synthetic")
    load.load_year(engine, df, 2022)
    with engine.connect() as c:
        assert c.execute(text("select count(*) from bench.provider_year where data_year=2022")).scalar() == len(df)


def test_findings_recover_planted_effects(engine):
    from cmsbench import analyze
    f = analyze.build_findings(engine)
    assert f["latest_year"] == 2022 and f["prior_year"] == 2021
    top = {m["provider_type"]: m for m in f["specialty_movers"]}
    assert "Nurse Practitioner" in top and top["Nurse Practitioner"]["median_ppb_yoy_pct"] > 0.05
    assert 5 <= f["outlier_count"] <= 25   # ~0.4% of 3000 planted, most detectable
    assert all(o["robust_z"] >= f["thresholds"]["outlier_robust_z"] for o in f["outliers"])


def test_outlier_view_needs_min_peers(engine):
    from sqlalchemy import text
    with engine.connect() as c:
        n = c.execute(text("select count(*) from bench.v_provider_outliers where peers < 30")).scalar()
    assert n == 0
