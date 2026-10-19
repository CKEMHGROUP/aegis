"""Background workers: nothing slow runs on the UI thread."""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from aegis import diagnostics


class FnWorker(QThread):
    """Runs fn(*args) off the UI thread. Emits done(result) or failed(error_class, message)."""
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, name, fn, *args, log_fields=None, parent=None):
        super().__init__(parent)
        self._name, self._fn, self._args = name, fn, args
        self._log_fields = log_fields or {}

    def run(self):
        t0 = time.perf_counter()
        try:
            result = self._fn(*self._args)
        except Exception as e:
            diagnostics.event(
                "stage", stage=self._name, ok=False, error_class=type(e).__name__,
                duration_ms=int((time.perf_counter() - t0) * 1000), **self._log_fields)
            self.failed.emit(type(e).__name__, str(e))
            return
        diagnostics.event(
            "stage", stage=self._name, ok=True,
            duration_ms=int((time.perf_counter() - t0) * 1000), **self._log_fields)
        self.done.emit(result)


class BatchWorker(QThread):
    """Runs a batch function fn(progress_callback). Emits progress(done, total, text), then done or failed."""
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._name, self._fn = "batch", fn

    def run(self):
        t0 = time.perf_counter()
        try:
            result = self._fn(lambda i, n, msg: self.progress.emit(i, n, msg))
        except Exception as e:
            diagnostics.event("stage", stage="batch", ok=False, error_class=type(e).__name__,
                              duration_ms=int((time.perf_counter() - t0) * 1000))
            self.failed.emit(type(e).__name__, str(e))
            return
        diagnostics.event("stage", stage="batch", ok=True, duration_ms=int((time.perf_counter() - t0) * 1000),
                          files=len(result.outcomes), ok_files=result.ok_count,
                          failed_files=len(result.outcomes) - result.ok_count,
                          uncertain_hidden=result.total_uncertain, entity_total=result.total_hidden)
        self.done.emit(result)
