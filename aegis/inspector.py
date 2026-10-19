"""
Inspector: the detailed, editable back end of the front window.

It is a view onto the current Session. Edits change the Analysis; once a request has been
sent everything is locked, because re-hydration must use exactly what was transmitted.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QHBoxLayout, QHeaderView, QInputDialog,
    QMenu, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from aegis import diagnostics, stores
from aegis.tips import TIPS
from aegis.ui_dialogs import ENTITY_LABELS, TagHighlighter, button, label

FILTERS = ["Needs attention", "Redacted", "Not redacted", "All"]
SOURCE_NAMES = {"model": "Model", "pattern": "Pattern", "preset": "Your terms",
                "manual": "Manual", "heuristic": "Heuristic"}
SOURCE_TIPS = {"model": "Found by the language model (names, places, organisations)",
               "pattern": "Found by a pattern or checksum (numbers, emails, IBANs…)",
               "preset": "One of your always-redacted terms", "manual": "Added by you",
               "heuristic": "A capitalised word in your request that might be a name"}


def _table(headers, stretch_col):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    h.setHighlightSections(False)
    h.setMinimumSectionSize(34)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.setTextElideMode(Qt.ElideRight)
    return t


class InspectorDock(QDockWidget):
    edited = Signal()                  # the user changed which items are redacted
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("Inspector", parent)
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetMovable)
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.session, self._locked, self._loading = None, False, False
        self._row_ids = []
        self._was_floating = False  # Track floating state

        tabs = QTabWidget()
        tabs.tabBar().setExpanding(False)
        self.tabs = tabs
        tabs.setStyleSheet("QTabBar::tab { min-width: 0px; padding: 5px 10px; }")
        self.setWidget(tabs)
        self.setMinimumWidth(400)

        # ---- Detections
        d = QWidget()
        dl = QVBoxLayout(d)
        dl.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.setToolTip(TIPS["det_filter"])
        self.filter.currentIndexChanged.connect(self._fill_detections)
        top.addWidget(self.filter)
        top.addStretch()
        self.add_btn = button("Add term…", slot=self._add_term)
        self.add_btn.setToolTip(TIPS["det_add"])
        top.addWidget(self.add_btn)
        dl.addLayout(top)
        self.det_table = _table(["", "Text", "Type", "Conf.", "Found by", "In"], 1)
        self.det_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.det_table.customContextMenuRequested.connect(self._row_menu)
        self.det_table.itemChanged.connect(self._item_changed)
        dl.addWidget(self.det_table, 1)
        self.det_hint = label("", "caption", True)
        dl.addWidget(self.det_hint)
        tabs.addTab(d, "Detections")

        # ---- Sent text
        s = QWidget()
        sl = QVBoxLayout(s)
        sl.setContentsMargins(0, 8, 0, 0)
        sl.addWidget(label("Exactly what will be transmitted.", "caption"))
        self.sent_view = QTextEdit()
        self.sent_view.setReadOnly(True)
        self.hl = TagHighlighter(self.sent_view.document())
        sl.addWidget(self.sent_view, 1)
        tabs.addTab(s, "Sent text")

        # ---- Mapping
        m = QWidget()
        ml = QVBoxLayout(m)
        ml.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Placeholder → real value. Held in memory only.", "caption"))
        row.addStretch()
        self.reveal = QCheckBox("Reveal values")
        self.reveal.setToolTip(TIPS["map_reveal"])
        self.reveal.toggled.connect(self._fill_mapping)
        row.addWidget(self.reveal)
        ml.addLayout(row)
        self.map_table = _table(["Placeholder", "Value"], 1)
        ml.addWidget(self.map_table, 1)
        tabs.addTab(m, "Mapping")

        # ---- Run log
        g = QWidget()
        gl = QVBoxLayout(g)
        gl.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Timings, counts and error types only. Never document text.", "caption"))
        row.addStretch()
        rb, eb = button("Refresh", slot=self.refresh_log), button("Export…", slot=self.export_requested.emit)
        rb.setToolTip(TIPS["log_refresh"])
        eb.setToolTip(TIPS["log_export"])
        row.addWidget(rb)
        row.addWidget(eb)
        gl.addLayout(row)
        self.log_table = _table(["Time", "Event", "Stage", "OK", "ms", "Error"], 2)
        gl.addWidget(self.log_table, 1)
        tabs.addTab(g, "Run log")
        tabs.currentChanged.connect(lambda i: self.refresh_log() if i == 3 else None)

    # ------------------------------------------------------------------ public
    def set_dark(self, dark): self.hl.set_dark(dark)

    def show_tab(self, name: str):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)

    def set_session(self, session):
        self.session = session
        self.refresh()

    def set_locked(self, locked: bool):
        self._locked = locked
        self.add_btn.setEnabled(not locked)
        self.refresh()

    def refresh(self):
        s = self.session
        has = bool(s and s.analysis)
        self._fill_detections()
        res = (s.sent_result if (s and s.sent) else s.result) if s else None
        self.sent_view.setPlainText(res.text if res else "")
        self._fill_mapping()
        self.det_hint.setText(
            "Locked: this request was already sent, so its redactions can no longer change. Start a new task to edit."
            if self._locked else
            ("Untick an item to stop redacting it. Tick a flagged item to redact it. Right-click for more." if has else
             "Nothing analysed yet. Ask Aegis something and the detections appear here."))

    # ------------------------------------------------------------------ detections
    def _visible(self, d):
        f = self.filter.currentText()
        return (f == "All" or (f == "Needs attention" and d.uncertain)
                or (f == "Redacted" and d.included) or (f == "Not redacted" and not d.included))

    def _fill_detections(self, *_):
        t = self.det_table
        self._loading = True
        t.setRowCount(0)
        self._row_ids = []
        if self.session and self.session.analysis:
            dets = [d for d in self.session.analysis.detections if self._visible(d)]
            dets.sort(key=lambda d: (d.segment != "instr", d.start))
            t.setRowCount(len(dets))
            for r, d in enumerate(dets):
                self._row_ids.append(d.id)
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled if not self._locked else Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked if d.included else Qt.Unchecked)
                t.setItem(r, 0, chk)
                txt = QTableWidgetItem(d.text if len(d.text) <= 60 else d.text[:57] + "…")
                if d.reason:
                    txt.setToolTip(d.reason)
                t.setItem(r, 1, txt)
                t.setItem(r, 2, QTableWidgetItem(ENTITY_LABELS.get(d.etype, d.etype.title())))
                t.setItem(r, 3, QTableWidgetItem(f"{int(d.score * 100)}%"))
                src = QTableWidgetItem(SOURCE_NAMES.get(d.source, d.source))
                src.setToolTip(SOURCE_TIPS.get(d.source, ""))
                t.setItem(r, 4, src)
                where = QTableWidgetItem("Ask" if d.segment == "instr" else "File")
                where.setToolTip("In your request" if d.segment == "instr" else "In an attachment")
                t.setItem(r, 5, where)
        self._loading = False

    def _item_changed(self, item):
        if self._loading or self._locked or item.column() != 0 or not self.session:
            return
        det_id = self._row_ids[item.row()]
        self.session.analysis.set_included(det_id, item.checkState() == Qt.Checked)
        self.edited.emit()

    def _add_term(self):
        if not (self.session and self.session.analysis) or self._locked:
            return
        term, ok = QInputDialog.getText(self, "Add term", "Word or phrase to always redact in this task:")
        if ok and term.strip():
            n = self.session.analysis.add_term(term.strip())
            self.det_hint.setText(f"Added “{term.strip()}”: {n} occurrence{'s' if n != 1 else ''}." if n
                                  else "That text does not appear in this task.")
            self.edited.emit()

    def _row_menu(self, pos):
        row = self.det_table.rowAt(pos.y())
        if row < 0 or self._locked or not self.session:
            return
        det = self.session.analysis.by_id(self._row_ids[row])
        menu = QMenu(self)
        a_toggle = QAction("Stop redacting this item" if det.included else "Redact this item", menu)
        a_always = QAction("Always redact this value (save to your terms)", menu)
        menu.addAction(a_toggle)
        menu.addAction(a_always)
        chosen = menu.exec(self.det_table.viewport().mapToGlobal(pos))
        if chosen is a_toggle:
            self.session.analysis.set_included(det.id, not det.included)
            self.edited.emit()
        elif chosen is a_always:
            try:
                stores.save_presets(stores.load_presets() + [det.text])
            except RuntimeError as e:
                self.det_hint.setText(str(e))
                return
            self.session.analysis.add_term(det.text, "PRESET")
            self.edited.emit()

    # ------------------------------------------------------------------ mapping / log
    def _fill_mapping(self, *_):
        t = self.map_table
        t.setRowCount(0)
        s = self.session
        res = (s.sent_result if (s and s.sent) else (s.result if s else None))
        if not res:
            return
        show = self.reveal.isChecked()
        t.setRowCount(len(res.mapping))
        for r, (tag, val) in enumerate(res.mapping.items()):
            t.setItem(r, 0, QTableWidgetItem(tag))
            t.setItem(r, 1, QTableWidgetItem(val if show else "•" * min(len(val), 12)))

    def refresh_log(self):
        t = self.log_table
        t.setRowCount(0)
        rows = []
        try:
            with open(diagnostics.LOG_FILE, "r", encoding="utf-8") as f:
                for line in f.readlines()[-300:]:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if "event" in rec:
                        rows.append(rec)
        except OSError:
            pass
        rows = rows[-150:][::-1]
        t.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            vals = [rec.get("ts", "")[11:19], rec.get("event", ""), rec.get("stage", ""),
                    "" if "ok" not in rec else ("yes" if rec["ok"] else "no"),
                    str(rec.get("duration_ms", "")), rec.get("error_class", "")]
            for c, v in enumerate(vals):
                t.setItem(r, c, QTableWidgetItem(v))
                if c == 3 and v == "no":
                    t.item(r, c).setForeground(Qt.red)
