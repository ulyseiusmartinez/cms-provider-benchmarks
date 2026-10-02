import json

from cmsbench import summarize
from cmsbench.config import parse_datasets
import pytest

F = {
    "latest_year": 2022, "prior_year": 2021, "years_loaded": [2021, 2022],
    "national": {"2022": {"providers": 100, "bene_provider_pairs": 5000, "payment": 3.2e6, "payment_per_bene": 640.0}},
    "specialty_movers": [{"provider_type": "Cardiology", "providers": 40, "payment": 1e6,
                          "payment_yoy_pct": 0.1, "median_ppb_yoy_pct": 0.05, "providers_yoy_pct": 0.02}],
    "state_high": [{"provider_type": "Cardiology", "state": "FL", "providers": 25,
                    "median_ppb": 1500.0, "national_median_ppb": 1200.0, "index_vs_national": 1.25}],
    "state_low": [], "outlier_count": 2,
    "outliers": [{"npi": 1234567890, "provider_type": "Cardiology", "state": "FL", "total_benes": 20,
                  "payment_per_bene": 9000.0, "specialty_median_ppb": 1200.0, "robust_z": 6.0}],
    "thresholds": {"outlier_robust_z": 4.5},
}


def test_template_narrative_mentions_key_numbers():
    t = summarize.template_narrative(F)
    assert "100 providers" in t and "Cardiology" in t and "+5.0%" in t and "+10.0%" in t and "not findings" in t


def test_report_flags_synthetic_and_renders_tables():
    r = summarize.render_report(F, "narr", "template", "synthetic")
    assert "Synthetic demo data" in r and "| Cardiology | FL |" in r and "1234567890" in r


def test_llm_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(summarize, "llm_narrative", boom)
    text, method = summarize.narrative(F, "sk-test", "m")
    assert method == "template" and text


def test_llm_payload_excludes_provider_detail(monkeypatch):
    captured = {}

    class FakeMsgs:
        def create(self, **kw):
            captured.update(kw)
            class B: type = "text"; text = "ok"
            class R: content = [B()]
            return R()

    class FakeClient:
        def __init__(self, **kw): self.messages = FakeMsgs()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    assert summarize.llm_narrative(F, "k", "m") == "ok"
    payload = json.loads(captured["messages"][0]["content"])
    assert "outliers" not in payload and "1234567890" not in captured["messages"][0]["content"]


def test_parse_datasets():
    assert parse_datasets("2022:abc, 2021:def") == {2022: "abc", 2021: "def"}
    assert parse_datasets("") == {}
    with pytest.raises(ValueError):
        parse_datasets("2022")


def test_national_line_does_not_claim_unique_beneficiaries():
    t = summarize.template_narrative(F)
    assert "beneficiaries" not in t.split(".")[0] and "per patient seen" in t


def test_outliers_report_drug_share():
    f = {**F, "outlier_count": 3, "outliers_drug_driven": 2, "outliers_drug_suppressed": 1,
         "thresholds": {"outlier_robust_z": 4.5, "drug_driven_share": 0.5},
         "outliers": [{**F["outliers"][0], "drug_share": 0.9},
                      {**F["outliers"][0], "npi": 1999999999, "drug_share": None}]}
    assert "2 mainly from Part B drug payments" in summarize.template_narrative(f)
    r = summarize.render_report(f, "narr", "template", "api")
    assert "| 90% |" in r and "| — |" in r


def test_no_drug_clause_when_drug_detail_absent():
    f = {**F, "outliers_drug_driven": 0, "outliers_drug_suppressed": 2}   # e.g. synthetic data
    assert "Part B" not in summarize.template_narrative(f)


def test_truncated_llm_reply_falls_back_to_template(monkeypatch):
    class FakeMsgs:
        def create(self, **kw):
            class B: type = "text"; text = "In 2024, Hematology-Oncology in"
            class R: content = [B()]; stop_reason = "max_tokens"
            return R()

    class FakeClient:
        def __init__(self, **kw): self.messages = FakeMsgs()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    text, method = summarize.narrative(F, "k", "m")
    assert method == "template" and not text.endswith(" in")


def test_facts_are_labeled_and_exclude_provider_detail():
    facts = json.dumps(summarize._facts(F))
    assert "Cardiology: median payment per patient +5.0%" in facts
    assert "1234567890" not in facts and "outliers" not in summarize._facts(F)


def test_unsupported_percentages_flags_invented_figures():
    facts = json.dumps(summarize._facts(F))
    assert summarize.unsupported_percentages("Cardiology rose 5.0% and payments 10%.", facts) == []
    assert summarize.unsupported_percentages("Cardiology fell 0.2%.", facts) == ["0.2%"]


def _fake_client(monkeypatch, replies):
    calls = []

    class FakeMsgs:
        def create(self, **kw):
            text = replies[min(len(calls), len(replies) - 1)]
            calls.append(kw)
            class B: type = "text"
            B.text = text
            class R: content = [B()]; stop_reason = "end_turn"
            return R()

    class FakeClient:
        def __init__(self, **kw): self.messages = FakeMsgs()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return calls


def test_bad_figure_retries_then_succeeds(monkeypatch):
    calls = _fake_client(monkeypatch, ["Cardiology fell 0.2%.", "Cardiology rose 5.0%."])
    assert summarize.llm_narrative(F, "k", "m") == "Cardiology rose 5.0%." and len(calls) == 2


def test_bad_figure_twice_falls_back_to_template(monkeypatch):
    _fake_client(monkeypatch, ["Cardiology fell 0.2%."])
    text, method = summarize.narrative(F, "k", "m")
    assert method == "template" and "0.2%" not in text
