"""Quick check that OCR works on THIS Mac.   Usage:  python tools/ocr_check.py  [path/to/image-or-pdf]
Prints your macOS version, which OCR engine Aegis will use, and (if you give a file) the text it reads."""
import os, platform, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aegis import documents, ocr, pdfrender

print("macOS:", platform.mac_ver()[0] or "not macOS", "| chip:", platform.machine(), "| python:", platform.python_version())
print("OCR:", ocr.describe())
engine = ocr.get_engine()
print("engine in use:", getattr(engine, "name", "none"))
print("PDF page renderers:", pdfrender.available_renderers() or "none")
if len(sys.argv) > 1:
    doc = documents.load_document(sys.argv[1], engine, ("en", "pt", "es", "de"))
    print(f"--- read {doc.pages} page(s), {doc.ocr_pages} by OCR, PDF renderer used: {doc.renderer or 'n/a'} ---")
    print(doc.text[:1500])
