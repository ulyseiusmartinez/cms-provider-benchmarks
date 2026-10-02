"""Weekly report: tables are rendered deterministically; only the narrative is LLM-written."""
from __future__ import annotations

import json
import re
import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

SYSTEM = (
    "You are a healthcare data analyst writing a short executive briefing about Medicare "
    "provider payment benchmarks. Use ONLY the numbers in the JSON provided; never invent "
    "figures. Write 3-5 plain-English sentences. Describe outliers strictly as statistical "
    "flags that warrant review, never as evidence of wrongdoing. Do not name individual "
    "providers or NPIs. Quote figures exactly as written in the facts and keep each figure with "
    "the specialty or state it belongs to; do not calculate new percentages. Provider-patient "
    "pairs count a patient once per provider seen: never call them beneficiaries or patients "
    "served. When discussing outliers, mention how many are driven mainly by Part B drug "
    "payments. Plain prose only: no headings, bold or bullet points."
)


def _money(x: float) -> str:
    if abs(x) >= 1e9:
        return f"${x/1e9:,.2f}B"
    if abs(x) >= 1e6:
        return f"${x/1e6:,.1f}M"
    return f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x*100:+.1f}%"


def template_narrative(f: dict[str, Any]) -> str:
    y, p = f["latest_year"], f["prior_year"]
    nat = f["national"][str(y)]
    parts = [f"In {y}, {nat['providers']:,} providers received {_money(nat['payment'])} in Medicare "
             f"payments, averaging {_money(nat['payment_per_bene'])} per patient seen by each "
             "provider (a patient who saw several providers counts once per provider)."]
    if p and f["specialty_movers"]:
        m = f["specialty_movers"][0]
        parts.append(f"Versus {p}, the largest specialty swing was {m['provider_type']} "
                     f"({_pct(m['median_ppb_yoy_pct'])} median payment per beneficiary, "
                     f"{_pct(m['payment_yoy_pct'])} total payments, "
                     f"{_pct(m['providers_yoy_pct'])} providers).")
    if f["state_high"]:
        s = f["state_high"][0]
        parts.append(f"The highest regional cost index was {s['provider_type']} in {s['state']} "
                     f"at {s['index_vs_national']:.2f}x the national median per beneficiary.")
    parts.append(f"{f['outlier_count']:,} providers were statistically flagged for unusually high "
                 "payment per beneficiary relative to specialty peers"
                 f"{_drug_clause(f)}; flags warrant review and are not findings of improper billing.")
    return " ".join(parts)


def _drug_clause(f: dict[str, Any]) -> str:
    n, drug, hidden = f["outlier_count"], f.get("outliers_drug_driven", 0), f.get("outliers_drug_suppressed", 0)
    if not n or hidden == n:
        return ""
    share = int(f["thresholds"].get("drug_driven_share", 0.5) * 100)
    return (f" ({drug:,} mainly from Part B drug payments, i.e. ≥{share}% drugs, which costly "
            f"infused drugs push up; {hidden:,} with drug detail suppressed by CMS)")


def _facts(f: dict[str, Any]) -> dict[str, Any]:
    """Findings as labeled, pre-formatted facts. Raw floats invite the model to misattribute
    or recompute numbers; labeled strings it can quote. No per-provider detail is included."""
    nat = f["national"]
    years = []
    for yr in sorted(nat, key=int):
        n, prev = nat[yr], nat.get(str(int(yr) - 1))
        row = {"year": int(yr), "providers": f"{n['providers']:,}",
               "total Medicare payments": _money(n["payment"]),
               "provider-patient pairs (a patient counts once per provider seen)":
                   f"{n['bene_provider_pairs'] / 1e6:,.1f} million",
               "payment per patient seen by a provider": f"${n['payment_per_bene']:,.2f}"}
        if prev:
            row |= {"payments vs prior year": _pct(n["payment"] / prev["payment"] - 1),
                    "providers vs prior year": _pct(n["providers"] / prev["providers"] - 1),
                    "provider-patient pairs vs prior year":
                        _pct(n["bene_provider_pairs"] / prev["bene_provider_pairs"] - 1),
                    "payment per patient vs prior year":
                        _pct(n["payment_per_bene"] / prev["payment_per_bene"] - 1)}
        years.append(row)
    t = f["thresholds"]
    n_out = f["outlier_count"]
    return {
        "latest_year": f["latest_year"], "prior_year": f["prior_year"], "national_by_year": years,
        "specialty_movers (clinical specialties with at least "
        f"{t.get('min_mover_providers', t.get('min_specialty_providers'))} providers, ranked by change in median "
        "payment per patient)": [
            f"{m['provider_type']}: median payment per patient {_pct(m['median_ppb_yoy_pct'])} vs prior year; "
            f"total payments {_pct(m['payment_yoy_pct'])}; {int(m['providers']):,} providers"
            for m in f["specialty_movers"]],
        "highest state cost index (state median payment per patient vs national median, same specialty)": [
            f"{s['provider_type']} in {s['state']}: {s['index_vs_national']:.2f}x national "
            f"({_money(s['median_ppb'])} vs {_money(s['national_median_ppb'])}); {int(s['providers']):,} providers"
            for s in f["state_high"]],
        "lowest state cost index": [
            f"{s['provider_type']} in {s['state']}: {s['index_vs_national']:.2f}x national "
            f"({_money(s['median_ppb'])} vs {_money(s['national_median_ppb'])}); {int(s['providers']):,} providers"
            for s in f["state_low"]],
        "outlier_flags": {
            "providers flagged (robust z >= "f"{t['outlier_robust_z']})": f"{n_out:,}",
            "flags mainly from Part B drug payments (drug share >= "
            f"{t.get('drug_driven_share', 0.5) * 100:.0f}%)": f"{f.get('outliers_drug_driven', 0):,}"
            + (f" ({f.get('outliers_drug_driven', 0) / n_out * 100:.0f}% of flags)" if n_out else ""),
            "flags where CMS suppresses the drug detail": f"{f.get('outliers_drug_suppressed', 0):,}"},
    }


