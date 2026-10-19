"""IMAP inbox digest. Read-only: never changes message flags."""
from __future__ import annotations

import imaplib
import ssl
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _imap_date(d: datetime) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"  # locale-independent, unlike strftime("%b")


def _body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        return ""
    if part.get_content_type() == "text/html":
        content = BeautifulSoup(content, "html.parser").get_text(separator="\n")
    return "\n".join(line.strip() for line in content.splitlines() if line.strip())


def _local_naive(dt: datetime) -> datetime:
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def clean_password(host: str, password: str) -> str:
    """Google shows app passwords in four groups separated by spaces; the spaces are not part of it."""
    return password.replace(" ", "") if host.lower().endswith(("gmail.com", "googlemail.com")) else password


def _connect(host, port):
    if host in LOCAL_HOSTS:
        return imaplib.IMAP4(host, port, timeout=30)
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)


def test_login(host, port, user, password) -> str:
    """Returns "ok" or raises. Used by the Settings 'Test connection' button."""
    mail = _connect(host, port)
    try:
        mail.login(user, clean_password(host, password))
        mail.select("INBOX", readonly=True)
        return "ok"
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_many(accounts, start: datetime, end: datetime, limit: int = 30, exact_start: bool = False) -> str:
    """accounts: list of dicts(host, port, user, password). One digest per account, labelled generically."""
    parts = []
    for i, a in enumerate(accounts, 1):
        digest = fetch_digest(a["host"], a["port"], a["user"], a["password"], start, end, limit, 1500, exact_start)
        parts.append(digest.replace("=== INBOX DIGEST", f"=== INBOX DIGEST (account {i})", 1) if len(accounts) > 1 else digest)
    return "\n\n".join(parts)


def fetch_digest(host, port, user, password, start: datetime, end: datetime,
                 limit: int = 30, body_chars: int = 1500, exact_start: bool = False) -> str:
    """exact_start=True also filters by time of day (used for 'Last 24 Hours')."""
    password = clean_password(host, password)
    if host in LOCAL_HOSTS:
        mail = imaplib.IMAP4(host, port, timeout=30)  # e.g. ProtonMail Bridge on this Mac
    else:
        mail = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)
    try:
        mail.login(user, password)
        mail.select("INBOX", readonly=True)  # readonly: reading must not mark mail as seen
        before = end + timedelta(days=1)
        status, data = mail.search(None, f'(SINCE "{_imap_date(start)}" BEFORE "{_imap_date(before)}")')
        header = f"=== INBOX DIGEST ({_imap_date(start)} to {_imap_date(end)}) ===\n\n"
        if status != "OK" or not data or not data[0]:
            return header + "No emails found in this period."

        ids = data[0].split()[-limit * 3:]  # over-fetch a little: exact_start may drop some
        out, kept = [], 0
        for msg_id in reversed(ids):
            if kept >= limit:
                break
            _, parts = mail.fetch(msg_id, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if not raw:
                continue
            msg = message_from_bytes(raw, policy=policy.default)
            try:
                when = parsedate_to_datetime(msg.get("Date", ""))
            except Exception:
                when = None
            if exact_start and when is not None and _local_naive(when) < start:
                continue
            kept += 1
            out.append(
                "--- EMAIL ---\n"
                f"Date: {msg.get('Date', '')}\nFrom: {msg.get('From', 'Unknown sender')}\n"
                f"Subject: {msg.get('Subject', 'No subject')}\nBody:\n{_body_text(msg)[:body_chars]}\n")
        return header + "\n".join(out) if out else header + "No emails found in this period."
    finally:
        try:
            mail.logout()
        except Exception:
            pass
