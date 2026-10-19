"""v0.12: language detection, categories, mail accounts, no-network guarantee, composer drops."""
import os, socket, sys
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import keyring
from keyring.backend import KeyringBackend
from aegis import redaction as r, mailbox


_APP = None


class MemKeyring(KeyringBackend):
    priority = 1
    d = {}
    def get_password(self, s, u): return MemKeyring.d.get((s, u))
    def set_password(self, s, u, p): MemKeyring.d[(s, u)] = p
    def delete_password(self, s, u): MemKeyring.d.pop((s, u), None)


@pytest.fixture(scope="module")
def engine():
    return r.build_engine()


@pytest.fixture()
def qt_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    global _APP                       # keep the QApplication alive: an unreferenced one is destroyed mid-test
    _APP = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("AegisTests"); QCoreApplication.setApplicationName("T12")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    keyring.set_keyring(MemKeyring()); MemKeyring.d.clear()
    from aegis import stores
    stores._CACHE.clear(); stores.settings().clear()
    return stores


# ---- language
def test_language_detection_and_fallback():
    avail = {"en", "pt", "es", "de"}
    assert r.detect_language("Olá, o João pediu um orçamento para o projeto da próxima semana.", avail) == "pt"
    assert r.detect_language("Hallo, bitte senden Sie mir die Rechnung bis Freitag.", avail) == "de"
    assert r.detect_language("12345 678", avail) is None             # too little language to decide
    assert r.detect_language("Hallo, bitte senden Sie mir die Rechnung bis Freitag.", {"en"}) is None  # model missing

def test_auto_region_picks_language_per_document(engine):
    a = r.analyze(engine, "Olá, o João Silva pediu um orçamento para o projeto da próxima semana.", r.AUTO_REGION)
    assert a.language == "pt" and a.language_source == "detected" and not a.degraded
    b = r.analyze(engine, "Mail ana@x.com", r.AUTO_REGION)
    assert b.language == "en"

def test_explicit_language_overrides_everything(engine):
    a = r.analyze(engine, "Mail ana@x.com", r.AUTO_REGION, language="en")
    assert a.language == "en" and a.language_source == "chosen"


# ---- categories
def test_switching_off_a_category_stops_hiding_it(engine):
    text = "Contact Maria Santos at maria@x.com."
    on = r.analyze(engine, text).render().text
    off = r.analyze(engine, text, disabled={"names"}).render().text
    assert "Maria" not in on and "Maria" in off and "maria@x.com" not in off

def test_unknown_entity_types_count_as_ids():
    assert r.category_of("SOME_NEW_DETECTOR") == "ids" and r.category_of("PRESET") is None
    assert r.default_disabled() == {"dates"}

def test_user_terms_ignore_category_switches(engine):
    a = r.analyze(engine, "Project Atlas is secret.", presets=["Atlas"], disabled=set(r.CATEGORIES))
    assert "Atlas" not in a.render().text


# ---- mail accounts
def test_gmail_app_password_spaces_are_removed():
    assert mailbox.clean_password("imap.gmail.com", "abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert mailbox.clean_password("imap.mail.me.com", "abcd-efgh-ijkl-mnop") == "abcd-efgh-ijkl-mnop"

def test_accounts_roundtrip_and_secrets_stay_out_of_settings(qt_settings):
    st = qt_settings
    a = st.Account(st.new_account_id(), "Work", "Gmail / Google Workspace", "imap.gmail.com", 993)
    st.set_account_credentials(a.id, "me@corp.com", "secret-pw")
    st.save_accounts([a])
    assert st.load_accounts()[0].label == "Work"
    assert st.account_credentials(a.id) == ("me@corp.com", "secret-pw")
    assert "secret-pw" not in st.settings().value("imap/accounts") and "me@corp.com" not in st.settings().value("imap/accounts")
    st.delete_account(a.id)
    assert st.load_accounts() == [] and st.account_credentials(a.id) == ("", "")

def test_legacy_single_account_is_migrated_once(qt_settings):
    st = qt_settings
    st.set_secret(st.K_IMAP_USER, "old@x.com"); st.set_secret(st.K_IMAP_PASS, "oldpw")
    st.settings().setValue("imap/host", "imap.mail.me.com"); st.settings().setValue("imap/provider", "iCloud Mail")
    assert st.migrate_legacy_account() is True and st.migrate_legacy_account() is False
    acct = st.load_accounts()[0]
    assert st.account_credentials(acct.id) == ("old@x.com", "oldpw") and st.get_secret(st.K_IMAP_USER) == ""

def test_secret_cache_avoids_repeat_keychain_reads(qt_settings, monkeypatch):
    st = qt_settings
    st.set_secret("X", "v")
    calls = []
    monkeypatch.setattr(keyring, "get_password", lambda *a: calls.append(a) or "v")
    st._CACHE.clear()
    st.get_secret("X"); st.get_secret("X"); st.get_secret("X")
    assert len(calls) == 1


# ---- nothing leaves the Mac during detection
def test_no_network_during_detection(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access attempted during detection")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    eng = r.build_engine()
    a = r.analyze(eng, "Visit https://acme.pt/x. Olá João Silva, joao@x.pt, NIF 123456789.", r.AUTO_REGION,
                  instruction="Summarise for Ana")
    out = a.render()
    r.rehydrate(out.text, out.mapping)


# ---- composer
def test_dropping_files_on_composer_attaches_instead_of_pasting_path(qt_settings):
    from PySide6.QtCore import QMimeData, QUrl
    from aegis.widgets import Composer
    c = Composer()
    got = []
    c.files_dropped.connect(got.append)
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile("/tmp/contract.pdf")])
    c.edit.insertFromMimeData(md)
    assert got == [["/tmp/contract.pdf"]] and c.edit.toPlainText() == ""
    c.deleteLater(); _APP.processEvents()      # widgets must go before the QApplication does
