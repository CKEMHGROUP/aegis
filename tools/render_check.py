"""Compare the PDF page renderers on this Mac.
Usage:  python tools/render_check.py tools/sample_scan.pdf
Renders page 1 with each available backend, OCRs each image and prints what was read.
Images are saved to tools/_debug/ for you to look at. ONLY use this with synthetic test files, never real documents."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aegis import ocr, pdfrender

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_scan.pdf")
engine = ocr.get_engine()
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_debug")
os.makedirs(out_dir, exist_ok=True)
print("OCR engine:", getattr(engine, "name", "none"), "| renderers:", pdfrender.available_renderers())
for name in pdfrender.available_renderers():
    r = pdfrender.PdfRenderer(path)
    r._backends = [name]                      # force this one backend
    try:
        png = r.render(0)
    except Exception as e:
        print(f"[{name}] FAILED: {type(e).__name__}: {e}")
        continue
    finally:
        r.close()
    from PIL import Image
    import io
    size = Image.open(io.BytesIO(png)).size
    dest = os.path.join(out_dir, f"{name}.png")
    open(dest, "wb").write(png)
    text = engine.recognize(png, ("en", "pt", "es", "de")) if engine else ""
    lines = [l for l in text.splitlines() if l.strip()]
    print(f"[{name}] image {size[0]}x{size[1]} px, {len(png)//1024} KB, {len(lines)} text lines read -> {dest}")
    for l in lines:
        print("    ", l)