_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def unsupported_percentages(text: str, facts_json: str) -> list[str]:
    """Percentages in the narrative that don't match any percentage in the facts (sign-insensitive,
    so "fell 3.5%" matches "-3.5%"). Catches invented or recomputed figures, not misattribution."""
    allowed = [float(x) for x in _PCT_RE.findall(facts_json)]
    return [f"{x}%" for x in _PCT_RE.findall(text)
            if not any(abs(float(x) - a) <= 0.051 for a in allowed)]


def llm_narrative(f: dict[str, Any], api_key: str, model: str, workspace_id: str | None = None,
                  attempts: int = 2) -> str:
    import anthropic
    # keys not scoped to a workspace must name one per request
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    client = anthropic.Anthropic(api_key=api_key, default_headers=headers)
    payload = json.dumps(_facts(f), ensure_ascii=False, indent=1)
    for attempt in range(1, attempts + 1):
        msg = client.messages.create(
            model=model, max_tokens=2000, system=SYSTEM,
            messages=[{"role": "user", "content": payload}],
        )
        if getattr(msg, "stop_reason", None) == "max_tokens":   # never ship a half sentence
            raise RuntimeError("LLM narrative hit max_tokens and was cut off")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        bad = unsupported_percentages(text, payload)
        if not bad:
            return text
        log.warning("LLM narrative attempt %s used figures not in the facts: %s", attempt, bad)
    raise RuntimeError(f"LLM narrative used unsupported figures {bad}")


def narrative(f: dict[str, Any], api_key: str | None, model: str,
              workspace_id: str | None = None) -> tuple[str, str]:
    """Returns (text, method). Falls back to the template on any LLM failure."""
    if api_key:
        try:
            return llm_narrative(f, api_key, model, workspace_id), f"llm:{model}"
        except Exception as exc:  # network, auth, quota...
            log.warning("LLM narrative failed (%s); using template", exc)
    return template_narrative(f), "template"


def render_report(f: dict[str, Any], narrative_text: str, method: str, data_source: str) -> str:
    y, p = f["latest_year"], f["prior_year"]
    L = [f"# Provider Benchmark Briefing — data year {y}",
         f"*Generated {date.today().isoformat()} · data source: **{data_source}** · "
         f"narrative: {method}*", ""]
    if data_source == "synthetic":
        L += ["> ⚠️ Synthetic demo data. Figures are not real Medicare statistics.", ""]
    L += ["## Summary", narrative_text, ""]
    if f["specialty_movers"]:
        L += [f"## Biggest specialty movers vs {p}",
              f"Clinical specialties with at least {f['thresholds'].get('min_mover_providers', 0):,} "
              "providers; facility, supplier and catch-all billing categories are excluded.", "",
              "| Specialty | Providers | Payments | Payments YoY | Median $/bene YoY (ranked by) |",
              "|---|---:|---:|---:|---:|"]
        for m in f["specialty_movers"]:
            L.append(f"| {m['provider_type']} | {int(m['providers']):,} | {_money(m['payment'])} | "
                     f"{_pct(m['payment_yoy_pct'])} | {_pct(m['median_ppb_yoy_pct'])} |")
        L.append("")
    for title, key in (("Highest regional cost index", "state_high"),
                       ("Lowest regional cost index", "state_low")):
        if f[key]:
            L += [f"## {title} (median $/beneficiary vs national, same specialty)",
                  "| Specialty | State | Providers | Median $/bene | Index |", "|---|---|---:|---:|---:|"]
            for s in f[key]:
                L.append(f"| {s['provider_type']} | {s['state']} | {int(s['providers']):,} | "
                         f"{_money(s['median_ppb'])} | {s['index_vs_national']:.2f}x |")
            L.append("")
    L += [f"## Statistical outlier flags: {f['outlier_count']:,}",
          f"Providers whose payment per beneficiary sits at robust z ≥ "
          f"{f['thresholds']['outlier_robust_z']} versus specialty peers (log scale, median/MAD). "
          "These are screening flags for analyst review, not conclusions about any provider. "
          "Drug share is the Part B drug portion of the provider's payments (— where CMS suppresses it); "
          "a high share usually means expensive infused or injected drugs rather than unusual visit billing.", ""]
    if f["outliers"]:
        L += ["| NPI | Specialty | State | $/bene | Peer median | Drug share | z |",
              "|---|---|---|---:|---:|---:|---:|"]
        for o in f["outliers"]:
            ds = o.get("drug_share")
            L.append(f"| {o['npi']} | {o['provider_type']} | {o['state']} | "
                     f"{_money(o['payment_per_bene'])} | {_money(o['specialty_median_ppb'])} | "
                     f"{'—' if ds is None else f'{ds:.0%}'} | {o['robust_z']:.1f} |")
        L.append("")
    return "\n".join(L)
