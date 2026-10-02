"""Optional email delivery of the weekly report."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger(__name__)


def send_report(settings: Settings, subject: str, body_markdown: str) -> bool:
    missing = [n for n, v in (("SMTP_HOST", settings.smtp_host), ("REPORT_FROM", settings.report_from),
                              ("REPORT_TO", settings.report_to)) if not v]
    if missing:
        log.warning("email requested but %s not set in .env; skipping", ", ".join(missing))
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.report_from, settings.report_to
    msg.set_content(body_markdown)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
    return True
