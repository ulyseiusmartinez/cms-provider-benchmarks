import pandas as pd
import pytest
import requests

from cmsbench import extract


def _row(i):
    return {"Rndrng_NPI": str(1000 + i), "Rndrng_Prvdr_Type": "Cardiology", "Rndrng_Prvdr_State_Abrvtn": "FL",
            "Rndrng_Prvdr_Ent_Cd": "I", "Tot_Benes": "40", "Tot_Srvcs": "90", "Tot_Sbmtd_Chrg": "900",
            "Tot_Mdcr_Alowd_Amt": "300", "Tot_Mdcr_Pymt_Amt": "240", "Bene_Avg_Risk_Scre": "1.1"}


class FakeResp:
    def __init__(self, rows, status=200): self._rows, self.status_code = rows, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)   # like real requests
    def json(self): return self._rows


class FakeSession:
    def __init__(self, total, fail_first=0, stats_total="match", reject_columns=False):
        self.total, self.fail_first, self.calls = total, fail_first, []
        self.stats_total = total if stats_total == "match" else stats_total
        self.reject_columns, self.params_seen = reject_columns, []
    def get(self, url, params=None, timeout=None):
        if url.endswith("/data/stats"):
            if self.stats_total is None:
                return FakeResp({"unexpected": "shape"})
            return FakeResp({"data": {"found_rows": self.stats_total, "total_rows": self.stats_total}})
        self.params_seen.append(dict(params))
        if self.reject_columns and "column" in params:
            return FakeResp([], 400)
        self.calls.append(params["offset"])
        if self.fail_first:
            self.fail_first -= 1
            return FakeResp([], 503)
        o, n = params["offset"], params["size"]
        return FakeResp([_row(i) for i in range(o, min(o + n, self.total))])


def test_pagination_stops_on_short_page():
    s = FakeSession(total=25)
    df = extract.fetch_api("abc", page_size=10, session=s)
    assert len(df) == 25 and s.calls == [0, 10, 20] and "npi" in df.columns


def test_pagination_stops_on_empty_page():
    s = FakeSession(total=20)
    assert len(extract.fetch_api("abc", page_size=10, session=s)) == 20


def test_max_rows_caps():
    assert len(extract.fetch_api("abc", page_size=10, max_rows=15, session=FakeSession(100))) == 15


def test_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    s = FakeSession(total=5, fail_first=2)
    assert len(extract.fetch_api("abc", page_size=10, session=s)) == 5


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        extract.fetch_api("abc", session=FakeSession(5, fail_first=99), retries=3)


def test_empty_dataset_raises():
    with pytest.raises(RuntimeError, match="no rows"):
        extract.fetch_api("abc", session=FakeSession(0))


def test_read_csv_roundtrip(tmp_path):
    p = tmp_path / "x.csv"
    pd.DataFrame([_row(1), _row(2)]).to_csv(p, index=False)
    df = extract.read_csv(p)
    assert list(df["npi"]) == ["1001", "1002"] and "total_payment" in df.columns


def test_requests_only_needed_columns_and_caps_page_size():
    s = FakeSession(total=3)
    extract.fetch_api("abc", page_size=99999, session=s)
    assert s.params_seen[0]["size"] == 5000
    assert set(s.params_seen[0]["column"].split(",")) == set(extract.COLUMN_MAP)


def test_falls_back_to_all_columns_when_cms_rejects_selection():
    s = FakeSession(total=3, reject_columns=True)
    assert len(extract.fetch_api("abc", session=s)) == 3
    assert "column" in s.params_seen[0] and "column" not in s.params_seen[-1]


def test_incomplete_download_is_rejected():
    with pytest.raises(RuntimeError, match="Incomplete download"):
        extract.fetch_api("abc", session=FakeSession(total=5, stats_total=9))


def test_unknown_stats_shape_is_tolerated():
    assert len(extract.fetch_api("abc", session=FakeSession(total=5, stats_total=None))) == 5


def test_count_check_skipped_when_sampling():
    s = FakeSession(total=50, stats_total=9999)
    assert len(extract.fetch_api("abc", page_size=10, max_rows=20, session=s)) == 20
