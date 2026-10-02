import pytest

from cmsbench import discover, pipeline

U22 = "11111111-aaaa-bbbb-cccc-222222222222"
U21 = "33333333-aaaa-bbbb-cccc-444444444444"
UOTHER = "55555555-aaaa-bbbb-cccc-666666666666"

# Shaped after the DCAT-US structure described in CMS's API guide (not a live capture).
CATALOG = {"dataset": [
    {"title": "Medicare Physician & Other Practitioners - by Provider",
     "distribution": [
         {"title": "Medicare Physician & Other Practitioners - by Provider : 2022-12-31",
          "temporal": "2022-01-01/2022-12-31", "format": "API",
          "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{U22}/data"},
         {"title": "Medicare Physician & Other Practitioners - by Provider : 2021-12-31",
          "format": "API", "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{U21}/data"},
         {"title": "CSV 2022", "downloadURL": "https://example.com/x.csv"},   # no API url
     ]},
    {"title": "Medicare Physician & Other Practitioners - by Provider and Service",
     "distribution": [{"temporal": "2022-01-01/2022-12-31",
                       "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{UOTHER}/data"}]},
]}


def test_finds_exact_title_only_and_years():
    c = discover.find_datasets(CATALOG)
    assert [(x.year, x.dataset_id) for x in c] == [(2021, U21), (2022, U22)]   # not "...and Service"


def test_title_matching_is_punctuation_tolerant():
    assert len(discover.find_datasets(CATALOG, "medicare physician and other practitioners – by provider")) == 2


def test_to_env_roundtrips_through_config_parser():
    from cmsbench.config import parse_datasets
    env = discover.to_env(discover.find_datasets(CATALOG))
    assert parse_datasets(env) == {2021: U21, 2022: U22}


def test_resolve_prefers_configured_and_discovers_the_rest(monkeypatch):
    monkeypatch.setattr(pipeline.discover, "fetch_catalog", lambda: CATALOG)
    out = pipeline.resolve_datasets([2021, 2022], {2022: "override"})
    assert out == {2021: U21, 2022: "override"}


def test_resolve_skips_network_when_fully_configured(monkeypatch):
    def boom(): raise AssertionError("catalog should not be fetched")
    monkeypatch.setattr(pipeline.discover, "fetch_catalog", boom)
    assert pipeline.resolve_datasets([2022], {2022: "x"}) == {2022: "x"}


def test_resolve_errors_clearly_on_unknown_year(monkeypatch):
    monkeypatch.setattr(pipeline.discover, "fetch_catalog", lambda: CATALOG)
    with pytest.raises(SystemExit, match="2019"):
        pipeline.resolve_datasets([2019], {})


# Live data.cms.gov layout (checked 2026-10-02): one dataset per year, release date in the title,
# temporal as a list of periods.
U23 = "77777777-aaaa-bbbb-cccc-888888888888"
LIVE_CATALOG = {"dataset": [
    {"title": "Medicare Physician & Other Practitioners - by Provider : 2023-12-31",
     "temporal": [{"@type": "PeriodOfTime", "startDate": "2023-01-01", "endDate": "2023-12-31"}],
     "distribution": [
         {"format": "CSV", "downloadURL": "https://data.cms.gov/x.csv"},
         {"format": "API", "title": "Medicare Physician & Other Practitioners - by Provider : 2023-12-31",
          "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{U23}/data"}]},
    {"title": "Medicare Physician & Other Practitioners - by Provider : 2022-12-02",
     "temporal": [{"@type": "PeriodOfTime", "startDate": "2022-01-01", "endDate": "2022-12-31"}],
     "distribution": [{"format": "API", "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{U22}/data"}]},
    {"title": "Medicare Physician & Other Practitioners - by Provider and Service : 2022-12-02",
     "temporal": [{"@type": "PeriodOfTime", "startDate": "2022-01-01", "endDate": "2022-12-31"}],
     "distribution": [{"format": "API", "accessURL": f"https://data.cms.gov/data-api/v1/dataset/{UOTHER}/data"}]},
]}


def test_live_layout_one_dataset_per_year():
    c = discover.find_datasets(LIVE_CATALOG)
    assert [(x.year, x.dataset_id) for x in c] == [(2022, U22), (2023, U23)]
