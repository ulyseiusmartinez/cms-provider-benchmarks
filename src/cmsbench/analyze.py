"""Analytics: turn the SQL views into a compact, structured findings object."""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

MIN_SPECIALTY_PROVIDERS = 30
MIN_MOVER_PROVIDERS = 100      # small specialties swing on noise and top the movers list
MIN_STATE_PROVIDERS = 20
OUTLIER_Z = 4.5
DRUG_DRIVEN_SHARE = 0.5
# CMS catch-all / billing-arrangement categories: not clinical specialties, so not "movers".
# Types that are mostly organizations (entity_code 'O': labs, ambulance, mass immunizers...) are
# dropped by the org-share rule below; these are the individual-coded catch-alls it misses.
NON_SPECIALTY_TYPES = ("Unknown Supplier/Provider Specialty", "Undefined Physician type",
                       "All Other Suppliers", "Centralized Flu", "Mass Immunizer Roster Biller")


def _df(engine: Engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def build_findings(engine: Engine, top_n: int = 5) -> dict[str, Any]:
    years = _df(engine, "SELECT DISTINCT data_year FROM bench.provider_year ORDER BY 1")["data_year"].tolist()
    if not years:
        raise RuntimeError("No data loaded; run the pipeline first.")
    latest = int(years[-1])
    prior = latest - 1 if (latest - 1) in years else None

    nat = _df(engine, "SELECT * FROM bench.v_national_year ORDER BY data_year")
    # benes is summed across providers, so a patient seen by 3 providers counts 3 times
    national = {int(r.data_year): {"providers": int(r.providers),
                                   "bene_provider_pairs": int(r.benes),
                                   "payment": float(r.payment),
                                   "payment_per_bene": float(r.payment_per_bene)}
                for r in nat.itertuples()}

    findings: dict[str, Any] = {
        "latest_year": latest, "prior_year": prior, "years_loaded": [int(y) for y in years],
        "national": national, "specialty_movers": [], "state_high": [], "state_low": [],
        "outlier_count": 0, "outliers_drug_driven": 0, "outliers_drug_suppressed": 0,
        "outliers": [], "thresholds": {
            "min_specialty_providers": MIN_SPECIALTY_PROVIDERS,
            "min_mover_providers": MIN_MOVER_PROVIDERS, "drug_driven_share": DRUG_DRIVEN_SHARE,
            "min_state_providers": MIN_STATE_PROVIDERS, "outlier_robust_z": OUTLIER_Z},
    }

    if prior:
        movers = _df(engine, """
            WITH org AS (
                SELECT provider_type, avg((entity_code = 'O')::int) AS org_share
                FROM bench.provider_year WHERE data_year = :y GROUP BY provider_type)
            SELECT provider_type, providers, payment, payment_yoy_pct,
                   median_ppb_yoy_pct, providers_yoy_pct
            FROM bench.v_specialty_yoy JOIN org USING (provider_type)
            WHERE data_year = :y AND providers >= :m
              AND org_share < 0.5 AND NOT (provider_type = ANY(:skip))
            ORDER BY abs(median_ppb_yoy_pct) DESC LIMIT :n  -- median: robust to outliers/churn""",
                     y=latest, m=MIN_MOVER_PROVIDERS, skip=list(NON_SPECIALTY_TYPES), n=top_n)
        findings["specialty_movers"] = movers.to_dict("records")

    states = _df(engine, """
        SELECT provider_type, state, providers, median_ppb, national_median_ppb, index_vs_national
        FROM bench.v_state_specialty_benchmark
        WHERE data_year = :y AND providers >= :m""", y=latest, m=MIN_STATE_PROVIDERS)
    findings["state_high"] = states.nlargest(top_n, "index_vs_national").to_dict("records")
    findings["state_low"] = states.nsmallest(top_n, "index_vs_national").to_dict("records")

    out = _df(engine, """
        SELECT npi, provider_type, state, total_benes, payment_per_bene,
               specialty_median_ppb, drug_share, robust_z
        FROM bench.v_provider_outliers
        WHERE data_year = :y AND robust_z >= :z ORDER BY robust_z DESC""", y=latest, z=OUTLIER_Z)
    findings["outlier_count"] = int(len(out))
    findings["outliers_drug_driven"] = int((out["drug_share"] >= DRUG_DRIVEN_SHARE).sum())
    findings["outliers_drug_suppressed"] = int(out["drug_share"].isna().sum())
    findings["outliers"] = out.head(10).to_dict("records")
    return _jsonable(findings)


def _jsonable(obj: Any) -> Any:
    import numpy as np
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if obj != obj else float(obj)
    return obj
