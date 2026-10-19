"""
OCR engines.

Apple Vision is the primary engine on macOS: built into the OS, no extra program to install,
and it reads Portuguese, Spanish, German and English. Tesseract is the fallback (and the
cross-platform path for a later Windows/Linux edition).

Images are passed to the engines as bytes in memory: scanned pages (often ID cards, contracts,
invoices) are never written to temporary files.
"""
from __future__ import annotations

import importlib.util
import io
import shutil
import sys

# Our language codes -> Vision (BCP-47) and Tesseract codes.
VISION_LANGS = {"en": "en-US", "pt": "pt-BR", "es": "es-ES", "de": "de-DE"}
TESS_LANGS = {"en": "eng", "pt": "por", "es": "spa", "de": "deu"}


class OcrError(Exception):
    pass


class VisionEngine:
    name = "Apple Vision"

    @staticmethod
    def available() -> bool:
        if sys.platform != "darwin":
            return False
        try:
            return all(importlib.util.find_spec(m) is not None for m in ("Quartz", "Vision", "Foundation"))
        except Exception:
            return False

    def recognize(self, png_bytes: bytes, languages=("en",)) -> str:
        import Vision
        from Foundation import NSData

        data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        wanted = [VISION_LANGS[l] for l in languages if l in VISION_LANGS]
        try:  # which languages this macOS version can read
            supported, _err = request.supportedRecognitionLanguagesAndReturnError_(None)
            wanted = [l for l in wanted if l in set(supported or [])]
        except Exception:
            pass
        if wanted:
            request.setRecognitionLanguages_(wanted)
        try:  # Vision ignores text smaller than a fraction of the page height; body text on a full page is small
            request.setMinimumTextHeight_(0.005)
        except Exception:
            pass
        try:  # macOS 13+: let Vision pick the language itself
            request.setAutomaticallyDetectsLanguage_(True)
        except Exception:
            pass
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise OcrError(f"Apple Vision could not read the image ({err})")
        observations = list(request.results() or [])

        def top_left(o):  # Vision boxes are normalised with the origin at the bottom left
            b = o.boundingBox()
            return (-round(b.origin.y + b.size.height, 2), b.origin.x)

        lines = []
        for obs in sorted(observations, key=top_left):
            cands = obs.topCandidates_(1)
            if cands:
                lines.append(str(cands[0].string()))
        return "\n".join(lines)


class TesseractEngine:
    name = "Tesseract"

    @staticmethod
    def available() -> bool:
        if not shutil.which("tesseract"):
            return False
        return importlib.util.find_spec("pytesseract") is not None

    def recognize(self, png_bytes: bytes, languages=("en",)) -> str:
        import pytesseract
        from PIL import Image

        try:
            installed = set(pytesseract.get_languages())
        except Exception:
            installed = {"eng"}
        codes = [TESS_LANGS[l] for l in languages if l in TESS_LANGS and TESS_LANGS[l] in installed] or ["eng"]
        try:
            return pytesseract.image_to_string(Image.open(io.BytesIO(png_bytes)), lang="+".join(codes))
        except Exception as e:
            raise OcrError(f"Tesseract could not read the image ({type(e).__name__})") from None


def get_engine(preference: str = "auto"):
    """Best available engine, or None. preference: "auto" | "vision" | "tesseract"."""
    order = {"vision": [VisionEngine, TesseractEngine], "tesseract": [TesseractEngine, VisionEngine]}.get(
        preference, [VisionEngine, TesseractEngine])
    for cls in order:
        if cls.available():
            return cls()
    return None


def describe() -> str:
    """One line for Settings: what OCR can do on this Mac right now."""
    if VisionEngine.available():
        return "Apple Vision is available."
    if TesseractEngine.available():
        return "Tesseract is available (Apple Vision is not)."
    if sys.platform == "darwin":
        return ("No OCR engine found. Install Apple Vision support with:  "
                "pip install pyobjc-framework-Vision pyobjc-framework-Quartz")
    return "No OCR engine found. Install Tesseract and pytesseract."


def image_to_png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
