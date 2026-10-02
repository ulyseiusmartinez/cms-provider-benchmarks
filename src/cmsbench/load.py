"""Load: idempotent per-year replace into Postgres, plus run auditing."""
from __future__ import annotations

import io

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from .config import SQL_DIR


def get_engine(url: str) -> Engine:
    return create_engine(url, future=True)


def init_db(engine: Engine, with_views: bool = True) -> None:
    files = ["schema.sql"] + (["views.sql"] if with_views else [])
    with engine.begin() as conn:
        for name in files:
            conn.exec_driver_sql((SQL_DIR / name).read_text(encoding="utf-8"))


def load_year(engine: Engine, df: pd.DataFrame, year: int, chunk_rows: int = 100_000) -> int:
    """Replace one data year atomically (re-running is safe).

    Uses COPY (~10x faster than INSERT for CMS-sized files of ~1.2M rows) inside the same
    transaction as the DELETE, so a failed load leaves the previous data intact.
    """
    cols = [c for c in df.columns if c != "loaded_at"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bench.provider_year WHERE data_year = :y"), {"y": year})
        raw = conn.connection.driver_connection          # psycopg connection, same transaction
        with raw.cursor() as cur:
            with cur.copy(f"COPY bench.provider_year ({', '.join(cols)}) "
                          "FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
                for i in range(0, len(df), chunk_rows):
                    buf = io.StringIO()
                    df[cols].iloc[i:i + chunk_rows].to_csv(buf, index=False, header=False, na_rep="")
                    cp.write(buf.getvalue())
    return len(df)


def start_run(engine: Engine, source: str, years: list[int]) -> int:
    with engine.begin() as conn:
        # a hard-killed earlier run never reaches finish_run; don't leave it 'running' forever
        conn.execute(text("""UPDATE bench.pipeline_run SET status = 'abandoned', finished_at = now()
                             WHERE status = 'running' AND started_at < now() - interval '1 hour'"""))
        return conn.execute(
            text("INSERT INTO bench.pipeline_run (source, years) VALUES (:s, :y) RETURNING run_id"),
            {"s": source, "y": ",".join(map(str, years))},
        ).scalar_one()


def finish_run(engine: Engine, run_id: int, status: str, rows_loaded: int = 0,
               rows_rejected: int = 0, detail: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE bench.pipeline_run
                    SET finished_at = now(), status = :st, rows_loaded = :rl,
                        rows_rejected = :rr, detail = :d WHERE run_id = :id"""),
            {"st": status, "rl": rows_loaded, "rr": rows_rejected, "d": detail, "id": run_id},
        )
