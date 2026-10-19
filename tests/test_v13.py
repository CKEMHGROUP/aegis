"""v0.13: OCR plumbing, folder scanning, one-way batches, optional encrypted key."""
import os, sys, json
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from aegis import redaction as r, documents, ocr, batch


@pytest.fixture(scope="module")
def engine():
    return r.build_engine()


class FakeOCR:
    name = "Fake"
    def __init__(self, text="Fatura 100200300 para Maria Santos, NIF 123456789"): self.text, self.calls = text, 0
    def recognize(self, png, languages=("en",)):
        self.calls += 1
        assert png[:8] == b"\x89PNG\r\n\x1a\n"      # real PNG bytes reach the engine, in memory
        return self.text


def _image(path, text="Invoice 4711 for Maria Silva"):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 300), "white")
    ImageDraw.Draw(img).text((40, 100), text, fill="black", font=ImageFont.load_default(size=56))
    img.save(path)


# ---- OCR / documents
def test_image_without_ocr_explains_what_to_do(tmp_path):
    _image(tmp_path / "a.png")
    with pytest.raises(documents.DocumentError, match="Settings"):
        documents.load_document(str(tmp_path / "a.png"))

def test_image_goes_through_engine_as_png_bytes(tmp_path):
    _image(tmp_path / "a.png")
    fake = FakeOCR()
    doc = documents.load_document(str(tmp_path / "a.png"), fake)
    assert "Maria Santos" in doc.text and doc.ocr_pages == 1 and doc.engine == "Fake" and fake.calls == 1

def test_scanned_pdf_pages_are_ocred_and_text_pages_are_not(tmp_path):
    from PIL import Image
    Image.new("RGB", (800, 600), "white").save(tmp_path / "scan.pdf")           # image-only PDF: no text layer
    fake = FakeOCR("Scanned page text")
    doc = documents.load_document(str(tmp_path / "scan.pdf"), fake)
    assert doc.text == "Scanned page text" and doc.ocr_pages == 1 and doc.renderer in ("pdfium", "quartz")
    with pytest.raises(documents.DocumentError, match="OCR"):
        documents.load_document(str(tmp_path / "scan.pdf"))                    # OCR off: clear message

@pytest.mark.skipif(not ocr.TesseractEngine.available(), reason="tesseract not installed")
def test_real_tesseract_reads_a_rendered_pdf(tmp_path):
    from PIL import Image
    _image(tmp_path / "a.png")
    Image.open(tmp_path / "a.png").save(tmp_path / "scan.pdf")
    doc = documents.load_document(str(tmp_path / "scan.pdf"), ocr.TesseractEngine(), ("en",))
    assert "Invoice" in doc.text and "4711" in doc.text

def test_engine_selection_never_crashes():
    eng = ocr.get_engine()
    assert eng is None or hasattr(eng, "recognize")
    assert isinstance(ocr.describe(), str)


# ---- scanning: new / changed / unchanged / removed / renamed
def _write(p, text): os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(text)

def test_scan_detects_new_changed_unchanged_removed_renamed(tmp_path, engine):
    src, loc = tmp_path / "in", tmp_path / "local"
    _write(src / "a.txt", "Mail ana@x.com"); _write(src / "b.txt", "Call 415 555 0132 John Smith"); _write(src / "sub/c.txt", "plain")
    m = batch.Manifest(str(src), str(loc))
    st = batch.scan_folder(str(src), m)
    assert {s.rel: s.status for s in st} == {"a.txt": "new", "b.txt": "new", os.path.join("sub", "c.txt"): "new"}
    batch.sanitize_batch(engine, str(src), batch.pending(st), str(tmp_path / "out"), m, local_root=str(loc))

    m = batch.Manifest(str(src), str(loc))
    assert all(s.status == "unchanged" for s in batch.scan_folder(str(src), m))

    time_bump = os.stat(src / "a.txt").st_mtime + 5
    _write(src / "a.txt", "Mail someone@else.com now"); os.utime(src / "a.txt", (time_bump, time_bump))
    os.rename(src / "b.txt", src / "b-renamed.txt")
    os.remove(src / "sub/c.txt")
    _write(src / "d.txt", "brand new")
    got = {s.rel: s.status for s in batch.scan_folder(str(src), m)}
    assert got["a.txt"] == "modified" and got["b-renamed.txt"] == "renamed" and got["d.txt"] == "new"
    assert got[os.path.join("sub", "c.txt")] == "missing"

def test_touching_a_file_without_changing_it_is_not_a_change(tmp_path, engine):
    src, loc = tmp_path / "in", tmp_path / "local"
    _write(src / "a.txt", "Mail ana@x.com")
    m = batch.Manifest(str(src), str(loc))
    batch.sanitize_batch(engine, str(src), batch.scan_folder(str(src), m), str(tmp_path / "out"), m, local_root=str(loc))
    os.utime(src / "a.txt", (os.stat(src / "a.txt").st_atime, os.stat(src / "a.txt").st_mtime + 100))
    assert batch.scan_folder(str(src), batch.Manifest(str(src), str(loc)))[0].status == "unchanged"

def test_hidden_and_unsupported_files_are_ignored(tmp_path):
    src = tmp_path / "in"
    _write(src / ".hidden.txt", "x"); _write(src / "notes.xyz", "x"); _write(src / "ok.txt", "x")
    assert [s.rel for s in batch.scan_folder(str(src), batch.Manifest(str(src), str(tmp_path / "l")))] == ["ok.txt"]


