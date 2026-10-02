"""Synthetic data in the CMS "by Provider" shape, for demos, tests, and CI.

Not real data. Includes deliberate trends and planted outliers so the
analytics layer has something to find. Deterministic for a given seed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# specialty -> (relative weight, median Medicare payment per beneficiary $, yearly drift)
SPECIALTIES = {
    "Internal Medicine":        (14, 640, 0.02),
    "Family Practice":          (13, 520, 0.02),
    "Nurse Practitioner":       (12, 410, 0.11),   # planted: fast growth
    "Cardiology":               (6, 1450, 0.01),
    "Emergency Medicine":       (6, 360, 0.03),
    "Radiology":                (6, 280, -0.05),   # planted: decline
    "Orthopedic Surgery":       (4, 1180, 0.02),
    "Dermatology":              (4, 780, 0.04),
    "Ophthalmology":            (4, 1320, 0.03),
    "Physician Assistant":      (7, 330, 0.09),
    "Psychiatry":               (3, 560, 0.06),
    "Gastroenterology":         (3, 980, 0.02),
    "Anesthesiology":           (5, 300, 0.00),
    "Neurology":                (3, 700, 0.03),
    "Physical Therapist":       (4, 450, -0.02),
    "Podiatry":                 (3, 540, 0.01),
}
STATES = {  # state -> payment multiplier, relative weight
    "FL": (1.10, 10), "CA": (1.05, 12), "TX": (1.02, 10), "NY": (1.12, 9),
    "PA": (0.98, 6), "OH": (0.95, 5), "IL": (1.00, 6), "GA": (0.97, 5),
    "NC": (0.96, 5), "MI": (0.99, 5), "AZ": (1.00, 4), "WA": (1.03, 4),
    "MN": (0.92, 3), "IA": (0.88, 2), "MS": (0.90, 2), "VT": (0.93, 1),
}


def generate(years: list[int], n_providers: int = 12000, seed: int = 42,
             outlier_rate: float = 0.004) -> dict[int, pd.DataFrame]:
    """Return {year: DataFrame} in the extract.py output shape (renamed columns)."""
    rng = np.random.default_rng(seed)
    specs = list(SPECIALTIES)
    sw = np.array([SPECIALTIES[s][0] for s in specs], float)
    states = list(STATES)
    stw = np.array([STATES[s][1] for s in states], float)

    base_year = min(years)
    n_pool = int(n_providers * 1.5)
    npis = np.unique(rng.integers(1_000_000_000, 1_999_999_999, size=n_pool * 2))[:n_pool]
    rng.shuffle(npis)
    pool = pd.DataFrame({
        "npi": npis,
        "provider_type": rng.choice(specs, size=len(npis), p=sw / sw.sum()),
        "state": rng.choice(states, size=len(npis), p=stw / stw.sum()),
        "skill": rng.normal(0, 0.35, size=len(npis)),          # persistent provider effect
        "size": rng.lognormal(np.log(90), 0.7, size=len(npis)),  # persistent panel size
        "start": rng.integers(0, max(1, len(npis) - n_providers), size=len(npis)),
    })
    pool["entity_code"] = "I"
    out: dict[int, pd.DataFrame] = {}
    for i, year in enumerate(sorted(years)):
        # sliding window of the pool -> ~realistic provider churn between years
        offset = int(i * n_providers * 0.06)
        active = pool.iloc[offset: offset + n_providers].copy()
        ppb_base = active["provider_type"].map(lambda s: SPECIALTIES[s][1])
        drift = active["provider_type"].map(lambda s: (1 + SPECIALTIES[s][2]) ** (year - base_year))
        stmult = active["state"].map(lambda s: STATES[s][0])
        noise = rng.normal(0, 0.08, len(active))
        ppb = ppb_base * drift * stmult * np.exp(active["skill"] + noise)

        # plant outliers (8-15x peer level) in a small random subset
        is_out = rng.random(len(active)) < outlier_rate
        ppb = np.where(is_out, ppb * rng.uniform(8, 15, len(active)), ppb)

        benes = np.maximum(11, (active["size"] * rng.normal(1.0, 0.08, len(active)))).round()
        payment = benes * ppb
        allowed = payment / 0.8
        df = pd.DataFrame({
            "npi": active["npi"].to_numpy(),
            "provider_type": active["provider_type"].to_numpy(),
            "state": active["state"].to_numpy(),
            "entity_code": "I",
            "total_benes": benes.astype(int),
            "total_srvcs": (benes * rng.uniform(1.5, 6.0, len(active))).round(),
            "total_submitted_charges": (allowed * rng.uniform(2.0, 4.5, len(active))).round(2),
            "total_allowed": allowed.round(2),
            "total_payment": payment.round(2),
            "avg_risk_score": np.clip(rng.normal(1.05, 0.2, len(active)), 0.4, 3.0).round(3),
        })
        out[year] = df.reset_index(drop=True)
    return out
