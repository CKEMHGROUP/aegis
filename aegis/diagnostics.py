"""
Aegis diagnostics: local, content-free logging and crash capture.

Design rules (enforced in code, not by convention):
  1. Only allow-listed field names are ever written, and only int/float/bool
     or short strings that match a safe pattern. Free text is dropped.
  2. Exception *messages* are never logged (libraries often echo input).
     Only the exception class and a stack of file:function:line are kept.
  3. No local variables, no document text, no tags, no hydration_map.
  4. Everything stays on this Mac. Export is a manual, user-reviewed action.
"""
import faulthandler
import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
import zipfile

APP_NAME = "AegisPrivacyAirlock"
LOG_DIR = os.path.expanduser(f"~/Library/Logs/{APP_NAME}")
LOG_FILE = os.path.join(LOG_DIR, "aegis.log")
FAULT_FILE = os.path.join(LOG_DIR, "native_crash.log")

# Only these keys may appear in an event. Add keys deliberately.
ALLOWED_FIELDS = {
    "stage", "provider", "duration_ms", "ok", "error_class", "entity_counts",
    "orphan_tags", "doc_type", "char_count", "retry", "app_version", "region",
    "http_status", "frames", "language", "degraded", "unresolved", "entity_total", "model", "mode", "files", "ok_files", "failed_files", "uncertain_hidden", "ocr_pages", "engine",
}
_SAFE_STR = re.compile(r"^[A-Za-z0-9_.:/ \-()]{0,64}$")
SESSION_ID = uuid.uuid4().hex[:8]

_logger = logging.getLogger("aegis.diag")
_fault_handle = None


def _clean(value, depth=0):
    """Return a JSON-safe, content-free version of value, or None to drop it."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if _SAFE_STR.match(value) else "[dropped]"
    if depth < 2 and isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SAFE_STR.match(k):
                c = _clean(v, depth + 1)
                if c is not None:
                    out[k] = c
        return out
    if depth < 2 and isinstance(value, (list, tuple)):
        return [c for c in (_clean(v, depth + 1) for v in value[:50]) if c is not None]
    return None


def event(name, **fields):
    """Log a structured, allow-listed event."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": SESSION_ID, "event": name}
    for k, v in fields.items():
        if k in ALLOWED_FIELDS:
            c = _clean(v)
            if c is not None:
                record[k] = c
    _logger.info(json.dumps(record, separators=(",", ":")))


def _safe_frames(tb, limit=25):
    frames = []
    for fs in traceback.extract_tb(tb)[-limit:]:
        frames.append(f"{os.path.basename(fs.filename)}:{fs.name.strip('<>')}:{fs.lineno}")
    return frames


def log_exception(exc_type, exc_value, exc_tb, where="unhandled"):
    event(
        "exception",
        stage=where,
        error_class=getattr(exc_type, "__name__", "Unknown"),
        frames=_safe_frames(exc_tb),
    )


def _excepthook(exc_type, exc_value, exc_tb):
    log_exception(exc_type, exc_value, exc_tb, "main_thread")
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_hook(args):
    log_exception(args.exc_type, args.exc_value, args.exc_traceback, "thread")


def _install_qt_handler():
    try:
        from PySide6.QtCore import qInstallMessageHandler, QtMsgType
    except Exception:
        return

    levels = {
        QtMsgType.QtWarningMsg: "qt_warning",
        QtMsgType.QtCriticalMsg: "qt_critical",
        QtMsgType.QtFatalMsg: "qt_fatal",
    }

    def handler(mode, context, message):
        kind = levels.get(mode)
        if kind:  # message text is dropped on purpose; category + source only
            event(kind, stage=os.path.basename(context.file or "") + ":" + str(context.line))

    qInstallMessageHandler(handler)


def init(app_version="0.0.0"):
    """Call once, before QApplication is created."""
    global _fault_handle
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=512_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    _logger.addHandler(handler)
    os.chmod(LOG_DIR, 0o700)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    _fault_handle = open(FAULT_FILE, "a", encoding="utf-8")  # native crashes: C-level stacks only
    faulthandler.enable(file=_fault_handle, all_threads=True)
    _install_qt_handler()

    event("app_start", app_version=app_version)
    _logger.info(json.dumps({"env": {"python": platform.python_version(), "macos": platform.mac_ver()[0]}}))


class stage:
    """Context manager: times a pipeline stage and records ok/error_class only."""

    def __init__(self, name, **fields):
        self.name, self.fields = name, fields

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = int((time.perf_counter() - self.t0) * 1000)
        event(
            "stage",
            stage=self.name,
            duration_ms=ms,
            ok=exc_type is None,
            error_class=exc_type.__name__ if exc_type else None,
            **{k: v for k, v in self.fields.items() if k != "stage"},
        )
        return False  # never swallow


def export_diagnostics(dest_zip):
    """Bundle logs for manual sharing. The UI must show the user what is inside first."""
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(LOG_DIR)):
            p = os.path.join(LOG_DIR, name)
            if os.path.isfile(p):
                z.write(p, name)
    return dest_zip
