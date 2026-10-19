"""The Folders view: drop a folder, see what is new or changed, make a one-way shareable copy."""
from __future__ import annotations

import base64
import os
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QMessageBox, QProgressBar, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from aegis import batch, diagnostics, ocr, redaction, stores
from aegis.tips import TIPS
from aegis.ui_dialogs import TagHighlighter, button, disabled_categories, label
from aegis.workers import BatchWorker, FnWorker

POLL_MS = 15_000


def _key_store(batch_id: str, key: bytes):
    stores.set_secret(f"BatchKey-{batch_id}", base64.b64encode(key).decode("ascii"))


def _key_loader(batch_id: str):
    raw = stores.get_secret(f"BatchKey-{batch_id}")
    return base64.b64decode(raw) if raw else None


class PreviewDialog(QDialog):
    def __init__(self, parent, title, text, dark):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(label("Exactly what a colleague would see", "caption"))
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        self._hl = TagHighlighter(view.document(), dark)
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Close", "primary", self.accept))
        lay.addLayout(row)


class FoldersPage(QWidget):
    COLS = ["File", "Change", "Result", "Hidden", "Copy"]

    def __init__(self, get_engine, ocr_config, version, is_dark, on_note):
        super().__init__()
        self._get_engine, self._ocr_config, self._version = get_engine, ocr_config, version
        self._is_dark, self._note = is_dark, on_note
        self.folder, self.manifest, self.states = "", None, []
        self.results = {}            # rel -> FileOutcome of the latest batch
        self.last = None             # latest BatchResult
        self._busy = False
        self._workers = set()
        self._cancel = threading.Event()
        self._failed = {}            # rel -> sha256 of a file that failed: not retried until its content changes

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        root.addWidget(self.pages)

        # ---- page 0: drop target
        drop = QWidget()
        dl = QVBoxLayout(drop)
        dl.addStretch(3)
        h = label("Sanitise a whole folder", "h1")
        h.setAlignment(Qt.AlignCenter)
        dl.addWidget(h)
        sub = label("Drop a folder anywhere in this window. Aegis makes a clean copy of every file that you can "
                    "share. Nothing is sent to any AI or any server, and your originals are never changed.",
                    "muted", True)
        sub.setAlignment(Qt.AlignCenter)
        sub.setMaximumWidth(560)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(sub, 100)
        row.addStretch()
        dl.addLayout(row)
        dl.addSpacing(8)
        choose = button("Choose folder…", "primary", self.choose_folder)
        choose.setToolTip(TIPS["folder_choose"])
        crow = QHBoxLayout()
        crow.addStretch()
        crow.addWidget(choose)
        crow.addStretch()
        dl.addLayout(crow)
        cap = label("Share only the sanitised copy, never the original folder.", "caption")
        cap.setAlignment(Qt.AlignCenter)
        dl.addWidget(cap)
        dl.addStretch(4)
        self.pages.addWidget(drop)

        # ---- page 1: folder view
        view = QWidget()
        vl = QVBoxLayout(view)
        vl.setContentsMargins(20, 16, 20, 14)
        vl.setSpacing(10)
        head = QHBoxLayout()
        self.folder_label = label("", "title")
        head.addWidget(self.folder_label, 1)
        self.change_btn = button("Change…", slot=self.choose_folder)
        self.change_btn.setToolTip(TIPS["folder_choose"])
        self.rescan_btn = button("Rescan", slot=lambda: self.rescan(manual=True))
        self.rescan_btn.setToolTip(TIPS["folder_rescan"])
        head.addWidget(self.change_btn)
        head.addWidget(self.rescan_btn)
        vl.addLayout(head)
        self.where_label = label("", "caption", True)
        vl.addWidget(self.where_label)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setToolTip(TIPS["folder_table"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.cellDoubleClicked.connect(self._preview)
        vl.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.go_btn = button("Sanitise", "primary", self.sanitize)
        self.go_btn.setToolTip(TIPS["folder_sanitize"])
        self.reveal_btn = button("Show sanitised folder", slot=self.reveal)
        self.reveal_btn.setToolTip(TIPS["folder_reveal"])
        self.reveal_btn.setEnabled(False)
        self.restore_btn = button("Restore a returned file…", slot=self.restore)
        self.restore_btn.setToolTip(TIPS["folder_restore"])
        for b in (self.go_btn, self.reveal_btn, self.restore_btn):
            bar.addWidget(b)
        bar.addStretch()
        self.key_check = QCheckBox("Keep an encrypted key to restore real values later")
        self.key_check.setToolTip(TIPS["folder_key"])
        bar.addWidget(self.key_check)
        vl.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        vl.addWidget(self.progress)
        self.summary = label("", "muted", True)
        vl.addWidget(self.summary)
        self.pages.addWidget(view)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_tick)

    # ------------------------------------------------------------------ settings
    def _s(self):
        return stores.settings()

    def out_root(self) -> str:
        return self._s().value("folders/out_dir", "") or stores.default_out_dir()

    def reload_settings(self):
        if self.folder:
            self.where_label.setText(f"Sanitised copies are saved in {self.out_root()}. Originals are never changed.")
        watching = self._s().value("folders/watch", True, type=bool)
        if watching and self.folder and self.isVisible():
            self._poll.start(POLL_MS)
        else:
            self._poll.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self.reload_settings()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._poll.stop()

    # ------------------------------------------------------------------ choosing and scanning
    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to sanitise", self.folder or os.path.expanduser("~"))
        if path:
            self.set_folder(path)

    def set_folder(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return
        self.folder, self.results, self.last = path, {}, None
        self.manifest = batch.Manifest(path)
        self.folder_label.setText(os.path.basename(path.rstrip(os.sep)) or path)
        self.folder_label.setToolTip(path)
        self.summary.setText("")
        self.pages.setCurrentIndex(1)
        self.reload_settings()
        self.rescan(manual=True, first=True)

    def rescan(self, manual=False, first=False):
        if self._busy or not self.folder:
            return
        recursive = self._s().value("folders/recursive", True, type=bool)
        if manual:
            self.summary.setText("Checking the folder…")
        w = FnWorker("folder_scan", batch.scan_folder, self.folder, self.manifest, recursive)
        self._workers.add(w)
        w.done.connect(lambda states: self._on_scanned(states, manual, first))
        w.failed.connect(lambda cls, msg: self.summary.setText(f"Could not read the folder: {msg}"))
        w.finished.connect(lambda w=w: self._workers.discard(w))
        w.start()

    def _on_scanned(self, states, manual, first):
        old = {(s.rel, s.status) for s in self.states}
        self.states = states
        self._fill_table()
        todo = self._auto_todo()
        changed = {(s.rel, s.status) for s in states} != old
        if any(s.status in (batch.STATUS_RENAMED, batch.STATUS_MISSING) for s in states):
            batch.apply_renames_and_removals(self.manifest, states)
        if manual and not self.last and not todo:
            self.summary.setText("Everything in this folder is already sanitised." if states else
                                 "No supported files here. PDF, Word, text and image files are supported.")
        auto = self._s().value("folders/auto", True, type=bool)
        if todo and auto and (first or changed):
            self.sanitize(auto=True)
        elif changed and not first and todo:
            self._note(f"{len(todo)} new or changed file{'s' if len(todo) != 1 else ''} in the folder.")

    def _auto_todo(self):
        """New or changed files, minus files that already failed with exactly this content."""
        return [s for s in batch.pending(self.states) if self._failed.get(s.rel) != s.sha256]

    def _fill_table(self):
        t = self.table
        t.setRowCount(len(self.states))
        for r, s in enumerate(self.states):
            out = self.results.get(s.rel)
            result = "—"
            if out:
                result = "Sanitised" if out.status == "ok" else "Failed"
                if out.status == "ok" and out.ocr_pages:
                    result += " (OCR)"
            elif s.status == batch.STATUS_UNCHANGED:
                result = "Sanitised earlier"
            cells = [s.rel if not s.prev_rel else f"{s.prev_rel}  →  {s.rel}", batch.CHANGE_LABELS[s.status], result,
                     str(out.hidden) if out and out.status == "ok" else "", out.output if out and out.output else ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if out and out.status == "failed":
                    item.setToolTip(out.error)
                    if c == 2:
                        item.setForeground(Qt.red)
                t.setItem(r, c, item)
        n = len(batch.pending(self.states))
        self.go_btn.setText(f"Sanitise {n} file{'s' if n != 1 else ''}" if n else "Sanitise")
        self.go_btn.setEnabled(bool(n) and not self._busy)

    # ------------------------------------------------------------------ sanitising
    def sanitize(self, auto=False):
        todo = self._auto_todo() if auto else batch.pending(self.states)   # the button retries failures too
        engine = self._get_engine()
        if self._busy or not todo or engine is None:
            if engine is None:
                self._note("The detection engine is still loading.")
            return
        s = self._s()
        ocr_engine, langs = self._ocr_config()
        region = s.value("region", redaction.AUTO_REGION)
        presets, disabled = tuple(stores.load_presets()), disabled_categories()
        keep = self.key_check.isChecked()
        out_root, manifest, folder, version = self.out_root(), self.manifest, self.folder, self._version
        self._cancel.clear()
        cancel = self._cancel

        def job(progress):
            return batch.sanitize_batch(engine, folder, todo, out_root, manifest, region=region, presets=presets,
                                        disabled=disabled, ocr=ocr_engine, ocr_languages=langs, keep_key=keep,
                                        key_store=_key_store, progress=progress, app_version=version, cancel=cancel)

        self._busy = True
        self.go_btn.setEnabled(False)
        self.rescan_btn.setEnabled(False)
        self.progress.setRange(0, len(todo))
        self.progress.setValue(0)
        self.progress.show()
        self.summary.setText(f"Sanitising {len(todo)} file{'s' if len(todo) != 1 else ''}…")
        w = BatchWorker(job)
        self._workers.add(w)
        w.progress.connect(lambda i, n, msg: (self.progress.setValue(i), self.summary.setText(msg)))
        w.done.connect(self._on_batch_done)
        w.failed.connect(self._on_batch_failed)
        w.finished.connect(lambda w=w: self._workers.discard(w))
        w.start()

    def _on_batch_failed(self, cls, msg):
        self._busy = False
        self.progress.hide()
        self.rescan_btn.setEnabled(True)
        self._fill_table()
        self.summary.setText("The batch stopped before it finished. Nothing was shared.")
        QMessageBox.warning(self, "Batch failed", msg)

    def _on_batch_done(self, res):
        self._busy = False
        self.progress.hide()
        self.rescan_btn.setEnabled(True)
        self.last = res
        sha = {st.rel: st.sha256 for st in self.states}
        for o in res.outcomes:
            self.results[o.rel] = o
            if o.status == "failed":
                self._failed[o.rel] = sha.get(o.rel)
            else:
                self._failed.pop(o.rel, None)
        self.reveal_btn.setEnabled(True)
        failed = len(res.outcomes) - res.ok_count
        text = (f"{res.ok_count} of {len(res.outcomes)} files sanitised · {res.total_hidden} items hidden")
        if res.total_uncertain:
            text += f" ({res.total_uncertain} unsure items were hidden to be safe)"
        text += ". Nothing was sent anywhere."
        if failed:
            text += f"  {failed} file{'s' if failed != 1 else ''} failed: hover the red rows."
        if res.key_kept:
            text += "  An encrypted key was kept on this Mac."
        self.summary.setText(text)
        self._note("Batch finished. Share the sanitised folder, never the originals.")
        self.rescan()          # refresh statuses: processed files are now "Unchanged"

    # ------------------------------------------------------------------ actions
    def reveal(self):
        target = self.last.out_dir if self.last else self.out_root()
        os.makedirs(target, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _preview(self, row, _col):
        if row >= len(self.states) or self.manifest is None:
            return
        entry = self.manifest.files.get(self.states[row].rel)
        if not entry or not entry.get("output"):
            return
        path = os.path.join(batch.out_dir_for(self.out_root(), entry["batch"]), entry["output"])
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            self._note("That sanitised file is no longer in the output folder.")
            return
        PreviewDialog(self, entry["output"], text, self._is_dark()).exec()

    def restore(self):
        ids = batch.list_restorable_batches()
        if not ids:
            QMessageBox.information(self, "Nothing to restore",
                                    "No batch has a saved key. Tick “Keep an encrypted key” before sanitising "
                                    "if you want to restore real values later.")
            return
        bid, ok = QInputDialog.getItem(self, "Restore real values", "Which batch was the file made from?", ids, 0, False)
        if not ok:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose the returned file", "", "Text files (*.txt *.md)")
        if not path:
            return
        try:
            mapping = batch.load_batch_mapping(bid, _key_loader)
        except Exception as e:
            QMessageBox.warning(self, "Cannot restore", f"The key or data for that batch is damaged ({type(e).__name__}).")
            return
        if mapping is None:
            QMessageBox.warning(self, "Cannot restore", "The key for that batch is not on this Mac.")
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            restored = batch.restore_text(f.read(), mapping)
        suggested = os.path.splitext(os.path.basename(path))[0] + " (restored).txt"
        dest, _ = QFileDialog.getSaveFileName(self, "Save the restored file", suggested, "Text (*.txt)")
        if dest:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(restored)
            diagnostics.event("restore", ok=True)
            self._note("Restored. That file contains real values: keep it inside your work environment.")

    def _poll_tick(self):
        if not self._busy and self.folder and self.isVisible():
            self.rescan()

    def shutdown(self):
        """Called when the window closes: stop a running batch between files and wait for threads."""
        self._cancel.set()
        self._poll.stop()
        for w in list(self._workers):
            w.wait(5000)

    def ocr_description(self) -> str:
        return ocr.describe()
