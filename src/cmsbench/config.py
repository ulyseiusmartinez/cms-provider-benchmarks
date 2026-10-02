from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
REPORTS_DIR = ROOT / "reports"
POWERBI_DIR = ROOT / "powerbi"


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Tiny .env loader (real environment variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def parse_datasets(raw: str) -> dict[int, str]:
    """'2022:uuid,2021:uuid' -> {2022: 'uuid', 2021: 'uuid'}"""
    out: dict[int, str] = {}
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        year, sep, dataset_id = part.partition(":")
        if not sep or not dataset_id.strip():
            raise ValueError(f"Bad CMS_DATASETS entry {part!r}; expected year:uuid")
        out[int(year)] = dataset_id.strip()
    return out


@dataclass(frozen=True)
class Settings:
    database_url: str
    cms_datasets: dict[int, str]
    anthropic_api_key: str | None
    summary_model: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    report_from: str | None
    report_to: str | None
    anthropic_workspace_id: str | None = None


def get_settings() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        database_url=env("DATABASE_URL")
        or "postgresql+psycopg://postgres:postgres@localhost:5432/cmsbench",
        cms_datasets=parse_datasets(env("CMS_DATASETS", "")),
        anthropic_api_key=env("ANTHROPIC_API_KEY") or None,
        anthropic_workspace_id=env("ANTHROPIC_WORKSPACE_ID") or None,
        summary_model=env("SUMMARY_MODEL") or "claude-sonnet-5-5",
        smtp_host=env("SMTP_HOST") or None,
        smtp_port=int(env("SMTP_PORT") or 587),
        smtp_user=env("SMTP_USER") or None,
        smtp_password=env("SMTP_PASSWORD") or None,
        report_from=env("REPORT_FROM") or None,
        report_to=env("REPORT_TO") or None,
    )
