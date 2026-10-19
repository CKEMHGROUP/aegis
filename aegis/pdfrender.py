"""
Turn PDF pages into PNG bytes, in memory, for OCR.

macOS: Core Graphics (built into the OS, reached through pyobjc-framework-Quartz): nothing extra to install.
Anywhere else, or if Core Graphics fails: pypdfium2 (optional).
Page images are never written to disk.
"""
from __future__ import annotations

import importlib.util
import io
import sys

RENDER_SCALE = 3.0          # about 216 dpi
MAX_SIDE = 4000             # pixels: keeps memory sane for poster-sized pages


class RenderError(Exception):
    pass


def available_renderers() -> list:
    names = []
    if sys.platform == "darwin" and importlib.util.find_spec("Quartz") is not None:
        names.append("quartz")
    if importlib.util.find_spec("pypdfium2") is not None:
        names.append("pdfium")
    return names


class PdfRenderer:
    """Renders pages one at a time. If a backend fails it is dropped and the next one is tried."""

    def __init__(self, path: str, scale: float = RENDER_SCALE):
        self.path, self.scale = path, scale
        self._backends = available_renderers()
        self._q = None
        self._p = None
        self.used = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._p is not None:
            try:
                self._p.close()
            except Exception:
                pass
            self._p = None
        self._q = None

    def render(self, index: int) -> bytes:
        if not self._backends:
            raise RenderError("No PDF page renderer is available. On a Mac this is built in "
                              "(pyobjc-framework-Quartz); elsewhere install pypdfium2.")
        last = None
        for name in list(self._backends):
            try:
                png = getattr(self, f"_render_{name}")(index)
                self.used = name
                return png
            except Exception as e:                      # drop a broken backend and try the next
                last = e
                self._backends.remove(name)
        raise RenderError(f"The PDF page could not be rendered ({type(last).__name__}).")

    # ---- Core Graphics (macOS)
    def _render_quartz(self, index: int) -> bytes:
        import Quartz

        if self._q is None:
            raw = self.path.encode("utf-8")
            url = Quartz.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            doc = Quartz.CGPDFDocumentCreateWithURL(url)
            if doc is None:
                raise RenderError("Core Graphics could not open the PDF")
            self._q = doc
        page = Quartz.CGPDFDocumentGetPage(self._q, index + 1)
        if page is None:
            raise RenderError("page not found")
        box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFCropBox)
        w, h = box.size.width, box.size.height
        if Quartz.CGPDFPageGetRotationAngle(page) % 360 in (90, 270):
            w, h = h, w
        scale = min(self.scale, MAX_SIDE / max(w, h, 1))
        pw, ph = max(1, int(w * scale)), max(1, int(h * scale))
        ctx = Quartz.CGBitmapContextCreate(None, pw, ph, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
                                           Quartz.kCGImageAlphaNoneSkipLast)
        if ctx is None:
            raise RenderError("could not create a bitmap")
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)                      # white paper
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, pw, ph))
        Quartz.CGContextConcatCTM(ctx, Quartz.CGPDFPageGetDrawingTransform(
            page, Quartz.kCGPDFCropBox, Quartz.CGRectMake(0, 0, pw, ph), 0, True))   # handles rotation and origin
        Quartz.CGContextDrawPDFPage(ctx, page)
        image = Quartz.CGBitmapContextCreateImage(ctx)
        data = Quartz.CFDataCreateMutable(None, 0)
        dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, image, None)
        if not Quartz.CGImageDestinationFinalize(dest):
            raise RenderError("could not encode the page")
        png = bytes(data)
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RenderError("unexpected image data")
        return png

    # ---- pypdfium2 (fallback / other platforms)
    def _render_pdfium(self, index: int) -> bytes:
        import pypdfium2 as pdfium

        if self._p is None:
            self._p = pdfium.PdfDocument(self.path)
        page = self._p[index]
        try:
            image = page.render(scale=self.scale).to_pil().convert("RGB")
        finally:
            page.close()                                # close pages before their document
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
