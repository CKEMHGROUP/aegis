"""Run: pytest -q   (needs the spaCy en model; pt model optional)"""
import json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from aegis import redaction as r, providers, diagnostics


@pytest.fixture(scope="module")
def engine():
    return r.build_engine()


# ---- validators
def test_nif_checksum():
    assert r.valid_pt_nif("123456789") and not r.valid_pt_nif("123456780")

def test_dni_nie_checksum():
    assert r.valid_es_dni_nie("12345678Z") and not r.valid_es_dni_nie("12345678A")
    assert r.valid_es_dni_nie("X1234567L")


# ---- redaction behaviour
def test_same_value_same_tag(engine):
    res = r.redact(engine, "Ana Costa met Bob. Ana Costa left. Mail ana@x.com, ana@x.com")
    assert res.text.count("<EMAIL_ADDRESS_1>") == 2 and "<EMAIL_ADDRESS_2>" not in res.text

def test_no_state_leaks_between_runs(engine):
    a = r.redact(engine, "Secret word: Orion", manual=["Orion"])
    b = r.redact(engine, "Orion is just a constellation here.")
    assert "<MANUAL_1>" in a.text and "<MANUAL" not in b.text   # v0.9.5 leaked manual rules across documents

def test_presets_case_insensitive(engine):
    assert "Atlas" not in r.redact(engine, "project ATLAS and Atlas.", presets=["atlas"]).text

def test_instruction_not_destroyed_by_ner(engine):
    res = r.redact(engine, "Hi, Maria Santos (maria@x.pt) wrote.", instruction="Summarise what Maria Santos wants")
    head, _, _ = res.text.partition("---")
    assert head.startswith("Summarise what") and "Maria" not in res.text

def test_dates_not_redacted_by_default(engine):
    assert "12 March 2025" in r.redact(engine, "Meeting on 12 March 2025.").text

def test_phone_and_ssn_detected(engine):
    out = r.redact(engine, "Call +1 415 555 0132, SSN 219-09-9999.").text
    assert "415" not in out and "219-09" not in out

def test_pt_nif_and_phone_bare_numbers(engine):
    assert "123456789" not in r.redact(engine, "O meu NIF é 123456789.", "Portugal (PT)").text   # valid checksum
    assert "100200300" in r.redact(engine, "Fatura número 100200300 paga.", "Portugal (PT)").text  # look-alike, no context
    assert "100200301" not in r.redact(engine, "NIF 100200301 (com erro de digitação)", "Portugal (PT)").text  # typo + context still caught

def test_roundtrip(engine):
    text = "Contact João Silva at joao@acme.pt or +351 912 345 678."
    res = r.redact(engine, text, "Portugal (PT)")
    back, unresolved = r.rehydrate(res.text, res.mapping)
    assert back == text and not unresolved


# ---- rehydration tolerance
def test_rehydrate_variants():
    m = {"<PERSON_1>": "Ana"}
    out, un = r.rehydrate("A <Person 1>, B <PERSON__1>, C <person_1>, D <PERSON_2>, E <h1>", m)
    assert out == "A Ana, B Ana, C Ana, D <PERSON_2>, E <h1>" and un == ["<PERSON_2>"]

def test_fill_candidates_ignore_markdown_links():
    c = r.find_fill_candidates("See [docs](http://x) and [Date] with <PERSON_9>")
    assert c == ["<PERSON_9>", "[Date]"]


# ---- providers
def test_provider_validation():
    assert providers.validate_config("gemini", "") is not None
    assert providers.validate_config("custom", "", "http://example.com/v1") is not None   # remote http blocked
    assert providers.validate_config("custom", "", "http://localhost:11434/v1") is None
    assert providers.describe_destination("custom", "m", "http://127.0.0.1:1234/v1").is_local
    assert not providers.describe_destination("openai", "m").is_local

def test_dispatch_never_falls_back_to_gemini(monkeypatch):
    called = []
    monkeypatch.setattr(providers, "_anthropic", lambda *a: called.append("anthropic") or ("ok", "m"))
    monkeypatch.setattr(providers, "_gemini", lambda *a: called.append("gemini") or ("ok", "m"))
    providers.send("anthropic", "k", "m", "s", "p")
    assert called == ["anthropic"]


# ---- diagnostics never log content
def test_diagnostics_content_free(tmp_path):
    diagnostics.LOG_DIR = str(tmp_path); diagnostics.LOG_FILE = str(tmp_path / "a.log"); diagnostics.FAULT_FILE = str(tmp_path / "n.log")
    diagnostics.init("test")
    diagnostics.event("stage", stage="redact", secret="Maria Silva 123456789", entity_counts={"PERSON": 2})
    try:
        raise KeyError("João Pereira NIF 123456789")
    except KeyError:
        diagnostics.log_exception(*sys.exc_info())
    log = open(diagnostics.LOG_FILE).read()
    for leak in ("Maria", "Pereira", "123456789", "João"):
        assert leak not in log


# ---- v0.11: editable analysis, review by exception, sessions
def test_uncertain_items_are_surfaced_not_silently_dropped(engine):
    a = r.analyze(engine, "Fatura 100200300 paga. O meu NIF é 123456789.", "Portugal (PT)")
    flagged = {d.text: d for d in a.needs_attention()}
    assert "100200300" in flagged and flagged["100200300"].included is False
    assert "123456789" not in a.render().text

def test_toggle_changes_rendered_output(engine):
    a = r.analyze(engine, "Fatura 100200300 paga.", "Portugal (PT)")
    det = next(d for d in a.needs_attention() if d.text == "100200300")
    assert "100200300" in a.render().text
    a.set_included(det.id, True)
    assert "100200300" not in a.render().text and not det.uncertain

def test_possible_name_in_instruction_is_flagged_then_redacted(engine):
    a = r.analyze(engine, "Quarterly numbers are attached.", instruction="Write to Ana about the report")
    ana = next(d for d in a.needs_attention() if d.text == "Ana")
    assert "Ana" in a.render().text
    a.set_included(ana.id, True)
    assert "Ana" not in a.render().text

def test_add_term_overrides_overlapping_detection(engine):
    a = r.analyze(engine, "Contact Maria Santos today.")
    a.add_term("Maria Santos")
    out = a.render().text
    assert "Maria" not in out and "<MANUAL_1>" in out

def test_earlier_render_is_a_frozen_snapshot(engine):
    a = r.analyze(engine, "Mail ana@x.com now.")
    sent = a.render()
    det = next(d for d in a.detections if d.etype == "EMAIL_ADDRESS")
    a.set_included(det.id, False)
    assert "<EMAIL_ADDRESS_1>" in sent.text and "ana@x.com" in a.render().text   # sent result unaffected

def test_filenames_never_enter_the_sent_text():
    from aegis.session import Attachment, Session
    s = Session(attachments=[Attachment("document", "Contract Maria Silva.pdf", "Body text")])
    assert "Maria" not in s.compose_doc() and "[Document 1]" in s.compose_doc()
