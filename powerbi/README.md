# Power BI

Two options:

1. **Direct Postgres connection (recommended).** *Get data → PostgreSQL database*, then import the
   `bench.v_*` views. Schedule refresh through a gateway if you publish to the service.
2. **CSV drop.** Every pipeline run writes `powerbi/*.csv` (gitignored). Use *Get data → Folder*.

Suggested model: `v_specialty_year` and `v_specialty_yoy` relate on (`data_year`, `provider_type`);
`v_state_specialty_benchmark` relates on the same keys plus `state`.

Suggested pages
- **National overview**: cards for providers, beneficiaries, payments, $/beneficiary; line by `data_year`.
- **Specialty movers**: bar of `median_ppb_yoy_pct` by `provider_type`, slicer on `data_year`.
- **Regional index**: filled map or matrix of `index_vs_national` by `state` × `provider_type`
  (diverging colour centred on 1.0).
- **Outlier review**: table from `v_provider_outliers` sorted by `robust_z`.

Starter DAX
```
Payment per Beneficiary = DIVIDE ( SUM ( specialty_year[payment] ), SUM ( specialty_year[benes] ) )
Payments YoY % =
VAR cur = SUM ( specialty_year[payment] )
VAR prev = CALCULATE ( SUM ( specialty_year[payment] ), specialty_year[data_year] = MAX ( specialty_year[data_year] ) - 1 )
RETURN DIVIDE ( cur - prev, prev )
```
