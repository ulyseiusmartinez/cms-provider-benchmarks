import pandas as pd
import pytest

from cmsbench import extract, transform


def _raw(**over):
    base = {"npi": ["1", "2", "3", "4", "4"], "provider_type": ["Cardiology"] * 5,
            "state": ["FL"] * 5, "entity_code": ["I"] * 5,
            "total_benes": ["50", "20", "-5", "abc", "30"],
            "total_srvcs": ["100"] * 5, "total_submitted_charges": ["900"] * 5,
            "total_allowed": ["300"] * 5, "total_payment": ["240"] * 5,
            "avg_risk_score": ["1.1"] * 5}
    base.update(over)
    return pd.DataFrame(base)


def test_clean_rejects_bad_rows_and_reports_why():
    df, qc = transform.clean(_raw(), 2022, "csv")
    assert list(df["npi"]) == [1, 2, 4]
    assert qc.rejected["negative_or_zero"] == 1       # npi 3 (benes -5)
    assert qc.rejected["non_numeric_measure"] == 1    # first npi-4 row ('abc')
    assert qc.rows_in == 5 and qc.rows_out == len(df)
    assert (df["data_year"] == 2022).all() and (df["data_source"] == "csv").all()


def test_clean_dedupes_npi():
    raw = _raw(npi=["1", "1", "2", "3", "4"], total_benes=["50", "60", "20", "20", "20"])
    df, qc = transform.clean(raw, 2022, "csv")
    assert len(df) == 4 and qc.rejected == {"duplicate_npi": 1}


def test_clean_rejects_missing_keys():
    raw = _raw(npi=["1", None, "3", "4", "5"], state=["FL", "FL", "", "FL", "FL"],
               total_benes=["50"] * 5)
    df, qc = transform.clean(raw, 2022, "csv")
    assert qc.rejected["missing_key"] == 2 and len(df) == 3


def test_rename_requires_expected_columns():
    with pytest.raises(ValueError, match="missing expected columns"):
        extract._rename(pd.DataFrame({"Rndrng_NPI": [1]}))


def test_rename_maps_cms_columns():
    raw = pd.DataFrame({k: ["1"] for k in extract.COLUMN_MAP})
    out = extract._rename(raw)
    assert set(out.columns) == set(extract.COLUMN_MAP.values())
