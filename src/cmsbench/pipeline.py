"""CLI entry point:  python -m cmsbench.pipeline run --source synthetic --years 2020 2021 2022"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import analyze, discover, export, extract, load, notify, summarize, synthetic, transform
from .config import POWERBI_DIR, REPORTS_DIR, get_settings

log = logging.getLogger("cmsbench")


def run(source: str, years: list[int], csv_paths: dict[int, str] | None = None,
        n_providers: int = 12000, send_email: bool = False, max_rows: int | None = None) -> dict:
    settings = get_settings()
    engine = load.get_engine(settings.database_url)
    load.init_db(engine)
    run_id = load.start_run(engine, source, years)
    total_in = total_rej = 0
    try:
        if source == "synthetic":
            frames = synthetic.generate(years, n_providers=n_providers)
        elif source == "csv":
            if not csv_paths:
                raise SystemExit("--csv YEAR=path required for --source csv")
            frames = {y: extract.read_csv(csv_paths[y]) for y in years}
        elif source == "api":
            datasets = resolve_datasets(years, settings.cms_datasets)
            frames = {y: extract.fetch_api(datasets[y], max_rows=max_rows) for y in years}
        else:
            raise SystemExit(f"unknown source {source}")

        for year in sorted(frames):
            df, qc = transform.clean(frames[year], year, source)
            n = load.load_year(engine, df, year)
            total_in += n
            total_rej += qc.rows_rejected
            log.info("year %s: %s loaded, rejected %s", year, n, qc.rejected or "none")

        out = publish(engine, settings, source, send_email)
        load.finish_run(engine, run_id, "success", total_in, total_rej)
        return {"run_id": run_id, "rows_loaded": total_in, "rows_rejected": total_rej, **out}
    except BaseException as exc:
        load.finish_run(engine, run_id, "failed", total_in, total_rej, detail=repr(exc)[:500])
        raise


def publish(engine, settings, source: str, send_email: bool = False) -> dict:
    """analyze -> narrative -> briefing/findings files -> Power BI CSVs -> optional email."""
    findings = analyze.build_findings(engine)
    text, method = summarize.narrative(findings, settings.anthropic_api_key, settings.summary_model,
                                       settings.anthropic_workspace_id)
    report = summarize.render_report(findings, text, method, source)

    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    (REPORTS_DIR / f"briefing_{stamp}.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / f"findings_{stamp}.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    csvs = export.export_csvs(engine, POWERBI_DIR)
    emailed = notify.send_report(settings, f"Provider benchmark briefing {stamp}", report) if send_email else False
    return {"report": str(REPORTS_DIR / f"briefing_{stamp}.md"), "csvs": [str(p) for p in csvs],
            "narrative": method, "emailed": emailed}


def report_only(send_email: bool = False) -> dict:
    """Rebuild outputs from what is already loaded (no download)."""
    settings = get_settings()
    engine = load.get_engine(settings.database_url)
    load.init_db(engine)
    from sqlalchemy import text as sql
    with engine.connect() as c:
        source = c.execute(sql("SELECT data_source FROM bench.provider_year "
                               "ORDER BY data_year DESC LIMIT 1")).scalar() or "unknown"
    return publish(engine, settings, source, send_email)


def resolve_datasets(years: list[int], configured: dict[int, str]) -> dict[int, str]:
    """CMS_DATASETS wins; any missing year is looked up in the public data.cms.gov catalog."""
    datasets = dict(configured)
    if all(y in datasets for y in years):
        return datasets
    found = {c.year: c.dataset_id for c in discover.find_datasets(discover.fetch_catalog())}
    datasets = {**found, **configured}
    missing = [y for y in years if y not in datasets]
    if missing:
        raise SystemExit(f"No CMS dataset found for year(s) {missing}. Available: {sorted(datasets)}")
    return datasets


def default_years(source: str, n: int = 3) -> list[int]:
    if source == "synthetic":
        return [2020, 2021, 2022]
    if source == "api":
        avail = sorted({**{c.year: c.dataset_id for c in discover.find_datasets(discover.fetch_catalog())},
                        **get_settings().cms_datasets})
        if not avail:
            raise SystemExit("Could not find any CMS datasets; set CMS_DATASETS or pass --years")
        return avail[-n:]
    raise SystemExit("--years is required for --source csv")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cmsbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract -> clean -> load -> analyze -> report -> export")
    r.add_argument("--source", choices=["synthetic", "csv", "api"], default="synthetic")
    r.add_argument("--years", type=int, nargs="+", default=None,
                   help="default: 2020-2022 for synthetic; latest 3 available for api")
    r.add_argument("--csv", nargs="*", default=[], metavar="YEAR=PATH")
    r.add_argument("--providers", type=int, default=12000, help="synthetic only")
    r.add_argument("--max-rows", type=int, default=None, help="api only: cap rows per year")
    r.add_argument("--email", action="store_true")
    rp = sub.add_parser("report", help="rebuild briefing + CSVs from loaded data, no download")
    rp.add_argument("--email", action="store_true")
    d = sub.add_parser("discover", help="list CMS dataset ids per year from the public catalog")
    d.add_argument("--title", default=discover.DEFAULT_TITLE)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "discover":
        cands = discover.find_datasets(discover.fetch_catalog(), args.title)
        for c in cands:
            print(f"{c.year}  {c.dataset_id}")
        print(f"\nCMS_DATASETS={discover.to_env(cands)}")
        return 0 if cands else 1

    if args.cmd == "report":
        print(json.dumps(report_only(args.email), indent=2))
        return 0

    csv_paths = {int(k): v for k, v in (kv.split("=", 1) for kv in args.csv)}
    years = args.years or ([*sorted(csv_paths)] if args.source == "csv" else default_years(args.source))
    result = run(args.source, years, csv_paths, args.providers, args.email, args.max_rows)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