# ---- the one-way share copy
def _run(tmp_path, engine, files, **kw):
    src, loc, out = tmp_path / "Confidential Maria Silva", tmp_path / "local", tmp_path / "out"
    for name, text in files.items(): _write(src / name, text)
    m = batch.Manifest(str(src), str(loc))
    res = batch.sanitize_batch(engine, str(src), batch.scan_folder(str(src), m), str(out), m,
                               local_root=str(loc), app_version="t", **kw)
    return res, src, loc, out

def test_shared_folder_has_no_names_no_manifest_no_mapping(tmp_path, engine):
    res, src, loc, out = _run(tmp_path, engine, {"Contract Maria Silva.txt": "Maria Silva, maria@x.com", "Memo.txt": "Call John Smith"})
    names = sorted(os.listdir(res.out_dir))
    assert names == ["Document-001.txt", "Document-002.txt"]
    blob = "".join(open(os.path.join(res.out_dir, n)).read() for n in names)
    assert "Maria" not in blob and "maria@x.com" not in blob and "John" not in blob
    assert blob.count("one-way") == 2
    assert not any(f.endswith((".json", ".enc")) for f in os.listdir(res.out_dir))
    assert os.path.exists(loc / res.batch_id / "report.json") and not os.path.exists(loc / res.batch_id / "mapping.enc")

def test_same_person_gets_same_placeholder_across_files(tmp_path, engine):
    res, *_ = _run(tmp_path, engine, {"a.txt": "Maria Santos asked for the report.", "b.txt": "Please thank Maria Santos."})
    a, b = (open(os.path.join(res.out_dir, f"Document-00{i}.txt")).read() for i in (1, 2))
    assert "<PERSON_1>" in a and "<PERSON_1>" in b

def test_unsure_items_are_hidden_by_default_in_batches(tmp_path, engine):
    res, *_ = _run(tmp_path, engine, {"a.txt": "Fatura 100200300 paga."}, region="Portugal (PT)")
    assert "100200300" not in open(os.path.join(res.out_dir, "Document-001.txt")).read()
    assert res.total_uncertain == 1

def test_one_bad_file_does_not_stop_the_batch(tmp_path, engine):
    res, *_ = _run(tmp_path, engine, {"good.txt": "Mail ana@x.com"})
    src = tmp_path / "Confidential Maria Silva"
    (src / "broken.pdf").write_bytes(b"not a pdf")
    m = batch.Manifest(str(src), str(tmp_path / "local"))
    res2 = batch.sanitize_batch(engine, str(src), [s for s in batch.scan_folder(str(src), m) if s.rel == "broken.pdf"],
                                str(tmp_path / "out"), m, local_root=str(tmp_path / "local"))
    assert res2.outcomes[0].status == "failed" and res2.ok_count == 0


# ---- optional encrypted key (owner-only restore)
def test_key_is_optional_off_by_default_and_roundtrips_when_on(tmp_path, engine):
    keys = {}
    res, src, loc, out = _run(tmp_path, engine, {"a.txt": "Maria Santos wrote to maria@x.com"}, keep_key=True,
                              key_store=lambda bid, k: keys.__setitem__(bid, k))
    assert res.key_kept and res.batch_id in keys
    enc = (loc / res.batch_id / "mapping.enc").read_bytes()
    assert b"Maria" not in enc and b"maria@x.com" not in enc               # ciphertext only
    mapping = batch.load_batch_mapping(res.batch_id, keys.get, str(loc))
    returned = "Reply to <PERSON_1> at <EMAIL_ADDRESS_1>."                  # what a colleague sends back
    assert batch.restore_text(returned, mapping) == "Reply to Maria Santos at maria@x.com."
    assert batch.load_batch_mapping(res.batch_id, lambda b: None, str(loc)) is None   # no key, no restore
    assert batch.list_restorable_batches(str(loc)) == [res.batch_id]

def test_tampered_mapping_fails_closed(tmp_path):
    key = os.urandom(32)
    blob = bytearray(batch.encrypt_mapping({"<PERSON_1>": "Ana"}, key)); blob[-1] ^= 1
    with pytest.raises(Exception):
        batch.decrypt_mapping(bytes(blob), key)


# ---- PDF page rendering: native first, fallback, clear errors
def test_pdf_renderer_falls_back_when_the_first_backend_breaks(tmp_path, monkeypatch):
    from PIL import Image
    from aegis import pdfrender
    Image.new("RGB", (400, 300), "white").save(tmp_path / "s.pdf")
    monkeypatch.setattr(pdfrender, "available_renderers", lambda: ["quartz", "pdfium"])
    def broken(self, i): raise RuntimeError("simulated Core Graphics failure")
    monkeypatch.setattr(pdfrender.PdfRenderer, "_render_quartz", broken, raising=False)
    with pdfrender.PdfRenderer(str(tmp_path / "s.pdf")) as pr:
        png = pr.render(0)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and pr.used == "pdfium"

def test_missing_renderer_gives_a_clear_message_not_a_traceback(tmp_path, monkeypatch):
    from PIL import Image
    from aegis import pdfrender
    Image.new("RGB", (400, 300), "white").save(tmp_path / "s.pdf")
    monkeypatch.setattr(pdfrender, "available_renderers", lambda: [])
    with pytest.raises(documents.DocumentError, match="pages could not be turned into images"):
        documents.load_document(str(tmp_path / "s.pdf"), FakeOCR())
