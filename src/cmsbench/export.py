"""Export view data as CSVs that Power BI can ingest (or connect Power BI to Postgres directly)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

TABLES = {
    "specialty_year": "SELECT * FROM bench.v_specialty_year ORDER BY data_year, provider_type",
    "specialty_yoy": "SELECT * FROM bench.v_specialty_yoy ORDER BY data_year, provider_type",
    "state_specialty_benchmark": "SELECT * FROM bench.v_state_specialty_benchmark ORDER BY 1,2,3",
    "provider_outliers": "SELECT * FROM bench.v_provider_outliers WHERE robust_z >= 3 ORDER BY data_year, robust_z DESC",
    "national_year": "SELECT * FROM bench.v_national_year ORDER BY data_year",
}


def export_csvs(engine: Engine, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with engine.connect() as conn:
        for name, sql in TABLES.items():
            p = out_dir / f"{name}.csv"
            pd.read_sql(sql, conn).to_csv(p, index=False, encoding="utf-8-sig")
            paths.append(p)
    return paths
