"""Extract: CMS Data API, a downloaded CSV, or (see synthetic.py) generated data.

Targets the "Medicare Physician & Other Practitioners - by Provider" dataset.
Column names below follow CMS's data dictionary; verify against the year you
download, since CMS occasionally renames fields.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

API_BASE = "https://data.cms.gov/data-api/v1/dataset"

COLUMN_MAP = {
    "Rndrng_NPI": "npi",
    "Rndrng_Prvdr_Type": "provider_type",
    "Rndrng_Prvdr_State_Abrvtn": "state",
    "Rndrng_Prvdr_Ent_Cd": "entity_code",
    "Tot_Benes": "total_benes",
    "Tot_Srvcs": "total_srvcs",
    "Tot_Sbmtd_Chrg": "total_submitted_charges",
    "Tot_Mdcr_Alowd_Amt": "total_allowed",
    "Tot_Mdcr_Pymt_Amt": "total_payment",
    "Bene_Avg_Risk_Scre": "avg_risk_score",
    "Drug_Mdcr_Pymt_Amt": "drug_payment",   # optional; blank when CMS suppresses small cells
}
REQUIRED = ["npi", "provider_type", "state", "total_benes", "total_srvcs",
            "total_submitted_charges", "total_allowed", "total_payment"]


def _rename(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=COLUMN_MAP)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"Source is missing expected columns {missing}. "
            "Check COLUMN_MAP against the CMS data dictionary for this year."
        )
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    return df[keep].copy()


def fetch_row_count(dataset_id: str, session: requests.Session | None = None) -> int | None:
    """Total rows per the CMS /data/stats endpoint, or None if the response shape is unfamiliar."""
    s = session or requests.Session()
    try:
        resp = s.get(f"{API_BASE}/{dataset_id}/data/stats", timeout=60)
        resp.raise_for_status()
        return _find_key(resp.json(), ("total_rows", "found_rows"))
    except (requests.RequestException, ValueError) as exc:
        log.warning("could not read row count for %s: %s", dataset_id, exc)
        return None


def _find_key(obj, keys: tuple[str, ...]) -> int | None:
    """Depth-first search for the first integer-like value under any of `keys`."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                try:
                    return int(obj[k])
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            hit = _find_key(v, keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, keys)
            if hit is not None:
                return hit
    return None


def _get_page(s: requests.Session, url: str, params: dict, retries: int) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = s.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 400 or attempt == retries - 1:   # 400 = our request is wrong; retrying won't help
                raise
            wait = 2 ** attempt
            log.warning("CMS API error (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_api(dataset_id: str, page_size: int = 5000, max_rows: int | None = None,
              session: requests.Session | None = None, retries: int = 4,
              select_columns: bool = True, verify_count: bool = True) -> pd.DataFrame:
    """Page through the CMS data API for one dataset (one year).

    page_size is capped at 5,000 by CMS. With select_columns, only the fields we use are
    requested (the full file has ~70 columns); if CMS rejects that (HTTP 400, e.g. a renamed
    field) we retry once without it. When the whole file is read, the row count is checked
    against /data/stats so a silently truncated download can't be loaded.
    """
    s = session or requests.Session()
    page_size = min(page_size, 5000)
    url = f"{API_BASE}/{dataset_id}/data"
    base_params: dict = {"size": page_size}
    if select_columns:
        base_params["column"] = ",".join(COLUMN_MAP)

    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        params = {**base_params, "offset": offset}
        try:
            resp = _get_page(s, url, params, retries)
        except requests.HTTPError as exc:
            if "column" in base_params and getattr(exc.response, "status_code", None) == 400 and offset == 0:
                log.warning("CMS rejected column selection (%s); retrying with all columns", exc)
                base_params.pop("column")
                continue
            raise
        rows = resp.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        offset += len(rows)
        log.info("fetched %s rows from %s", offset, dataset_id)
        if max_rows and offset >= max_rows:
            break
        if len(rows) < page_size:
            break
    if not frames:
        raise RuntimeError(f"CMS API returned no rows for dataset {dataset_id}")
    df = pd.concat(frames, ignore_index=True)

    if verify_count and not max_rows:
        expected = fetch_row_count(dataset_id, s)
        if expected is not None and expected != len(df):
            raise RuntimeError(f"Incomplete download for {dataset_id}: got {len(df)} rows, "
                               f"CMS reports {expected}")
    if max_rows:
        df = df.head(max_rows)
    return _rename(df)


def read_csv(path: str | Path) -> pd.DataFrame:
    """Read a CSV downloaded from data.cms.gov (original CMS column names)."""
    return _rename(pd.read_csv(path, dtype=str))
