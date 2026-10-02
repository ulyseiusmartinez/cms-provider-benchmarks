# CMS Provider Benchmarking Pipeline

An automated pipeline that pulls public Medicare provider data, loads it into PostgreSQL, computes
specialty and regional benchmarks with SQL, flags statistical outliers, writes an executive briefing
(LLM narrative over deterministic tables), and feeds a Power BI dashboard. It runs on a schedule.

```
CMS Data API ─┐
CSV download ─┼─► extract ─► transform/QC ─► Postgres (bench.*) ─► SQL views ─► analyze ─┬─► briefing.md (+ email)
synthetic ────┘     │            │                                      │                 └─► findings.json
                 retries     rejects logged                       Power BI (direct or CSV)
```

## Quick start (no CMS access needed)

```bash
docker compose up -d                       # Postgres 16
pip install -r requirements.txt
export PYTHONPATH=src                      # optional settings go in a .env file; see "Settings" below
python -m cmsbench.pipeline run --source synthetic --years 2020 2021 2022
pytest -q                                  # unit tests; integration tests run when DATABASE_URL is set
```
Outputs: `reports/briefing_<date>.md`, `reports/findings_<date>.json`, `powerbi/*.csv`.

## Results from the live run

A full run on 2 October 2026 pulled 2022–2024 from the CMS API: 3,786,375 providers, zero rows rejected, and
every year's row count matched CMS's own count. Download took about 9 minutes, loading about 20 seconds per year,
and the analysis and exports about 2.5 minutes. A write-up with the findings is at
[ulys-portfolio.onrender.com/cms-benchmarks.html](https://ulys-portfolio.onrender.com/cms-benchmarks.html).

## Using real CMS data

Dataset: *Medicare Physician & Other Practitioners – by Provider* (one file per year, ~1.2M providers each).
No API key is needed, and you don't have to look up dataset ids.

```bash
export PYTHONPATH=src
python -m cmsbench.pipeline discover          # prints the year -> dataset id map found in data.cms.gov/data.json
python -m cmsbench.pipeline run --source api  # latest 3 available years, end to end
python -m cmsbench.pipeline run --source api --years 2021 2022 --max-rows 50000   # quick sampled smoke test
python -m cmsbench.pipeline report --email   # rebuild briefing/CSVs (and email) from loaded data, no download
```
- **Discovery** reads CMS's public catalog (`data.json`) by dataset title. Pin ids with `CMS_DATASETS`
  (`2022:<uuid>,2021:<uuid>`) to override it.
- **Efficient pulls.** 5,000-row pages (CMS's maximum) and only the 11 columns we use (`column=`), instead of ~70.
  If CMS rejects the column list, it retries with all columns.
- **Completeness check.** After a full read, the row count is compared with CMS's `/data/stats`; a mismatch aborts
  before anything is loaded. (Skipped with `--max-rows`; a sampled run takes the first N rows, not a random sample.)
- **Offline alternative.** Download the CSVs yourself and run
  `--source csv --csv 2021=./data/2021.csv 2022=./data/2022.csv`.

Expect a full run to make ~260 API calls per year (about 3 minutes), and loading takes about 20 seconds per year (COPY).
Column names live in `extract.COLUMN_MAP`; missing ones fail loudly with a pointer to it.

## Design decisions

- **Idempotent loads.** Each data year is replaced in a single transaction via COPY (200k rows in ~3s), so re-runs and
  retries are safe and a failed load leaves the previous data intact.
- **Settings.** All optional, read from environment variables or a `.env` file in the repo root:
  `DATABASE_URL`, `CMS_DATASETS`, `ANTHROPIC_API_KEY` (plus `ANTHROPIC_WORKSPACE_ID` for keys not scoped to a
  workspace), `SUMMARY_MODEL`, and `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_FROM`, `REPORT_TO`
  for email (Gmail: `smtp.gmail.com` with an app password).
- **QC with accounting.** Bad rows are rejected by reason (missing key, non-numeric, negative, duplicate NPI)
  and counted in `bench.pipeline_run`.
- **SQL does the analytics.** Benchmarks are views (`sql/views.sql`), so Power BI and Python read the same logic.
- **Robust statistics.** Peer comparisons use medians, not means. Outliers are flagged with a median/MAD
  z-score on log payment per beneficiary within specialty-year (min 30 peers). Specialty "movers" are ranked by
  the change in *median* payment per beneficiary, because total payments are dominated by a handful of
  extreme providers and by provider churn between years. (The integration test caught this.) Movers are limited to
  clinical specialties with at least 100 providers; organization-type and catch-all categories ("Unknown Supplier",
  "Mass Immunizer Roster Biller") are excluded because they topped the list on noise in real data.
- **Drug context for outliers.** CMS's Part B drug payment field is loaded too, so each flag shows how much of the
  provider's payments are drugs. On 2024 data, 39% of flags were mainly drug-driven.
- **Patients aren't counted once.** CMS's beneficiary count is per provider, so summing it double-counts patients
  who see several providers. Reports say "per patient seen by a provider", never "beneficiaries served".
- **LLM writes words, code makes numbers.** Tables are rendered deterministically. The model only gets aggregate
  findings (no per-provider rows or NPIs) as labeled, pre-formatted facts. Every percentage in its reply must match one
  of those facts, or the pipeline retries once and then falls back to a template narrative. It also falls back if
  no key is set, the call fails, or the reply is cut off. (On real data, an unchecked early version attached one
  specialty's figure to another.)
- **Responsible framing.** Outlier flags are screening signals for review, never statements about a provider's conduct.
  Reports label synthetic data explicitly.

## Known limits / honest notes

- CMS publishes this dataset **annually**, so the "weekly" job mostly re-validates and regenerates; week-over-week
  deltas don't exist in the source. The analysis is year-over-year. Extending to a release check is the natural next step.
- Medicare fee-for-service only: no payer mix, and no commercial or Medicaid data.
- The number check catches invented or recomputed percentages, not a real figure attached to the wrong row; labeled
  facts are what make that unlikely. Dollar amounts and multiples aren't checked yet.
- Windows: every file is read and written as UTF-8, so the default code page doesn't matter.
- Synthetic mode plants known trends (Nurse Practitioner growth, Radiology decline) and ~0.4% outliers so tests can assert
  the analytics recover them.

## Layout

```
sql/schema.sql, sql/views.sql      tables and analytics views
src/cmsbench/                      discover, extract, transform, load, analyze, summarize, export, notify, pipeline (CLI)
tests/                             44 tests (extract w/ fake HTTP, live-layout discovery, QC rules, LLM guardrails, Postgres integration)
.github/workflows/weekly.yml       CI + scheduled run
powerbi/README.md                  model, page ideas, starter DAX
```

## Ideas to extend

Open Payments (industry payments to physicians) join on NPI · Hospital Compare quality metrics ·
release-detection step that skips runs when no new CMS year is published · dbt models for the views ·
Slack delivery · numeric checks for dollar amounts and multiples in the narrative.
