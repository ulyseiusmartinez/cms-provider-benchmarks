"""Transform: type-coerce, validate, and quarantine bad rows."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

NUMERIC = ["total_benes", "total_srvcs", "total_submitted_charges",
           "total_allowed", "total_payment", "avg_risk_score", "drug_payment"]
FINAL_COLUMNS = ["data_year", "npi", "provider_type", "state", "entity_code",
                 "total_benes", "total_srvcs", "total_submitted_charges",
                 "total_allowed", "total_payment", "avg_risk_score", "drug_payment",
                 "data_source"]


@dataclass
class QC:
    rows_in: int = 0
    rows_out: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    @property
    def rows_rejected(self) -> int:
        return sum(self.rejected.values())


def clean(df: pd.DataFrame, year: int, source: str) -> tuple[pd.DataFrame, QC]:
    qc = QC(rows_in=len(df))
    df = df.copy()
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else pd.NA
    df["npi"] = pd.to_numeric(df["npi"], errors="coerce")
    for col in ("provider_type", "state", "entity_code"):
        df[col] = df[col].astype("string").str.strip() if col in df else pd.NA

    rules = {
        "missing_key": df["npi"].isna() | df["provider_type"].isna() | df["provider_type"].eq("")
                       | df["state"].isna() | df["state"].eq(""),
        "non_numeric_measure": df[["total_benes", "total_srvcs", "total_submitted_charges",
                                   "total_allowed", "total_payment"]].isna().any(axis=1),
    }
    bad = pd.Series(False, index=df.index)
    for name, mask in rules.items():
        mask = mask & ~bad
        qc.rejected[name] = int(mask.sum())
        bad |= mask

    neg = ((df[["total_benes", "total_srvcs", "total_allowed", "total_payment"]] < 0).any(axis=1)
           | (df["total_benes"] <= 0)) & ~bad
    qc.rejected["negative_or_zero"] = int(neg.sum())
    bad |= neg

    df = df[~bad]
    dup = df.duplicated(subset=["npi"], keep="first")
    qc.rejected["duplicate_npi"] = int(dup.sum())
    df = df[~dup]

    df["npi"] = df["npi"].astype("int64")
    df["total_benes"] = df["total_benes"].astype("int64")
    df["data_year"] = year
    df["data_source"] = source
    qc.rejected = {k: v for k, v in qc.rejected.items() if v}
    qc.rows_out = len(df)
    return df[FINAL_COLUMNS].reset_index(drop=True), qc
