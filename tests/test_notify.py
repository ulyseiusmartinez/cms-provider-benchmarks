from cmsbench import notify
from cmsbench.config import Settings


def _s(**kw):
    base = dict(database_url="x", cms_datasets={}, anthropic_api_key=None, summary_model="m",
                smtp_host="h", smtp_port=587, smtp_user="u", smtp_password="p",
                report_from="a@x.com", report_to="b@x.com")
    base.update(kw)
    return Settings(**base)


def test_skips_when_unconfigured():
    assert notify.send_report(_s(smtp_host=None), "s", "b") is False


def test_sends_via_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = u
        def send_message(self, m): sent["subject"] = m["Subject"]

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    assert notify.send_report(_s(), "Subj", "body") is True
    assert sent == {"host": "h", "tls": True, "login": "u", "subject": "Subj"}
