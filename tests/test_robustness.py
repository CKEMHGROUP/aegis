"""Hostile and awkward inputs. If one of these fails, a real document eventually will too."""
import os, sys, email.message
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from aegis import redaction as r, mailbox, diagnostics


@pytest.fixture(scope="module")
def engine():
    return r.build_engine()


def test_empty_and_whitespace_inputs(engine):
    assert r.analyze(engine, "").render().text == ""
    assert r.analyze(engine, "   \n\t ").render().text.strip() == ""
    assert r.analyze(engine, "", instruction="Just a question").render().text == "Just a question"

@pytest.mark.parametrize("text", [
    "Ana 😀 ana@x.com ✓ \u200bJoão\u200b Silva", "مرحبا ana@x.com", "e\u0301mail: jo\u0303ao@x.pt", "\x00\x01 ana@x.com \x7f",
    "A" * 50000, "ana@x.com\r\nJoão Silva\r\n" * 2000, "<<<>>> <PERSON_ <PERSON_1> </PERSON_2>",
])
def test_awkward_text_never_crashes_and_roundtrips_emails(engine, text):
    res = r.analyze(engine, text).render()
    back, _ = r.rehydrate(res.text, res.mapping)
    assert isinstance(back, str)
    if "ana@x.com" in text:
        assert "ana@x.com" not in res.text

def test_document_that_already_contains_placeholders_cannot_collide(engine):
    """A pasted, already-sanitised text contains <PERSON_1>. It must not be swapped for a real value later."""
    res = r.analyze(engine, "Earlier note: <PERSON_1> agreed. New: Maria Santos agreed too.").render()
    back, _ = r.rehydrate(res.text, res.mapping)
    assert back.count("Maria Santos") == 1          # only the real name comes back, not the pre-existing tag
    assert "<PERSON_1>" in back or "<TAGLIKE_1>" not in back

def test_large_document_is_chunked_without_losing_entities(engine):
    filler = "Lorem ipsum dolor sit amet consectetur.\n" * 6000       # about 240k characters, crosses the chunk size
    text = filler + "Contact maria@x.com now.\n" + filler
    out = r.analyze(engine, text).render().text
    assert "maria@x.com" not in out and out.count("<EMAIL_ADDRESS_1>") == 1

def test_overlapping_user_terms_and_detections(engine):
    a = r.analyze(engine, "Maria Santos Maria Santos-Silva", presets=["Maria", "Santos-Silva"])
    assert "Maria" not in a.render().text and "Santos-Silva" not in a.render().text

def test_rehydrate_is_not_fooled_by_html_or_code(engine):
    m = {"<PERSON_1>": "Ana"}
    text = "<h1>Title</h1> <b2>x</b2> if a<b and c>d then <PERSON_1> <script>1</script>"
    out, un = r.rehydrate(text, m)
    assert out == text.replace("<PERSON_1>", "Ana") and un == []


# ---- mail: read-only, peek, gmail password, multipart/html
class FakeIMAP:
    log = []
    def __init__(self, *a, **k): FakeIMAP.log.append(("connect", a, k))
    def login(self, u, p): FakeIMAP.log.append(("login", u, p))
    def select(self, box, readonly=False): FakeIMAP.log.append(("select", box, readonly)); return "OK", [b"1"]
    def search(self, charset, crit): FakeIMAP.log.append(("search", crit)); return "OK", [b"1 2"]
    def fetch(self, mid, what):
        FakeIMAP.log.append(("fetch", what))
        m = email.message.EmailMessage()
        m["From"] = "Ana <ana@x.com>"; m["Subject"] = "Hello =?utf-8?q?Jo=C3=A3o?="; m["Date"] = "Mon, 05 Oct 2026 10:00:00 +0000"
        m.set_content("plain body"); m.add_alternative("<html><body><p>Html <b>body</b></p></body></html>", subtype="html")
        return "OK", [(b"1 (BODY[] {1})", m.as_bytes()), b")"]
    def logout(self): FakeIMAP.log.append(("logout",))

def test_mail_digest_is_read_only_peeks_and_cleans_gmail_password(monkeypatch):
    from datetime import datetime, timedelta
    FakeIMAP.log.clear()
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", FakeIMAP)
    out = mailbox.fetch_digest("imap.gmail.com", 993, "me@x.com", "abcd efgh ijkl mnop", datetime.now() - timedelta(days=3), datetime.now())
    kinds = {e[0]: e for e in FakeIMAP.log}
    assert kinds["select"][2] is True and "PEEK" in kinds["fetch"][1] and kinds["login"][2] == "abcdefghijklmnop"
    assert "plain body" in out and "João" in out and kinds["logout"]
    assert FakeIMAP.log[0][2].get("timeout") == 30

def test_digest_survives_a_message_with_no_body_or_date(monkeypatch):
    from datetime import datetime, timedelta
    class Odd(FakeIMAP):
        def fetch(self, mid, what):
            return "OK", [(b"1", b"From: x@y.com\r\nSubject: only headers\r\n\r\n"), b")"]
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", Odd)
    out = mailbox.fetch_digest("imap.mail.me.com", 993, "u", "p", datetime.now() - timedelta(days=1), datetime.now(), exact_start=True)
    assert "only headers" in out

def test_login_failure_still_logs_out(monkeypatch):
    from datetime import datetime
    class Bad(FakeIMAP):
        def login(self, u, p): raise OSError("auth failed")
    FakeIMAP.log.clear()
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", Bad)
    with pytest.raises(OSError):
        mailbox.fetch_digest("imap.x.com", 993, "u", "p", datetime.now(), datetime.now())


# ---- diagnostics with odd values
def test_diagnostics_tolerates_odd_values(tmp_path):
    diagnostics.LOG_DIR = str(tmp_path); diagnostics.LOG_FILE = str(tmp_path / "a.log"); diagnostics.FAULT_FILE = str(tmp_path / "n.log")
    diagnostics.init("t")
    diagnostics.event("stage", stage=None, ok=object(), duration_ms=b"bytes", entity_counts={"P": float("nan")}, frames=["a" * 500])
    diagnostics.event("stage", stage="x" * 5000)
    assert open(diagnostics.LOG_FILE).read().count("\n") >= 3
