"""Find dataset ids (UUIDs) per year from the public data.cms.gov catalog (data.json, DCAT-US).

Per CMS's API guide, each dataset lists API endpoints under distribution[].accessURL, of the
form  https://data.cms.gov/data-api/v1/dataset/<uuid>/data .  The live catalog lists each year as
its own dataset titled "<name> : <release date>" with temporal [{"startDate": ...}]; older
DCAT layouts put one distribution per release under a single dataset. Both are handled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

CATALOG_URL = "https://data.cms.gov/data.json"
DEFAULT_TITLE = "Medicare Physician & Other Practitioners - by Provider"
_UUID_URL = re.compile(r"/data-api/v1/dataset/([0-9a-fA-F-]{36})/data/?$")
_YEAR = re.compile(r"\b(20\d{2})\b")


@dataclass(frozen=True)
class Candidate:
    year: int
    dataset_id: str
    title: str


def fetch_catalog(session: requests.Session | None = None) -> dict:
    resp = (session or requests.Session()).get(CATALOG_URL, timeout=180)
    resp.raise_for_status()
    return resp.json()


def _norm(t: str) -> str:
    t = re.sub(r"\s+:\s+\d{4}-\d{2}-\d{2}\s*$", "", t)   # drop " : 2022-12-02" release suffix
    return re.sub(r"[\s–—-]+", " ", t.replace("&", "and")).strip().lower()


def _temporal_text(temporal) -> str:
    """temporal may be "2022-01-01/2022-12-31" or [{"startDate": "2022-01-01", "endDate": ...}]."""
    if isinstance(temporal, list):
        return " ".join(str(p.get("startDate", "")) for p in temporal if isinstance(p, dict))
    return temporal or ""


def _year_of(dist: dict, dataset: dict) -> int | None:
    # data period first; titles carry the release date, which can fall in a later year
    for text in (_temporal_text(dist.get("temporal")), _temporal_text(dataset.get("temporal")),
                 dist.get("title"), dist.get("description")):
        years = _YEAR.findall(text or "")
        if years:
            return int(years[-1])      # "2022-01-01/2022-12-31" or "... : 2022-12-31" -> 2022
    return None


def find_datasets(catalog: dict, title: str = DEFAULT_TITLE) -> list[Candidate]:
    """Return one Candidate per (year) API distribution of datasets whose title matches exactly."""
    want = _norm(title)
    out: dict[int, Candidate] = {}
    for ds in catalog.get("dataset", []):
        if _norm(ds.get("title", "")) != want:
            continue
        for dist in ds.get("distribution", []) or []:
            m = _UUID_URL.search(dist.get("accessURL") or "")
            if not m:
                continue
            year = _year_of(dist, ds)
            if year is None:
                continue
            out[year] = Candidate(year, m.group(1), ds.get("title", ""))
    return [out[y] for y in sorted(out)]


def to_env(cands: list[Candidate]) -> str:
    return ",".join(f"{c.year}:{c.dataset_id}" for c in cands)
