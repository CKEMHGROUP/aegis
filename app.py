"""
Aegis Privacy Airlock v0.13.3

Front window: a chat-style composer. Back end: an inspector you can open to see and edit
every detection, the exact text that is sent, the placeholder mapping and the run log.
"""
import os
import sys

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QGuiApplication, QKeySequence, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from aegis import diagnostics, documents, mailbox, ocr, providers, redaction, stores, theme
from aegis.folders_ui import FoldersPage
from aegis.inspector import InspectorDock
from aegis.session import Attachment, Session
from aegis.tips import TIPS
from aegis.ui_dialogs import (
    ENTITY_LABELS, DataHandlingDialog, InboxDialog, PresetsDialog, SettingsDialog, TagHighlighter,
    button, disabled_categories, label, pill, repolish,
)
from aegis.widgets import Chip, Composer
from aegis.workers import FnWorker

APP_VERSION = "0.13.3"
COLUMN_WIDTH = 800

SUGGESTIONS = [
    ("Summarise a document", "Summarise this document and list the key points and any action items.", "document"),
    ("Digest my inbox", "Give me an executive briefing of these emails: what needs a reply, deadlines and priorities.", "inbox"),
    ("Draft a reply", "Draft a polite, concise reply to this message.", None),
    ("Review for risks", "Review this for risks, unclear terms and missing information.", "document"),
]


def build_palette(dark: bool) -> QPalette:
    t = theme.tokens(dark)
    p = QPalette()
    for role, key in [
        (QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
        (QPalette.AlternateBase, "surface_alt"), (QPalette.Text, "text"), (QPalette.Button, "surface"),
        (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "accent_text"),
        (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "text3"),
    ]:
        p.setColor(role, QColor(t[key]))
    return p


def centered(widget, max_width=COLUMN_WIDTH) -> QWidget:
    """Wrap widget in a row that keeps it at a readable width, like a chat column."""
    widget.setMaximumWidth(max_width)
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch(1)
    lay.addWidget(widget, 100)
    lay.addStretch(1)
    return row


def summary_text(counts: dict) -> str:
    total = sum(counts.values())
    parts = ", ".join(f"{ENTITY_LABELS.get(k, k.title())} {v}"
                      for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"<b>{total}</b> item{'s' if total != 1 else ''} will be hidden: {parts}."


# ----------------------------------------------------------------------------- thread cards
class ReviewCard(QFrame):
    """Inline review gate. Always visible: the user approves what leaves the Mac."""

    def __init__(self, session, dest, languages, on_edit, on_inspect, on_cancel, on_send, on_language):
        super().__init__()
        self.setProperty("card", True)
        self.session, self.dest, self._on_edit = session, dest, on_edit
        self.checks = {}
        lay = self._lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(label("Review before sending", "title"))
        head.addStretch()
        head.addWidget(pill("Stays on this Mac" if dest.is_local else "Leaves this Mac",
                            "local" if dest.is_local else "warn"))
        lay.addLayout(head)
        lay.addWidget(label(f"To {dest.label} · {dest.host} · {dest.model}", "caption"))

        self.summary = label("", None, True)
        lay.addWidget(self.summary)
        if len(languages) > 1:
            lrow = QHBoxLayout()
            how = {"detected": "detected", "default": "default", "chosen": "chosen"}[session.analysis.language_source]
            lrow.addWidget(label(f"Language ({how}):", "caption"))
            self.lang = QComboBox()
            self.lang.setProperty("kind", "ghost")
            for code in languages:
                self.lang.addItem(redaction.LANG_NAMES.get(code, code), code)
            self.lang.setCurrentIndex(max(0, self.lang.findData(session.analysis.language)))
            self.lang.setToolTip(TIPS["review_language"])
            self.lang.activated.connect(lambda _i: on_language(self.lang.currentData()))
            lrow.addWidget(self.lang)
            lrow.addStretch()
            lay.addLayout(lrow)
        self.degraded = label("The language model for this region is not installed, so names and places were "
                              "found with the English model. Expect more misses.", "muted", True)
        self.degraded.setVisible(session.result.degraded)
        lay.addWidget(self.degraded)

        attn = session.analysis.needs_attention()
        self.attn_host = QWidget()
        al = QVBoxLayout(self.attn_host)
        al.setContentsMargins(0, 4, 0, 0)
        al.setSpacing(2)
        if attn:
            al.addWidget(label("Please check these. They could be sensitive:", "caption"))
            for d in attn[:6]:
                row = QHBoxLayout()
                text = d.text if len(d.text) <= 48 else d.text[:45] + "…"
                cb = QCheckBox(f"“{text}”")
                cb.setChecked(d.included)
                cb.setToolTip(d.reason + "\nTicked = hidden before sending.")
                cb.toggled.connect(lambda on, i=d.id: self._toggled(i, on))
                self.checks[d.id] = cb
                row.addWidget(cb)
                row.addWidget(label(d.reason, "caption"))
                row.addStretch()
                al.addLayout(row)
            if len(attn) > 6:
                al.addWidget(label(f"+ {len(attn) - 6} more in the Inspector", "caption"))
        self.attn_host.setVisible(bool(attn))
        lay.addWidget(self.attn_host)

        self.btn_row = QWidget()
        bl = QHBoxLayout(self.btn_row)
        bl.setContentsMargins(0, 4, 0, 0)
        insp = button("Inspect details", "quiet", on_inspect)
        insp.setToolTip(TIPS["review_inspect"])
        bl.addWidget(insp)
        bl.addStretch()
        cancel = button("Cancel", slot=on_cancel)
        cancel.setToolTip(TIPS["review_cancel"])
        bl.addWidget(cancel)
        send = button(f"Send to {dest.label}", "primary", on_send)
        send.setToolTip(TIPS["review_send"])
        send.setDefault(True)
        bl.addWidget(send)
        lay.addWidget(self.btn_row)
        self.refresh()

    def add_note(self, text):
        self._lay.insertWidget(3, label(text, "muted", True))

    def _toggled(self, det_id, on):
        self.session.analysis.set_included(det_id, on)
        self._on_edit()

    def refresh(self):
        res = self.session.result
        if sum(res.counts.values()):
            self.summary.setProperty("role", None)
            self.summary.setText(summary_text(res.counts))
        else:
            self.summary.setProperty("role", "warn")
            self.summary.setText("No sensitive items are being hidden. Detection is not perfect: check the "
                                 "Inspector before sending.")
        repolish(self.summary)

    def sync_checks(self):
        for det_id, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(self.session.analysis.by_id(det_id).included)
            cb.blockSignals(False)

    def lock(self, text):
        self.btn_row.hide()
        self.attn_host.hide()
        self.summary.setProperty("role", None)
        self.summary.setText(text)
        repolish(self.summary)


class AnswerCard(QFrame):
    def __init__(self, final: str, raw: str, model: str, dark: bool, on_fill, on_copy, on_save):
        super().__init__()
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(False)
        self.final = QTextEdit()
        self.final.setReadOnly(True)
        self.final.setPlainText(final)
        self.final.setMinimumHeight(240)
        self.tabs.addTab(self.final, "Answer")
        self.raw = QTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setPlainText(raw)
        self.raw.setMinimumHeight(240)
        self.hl = TagHighlighter(self.raw.document(), dark)
        self.tabs.addTab(self.raw, "What the AI said (tagged)")
        self.tabs.setTabToolTip(1, TIPS["answer_raw"])
        lay.addWidget(self.tabs)
        bar = QHBoxLayout()
        self.fill_btn = button("Fill in…", slot=on_fill)
        self.fill_btn.setToolTip(TIPS["answer_fill"])
        bar.addWidget(self.fill_btn)
        copy_b, save_b = button("Copy", slot=on_copy), button("Save…", slot=on_save)
        copy_b.setToolTip(TIPS["answer_copy"])
        save_b.setToolTip(TIPS["answer_save"])
        bar.addWidget(copy_b)
        bar.addWidget(save_b)
        bar.addStretch()
        self.pill = pill("", "warn")
        self.pill.hide()
        bar.addWidget(self.pill)
        lay.addLayout(bar)
        lay.addWidget(label(f"Answered by {model}", "caption"))
        self.update_fill_state()

    def update_fill_state(self):
        left = redaction.find_fill_candidates(self.final.toPlainText())
        self.pill.setVisible(bool(left))
        self.pill.setText(f"{len(left)} to fill")
        self.fill_btn.setEnabled(bool(left))


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aegis Privacy Airlock")
        self.resize(1040, 780)
        self.setMinimumSize(720, 560)
        self.setAcceptDrops(True)

        self.engine = None
        self.session = Session()
        self._last_session = None  # Store last session for redo
        self.review = None
        self.answer = None
        self._working = None
        self._workers = set()
        self.dark = False
        self._language_override = None
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._on_idle)
        QApplication.instance().installEventFilter(self)

        stores.migrate_legacy_account()
        self._build_ui()
        self._build_menu()
        self._build_topbar_actions()
        self.apply_theme()
        self.refresh_provider_status()
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme)

        self.composer.set_busy(True)
        self.note("Loading detection engine…", sticky=True)
        self._start(FnWorker("engine_load", redaction.build_engine), self._on_engine_ready, self._on_engine_failed)
        self._restart_idle()
        if stores.legacy_presets_available():
            QTimer.singleShot(1500, lambda: self.note(
                "An old plain-text terms file was found. Move it to the Keychain in Settings > Privacy.", sticky=True))

    # ------------------------------------------------------------------ construction
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.topbar = QFrame()
        self.topbar.setObjectName("TopHeader")
        self.top_layout = QHBoxLayout(self.topbar)
        self.top_layout.setContentsMargins(16, 8, 12, 8)
        self.top_layout.setSpacing(6)
        self.top_layout.addWidget(label("Aegis", "title"))
        self.top_layout.addSpacing(12)
        self.mode_group = QButtonGroup(self)
        self.mode_btns = []
        for i, (text, tip) in enumerate((("Ask", TIPS["mode_ask"]), ("Folders", TIPS["mode_folders"]))):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setProperty("kind", "bar")
            b.setToolTip(tip)
            b.setChecked(i == 0)
            self.mode_group.addButton(b, i)
            self.mode_btns.append(b)
            self.top_layout.addWidget(b)
        self.mode_group.idClicked.connect(self.set_mode)
        self.top_layout.addStretch()
        root.addWidget(self.topbar)

        self.mode_stack = QStackedWidget()
        root.addWidget(self.mode_stack, 1)
        ask_page = QWidget()
        ask_layout = QVBoxLayout(ask_page)
        ask_layout.setContentsMargins(0, 0, 0, 0)
        ask_layout.setSpacing(0)
        self.stack = QStackedWidget()
        ask_layout.addWidget(self.stack, 1)

        # page 0: empty state
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(3)
        h1 = label("What would you like to do?", "h1")
        h1.setAlignment(Qt.AlignCenter)
        sub = label("Drop a file, paste text, or attach your inbox. Personal data is hidden on this Mac "
                    "before anything is sent, and you review it first.", "muted", True)
        sub.setAlignment(Qt.AlignCenter)
        el.addWidget(h1)
        el.addWidget(centered(sub, 520))
        el.addSpacing(10)
        chips = QHBoxLayout()
        chips.addStretch()
        for text, prompt, source in SUGGESTIONS:
            b = QPushButton(text)
            b.setProperty("kind", "suggest")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=prompt, s=source: self.use_suggestion(p, s))
            chips.addWidget(b)
        chips.addStretch()
        el.addLayout(chips)
        el.addStretch(4)
        self.stack.addWidget(empty)

        # page 1: thread
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.thread = QVBoxLayout(inner)
        self.thread.setContentsMargins(20, 18, 20, 12)
        self.thread.setSpacing(12)
        self.thread.addStretch(1)
        self.scroll.setWidget(inner)
        self.stack.addWidget(self.scroll)

        # composer
        self.caption_row = QHBoxLayout()
        self.caption_row.addStretch()
        self.caption = label("", "caption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption_row.addWidget(self.caption)
        self.caption_row.addStretch()
        self.composer = Composer()
        self.composer.submitted.connect(self.submit)
        self.composer.attach_requested.connect(self.on_attach_requested)
        self.composer.attachment_removed.connect(self._remove_attachment)
        self.composer.files_dropped.connect(self.attach_documents)
        foot = QVBoxLayout()
        foot.setContentsMargins(20, 6, 20, 8)
        foot.setSpacing(4)
        foot.addLayout(self.caption_row)
        foot.addWidget(centered(self.composer))
        ask_layout.addLayout(foot)
        self.mode_stack.addWidget(ask_page)
        self.folders = FoldersPage(lambda: self.engine, self._ocr_config, APP_VERSION,
                                   lambda: self.dark, self.note)
        self.mode_stack.addWidget(self.folders)

        # inspector
        self.inspector = InspectorDock(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector)
        self.inspector.hide()
        self.inspector.edited.connect(self._on_edited)
        self.inspector.export_requested.connect(self.export_diagnostics)
        self.inspector.set_session(self.session)

    def _act(self, text, slot, shortcut=None, role=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if role is not None:
            a.setMenuRole(role)
        return a

    def _build_menu(self):
        self.a_prefs = self._act("Settings…", self.open_settings, "Ctrl+,", QAction.PreferencesRole)
        self.a_terms = self._act("Always-Redacted Terms…", self.open_presets)
        self.a_data = self._act("Data Handling…", lambda: DataHandlingDialog(self).exec())
        self.a_export = self._act("Export Diagnostics…", self.export_diagnostics)
        self.a_logs = self._act("Open Log Folder",
                                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(diagnostics.LOG_DIR)))
        self.a_new = self._act("New Task", self.new_task, "Ctrl+N")
        mb = self.menuBar()
        m = mb.addMenu("Aegis")
        for a in (self.a_prefs, self.a_terms):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_data, self.a_export, self.a_logs):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self._act("Quit Aegis", QApplication.quit, "Ctrl+Q", QAction.QuitRole))
        f = mb.addMenu("File")
        f.addAction(self.a_new)
        f.addAction(self._act("Redo Last Task", self.redo_task, "Ctrl+R"))
        f.addAction(self._act("Attach Document…", lambda: self.choose_documents(), "Ctrl+O"))
        f.addAction(self._act("Attach Inbox Digest…", self.attach_inbox, "Ctrl+Shift+M"))
        f.addSeparator()
        f.addAction(self._act("Save Answer…", self.save_result, "Ctrl+S"))
        v = mb.addMenu("View")
        self.a_inspector = self.inspector.toggleViewAction()
        self.a_inspector.setText("Inspector")
        self.a_inspector.setShortcut(QKeySequence("Ctrl+I"))
        self.a_inspector.setToolTip(TIPS["inspector"])
        v.addAction(self._act("Ask", lambda: self.set_mode(0), "Ctrl+1"))
        v.addAction(self._act("Folders", lambda: self.set_mode(1), "Ctrl+2"))
        v.addSeparator()
        v.addAction(self.a_inspector)

    def _build_topbar_actions(self):
        lay = self.top_layout
        self.provider_btn = QToolButton()
        self.provider_btn.setProperty("kind", "bar")
        self.provider_btn.setPopupMode(QToolButton.InstantPopup)
        self.provider_btn.setToolTip(TIPS["provider"])
        menu = QMenu(self.provider_btn)
        group = QActionGroup(self)
        group.setExclusive(True)
        current = stores.settings().value("provider", providers.GEMINI)
        self._provider = current if current in providers.PROVIDERS else providers.GEMINI
        for key, name in providers.PROVIDERS.items():
            a = QAction(name, self, checkable=True)
            a.setChecked(key == self._provider)
            a.triggered.connect(lambda _=False, k=key: self._set_provider(k))
            group.addAction(a)
            menu.addAction(a)
        self.provider_btn.setMenu(menu)
        lay.addWidget(self.provider_btn)
        self.provider_pill = pill("", "ok")
        lay.addWidget(self.provider_pill)
        lay.addSpacing(8)
        self._ask_only = [self.provider_btn, self.provider_pill]

        new_btn = QToolButton()
        new_btn.setDefaultAction(self.a_new)
        new_btn.setProperty("kind", "bar")
        new_btn.setToolTip(TIPS["new_task"])
        lay.addWidget(new_btn)
        self._ask_only.append(new_btn)

        redo_btn = QToolButton()
        redo_btn.setText("↺")
        redo_btn.setProperty("kind", "bar")
        redo_btn.setToolTip("Redo last task (Ctrl+R)")
        redo_btn.setCursor(Qt.PointingHandCursor)
        redo_btn.clicked.connect(self.redo_task)
        redo_btn.setEnabled(False)
        self.redo_btn = redo_btn
        lay.addWidget(redo_btn)
        self._ask_only.append(redo_btn)

        insp = QToolButton()
        insp.setDefaultAction(self.a_inspector)
        insp.setProperty("kind", "bar")
        lay.addWidget(insp)

        more = QToolButton()
        more.setText("⋯")
        more.setProperty("kind", "bar")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setToolTip(TIPS["more"])
        mm = QMenu(more)
        for a in (self.a_prefs, self.a_terms):
            mm.addAction(a)
        mm.addSeparator()
        for a in (self.a_data, self.a_export, self.a_logs):
            mm.addAction(a)
        more.setMenu(mm)
        lay.addWidget(more)

    # ------------------------------------------------------------------ theme
    def _theme_mode(self):
        return stores.settings().value("theme", "auto")

    def _system_dark(self):
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorScheme"):
            return hints.colorScheme() == Qt.ColorScheme.Dark
        return QGuiApplication.palette().color(QPalette.Window).lightness() < 128

    def apply_theme(self):
        mode = self._theme_mode()
        self.dark = self._system_dark() if mode == "auto" else mode == "dark"
        app = QApplication.instance()
        app.setPalette(build_palette(self.dark))
        app.setStyleSheet(theme.build_qss(self.dark))
        self.inspector.set_dark(self.dark)
        if self.answer:
            self.answer.hl.set_dark(self.dark)
        diagnostics.event("theme", mode="dark" if self.dark else "light")

    def _on_system_scheme(self, *_):
        if self._theme_mode() == "auto":
            self.apply_theme()

    # ------------------------------------------------------------------ modes and OCR
    def set_mode(self, index: int):
        self.mode_stack.setCurrentIndex(index)
        for i, b in enumerate(self.mode_btns):
            b.setChecked(i == index)
        for w in self._ask_only:               # nothing is sent in Folders mode, so hide the AI controls
            w.setVisible(index == 0)
        if index == 1:
            self.folders.reload_settings()
        else:
            self.composer.focus()

    def _ocr_config(self):
        s = stores.settings()
        if not s.value("ocr/enabled", True, type=bool):
            return None, ("en",)
        region = s.value("region", redaction.AUTO_REGION)
        langs = ("en", "pt", "es", "de") if region == redaction.AUTO_REGION else (
            redaction.REGION_LANG.get(region, "en"), "en")
        return ocr.get_engine(), langs

    # ------------------------------------------------------------------ small helpers
    def note(self, text, sticky=False, active=False):
        self.caption.setText(text)
        self.caption.setProperty("status", "active" if active else None)
        repolish(self.caption)
        self._update_caption_visibility()
        if not sticky:
            QTimer.singleShot(7000, lambda t=text: self.caption.text() == t and self._idle_caption())

    def _update_caption_visibility(self):
        self.caption.setVisible(bool(self.caption.text()))

    def _idle_caption(self):
        self.caption.setText("Nothing leaves your Mac until you review it.")

    def _start(self, worker, on_done, on_failed):
        self._workers.add(worker)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        worker.start()

    def _clear_thread(self):
        while self.thread.count() > 1:
            it = self.thread.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.review = self.answer = self._working = None

    def _add(self, widget):
        row = centered(widget)
        self.thread.insertWidget(self.thread.count() - 1, row)
        self.stack.setCurrentIndex(1)
        QTimer.singleShot(40, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return row

    def _set_working(self, text):
        if self._working is not None:
            self._working.deleteLater()
            self._working = None
        if text:
            self.note(f"⏳ {text}", sticky=True, active=True)
            self._working = self._add(label(text, "muted"))

    # ------------------------------------------------------------------ engine / migration
    def _on_engine_ready(self, engine):
        self.engine = engine
        self.composer.set_busy(False)
        self.composer.focus()
        self._idle_caption()
        self.caption.setProperty("status", None)
        repolish(self.caption)
        self.caption.setToolTip("Detection models loaded: " + ", ".join(
            f"{lang} ({name})" for lang, name in sorted(engine.models.items())))

    def _on_engine_failed(self, cls, msg):
        self.note("Detection engine failed to load. Sending is disabled.", sticky=True)
        QMessageBox.critical(self, "Detection engine unavailable",
                             f"Aegis cannot redact without its detection engine, so sending is disabled.\n\n{msg}")

    # ------------------------------------------------------------------ provider
    def _set_provider(self, key):
        self._provider = key
        stores.settings().setValue("provider", key)
        self.refresh_provider_status()

    def _model(self, provider):
        return stores.settings().value(f"model/{provider}", providers.DEFAULT_MODELS[provider])

    def _custom_url(self):
        return stores.settings().value("custom_url", "") or stores.get_secret(stores.LEGACY_CUSTOM_ENDPOINT)

    def refresh_provider_status(self):
        p = self._provider
        self.provider_btn.setText(providers.PROVIDERS[p] + "  ▾")
        err = providers.validate_config(p, stores.get_api_key(p), self._custom_url())
        if err:
            text, kind, tip = "Setup needed", "warn", err
        else:
            dest = providers.describe_destination(p, self._model(p), self._custom_url())
            text, kind = ("On this Mac", "local") if dest.is_local else ("Ready", "ok")
            tip = f"{dest.label} · {dest.host} · {dest.model}"
        self.provider_pill.setText(text)
        self.provider_pill.setProperty("pill", kind)
        self.provider_pill.setToolTip(tip)
        repolish(self.provider_pill)

    # ------------------------------------------------------------------ dialogs
    def open_settings(self):
        if SettingsDialog(self).exec():
            self.apply_theme()
            self.refresh_provider_status()
            self._restart_idle()
            self.folders.reload_settings()
            if not stores.legacy_presets_available():
                self._idle_caption()

    def open_presets(self):
        PresetsDialog(self).exec()

    def export_diagnostics(self):
        ans = QMessageBox.question(
            self, "Export diagnostics",
            "The report contains event types, timings, counts, error classes and code locations. "
            "It never contains document text, names, emails or placeholders.\n\nYou can open the log folder to "
            "check it before sharing. Export now?")
        if ans != QMessageBox.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostics", "aegis-diagnostics.zip", "Zip (*.zip)")
        if path:
            diagnostics.export_diagnostics(path)
            self.note("Diagnostics exported.")

    # ------------------------------------------------------------------ attachments
    def use_suggestion(self, prompt, source):
        if not self.composer.text():
            self.composer.set_text(prompt)
        self.composer.focus()
        if source == "document":
            self.choose_documents()
        elif source == "inbox":
            self.attach_inbox()

    def on_attach_requested(self, kind):
        self.choose_documents() if kind == "document" else self.attach_inbox()

    def choose_documents(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach documents", "", documents.FILE_FILTER)
        self.attach_documents(paths)

    def attach_documents(self, paths):
        for path in paths:
            if os.path.isdir(path):             # a folder belongs in the Folders view
                self.set_mode(1)
                self.folders.set_folder(path)
                return
            ext = os.path.splitext(path)[1].lower()
            if ext not in documents.SUPPORTED_EXT:
                self.note(f"Skipped {os.path.basename(path)}: PDF, Word, text and image files are supported.")
                continue
            name = os.path.basename(path)
            engine, langs = self._ocr_config()
            self.note(f"Reading {name}…", sticky=True)

            def done(doc, name=name):
                label_text = name + (" · OCR" if doc.ocr_pages else "")
                self._add_attachment(Attachment("document", label_text, doc.text, doc.ocr_pages))
                self._idle_caption()

            def failed(cls, msg, name=name):
                self.note(f"Could not read {name}.")
                QMessageBox.warning(self, "Could not read file", msg)

            self._start(FnWorker("load_document", documents.load_document, path, engine, langs,
                                 log_fields={"doc_type": ext.lstrip("."),
                                             "engine": getattr(engine, "name", "none")}), done, failed)

    def attach_inbox(self):
        accounts = stores.load_accounts()
        if not accounts:
            QMessageBox.information(self, "No mail account", "Add a mail account in Settings > Email first.")
            self.open_settings()
            return
        dlg = InboxDialog(self, accounts)
        if not dlg.exec():
            return
        chosen = [a for a in accounts if a.id in dlg.selected_ids()]
        if not chosen:
            QMessageBox.information(self, "No account selected", "Tick at least one account.")
            return
        start, end, exact = dlg.date_range()
        if start.date() > end.date():
            QMessageBox.warning(self, "Invalid range", "The start date is after the end date.")
            return
        creds = []
        for a in chosen:
            user, pw = stores.account_credentials(a.id)
            creds.append({"host": a.host, "port": a.port, "user": user, "password": pw})
        self.note("Fetching emails (read-only)…", sticky=True)

        def done(digest):
            n = digest.count("--- EMAIL ---")
            tag = f"{len(chosen)} accounts" if len(chosen) > 1 else chosen[0].label
            self._add_attachment(Attachment("inbox", f"Inbox · {tag} · {n} email{'s' if n != 1 else ''}", digest))
            self._idle_caption()

        def failed(cls, msg):
            self.note("Inbox fetch failed.")
            QMessageBox.warning(self, "Email error", msg)

        self._start(FnWorker("imap_fetch", mailbox.fetch_many, creds, start, end,
                             stores.settings().value("imap/limit", 30, type=int), exact), done, failed)

    def _add_attachment(self, att):
        self.composer.set_attachments(self.composer.attachments() + [att])
        self.composer.focus()

    def _remove_attachment(self, index):
        atts = self.composer.attachments()
        if 0 <= index < len(atts):
            del atts[index]
            self.composer.set_attachments(atts)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            if not any(os.path.isdir(p) for p in paths):
                self.set_mode(0)
            self.attach_documents(paths)
            e.acceptProposedAction()

    # ------------------------------------------------------------------ the task
    def submit(self):
        if self.engine is None:
            return
        message, atts = self.composer.text(), self.composer.attachments()
        if not message and not atts:
            return
        provider = self._provider
        key, url = stores.get_api_key(provider), self._custom_url()
        err = providers.validate_config(provider, key, url)
        if err:
            QMessageBox.warning(self, "Provider not ready", err)
            self.open_settings()
            return

        self._clear_thread()
        self.session = Session(attachments=atts, message=message)
        self._last_session = Session(attachments=list(atts), message=message)  # Save for redo
        self.redo_btn.setEnabled(True)
        self.inspector.set_session(self.session)
        self.inspector.set_locked(False)
        self._add(self._user_bubble(message, atts))
        self.composer.clear()
        self.composer.set_busy(True)
        self._set_working("Looking for personal data on this Mac…")

        self._language_override = None
        self._run_analysis()

    def _run_analysis(self):
        s = self.session
        region = stores.settings().value("region", redaction.AUTO_REGION)
        doc = s.compose_doc()
        worker = FnWorker("redact", redaction.analyze, self.engine, doc, region,
                          tuple(stores.load_presets()), (), False, s.message, self._language_override,
                          disabled_categories(), log_fields={"char_count": len(doc) + len(s.message)})
        self._start(worker, self._on_analyzed, self._on_failed)

    def _reanalyze(self, language):
        """The user corrected the detected language: run detection again before anything is sent."""
        if self.session.sent:
            return
        self._language_override = language
        if self.review is not None:
            self.thread.removeWidget(self.review.parentWidget())
            self.review.parentWidget().deleteLater()
            self.review = None
        self._set_working(f"Checking again as {redaction.LANG_NAMES.get(language, language)}…")
        self.composer.set_busy(True)
        self._run_analysis()

    def _user_bubble(self, message, atts):
        bubble = QFrame()
        bubble.setProperty("bubble", "user")
        lay = QVBoxLayout(bubble)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        if atts:
            row = QHBoxLayout()
            for a in atts:
                row.addWidget(Chip(a.name, f"{a.chars:,} characters", removable=False))
            row.addStretch()
            lay.addLayout(row)
        if message:
            lbl = label(message, None, True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(lbl)
        return bubble

    def _on_analyzed(self, analysis):
        s = self.session
        s.analysis = analysis
        s.result = analysis.render()
        self._set_working(None)
        diagnostics.event("redaction_summary", language=analysis.language, entity_counts=s.result.counts,
                          entity_total=sum(s.result.counts.values()),
                          degraded=s.result.degraded, char_count=len(s.result.text))
        provider = self._provider
        dest = providers.describe_destination(provider, self._model(provider), self._custom_url())
        self.review = ReviewCard(s, dest, sorted(self.engine.languages), self._on_edited, self._inspect,
                                 self.cancel_review, self.send_now, self._reanalyze)
        if any(a.ocr_pages for a in s.attachments):
            self.review.add_note("Some text was read by OCR. Digits can be misread, so check the flagged items.")
        self._add(self.review)
        self.inspector.set_session(s)
        skip = (dest.is_local and stores.settings().value("skip_review_local", False, type=bool)
                and not s.analysis.needs_attention())
        if skip:
            self.send_now()

    def _on_edited(self):
        s = self.session
        if not s.analysis or s.sent:
            return
        s.result = s.analysis.render()
        if self.review:
            self.review.refresh()
            self.review.sync_checks()
        self.inspector.refresh()

    def _inspect(self):
        if not self.inspector.isVisible():
            self.inspector.show()
            self.resizeDocks([self.inspector], [470], Qt.Horizontal)
        self.inspector.show_tab("Detections")

    def cancel_review(self):
        s = self.session
        diagnostics.event("review", ok=False)
        message, atts = s.message, s.attachments
        self._clear_thread()
        self.session = Session()
        self.inspector.set_session(self.session)
        self.stack.setCurrentIndex(0)
        self.composer.set_text(message)
        self.composer.set_attachments(atts)
        self.composer.set_busy(False)
        self.composer.focus()
        self.note("Cancelled. Nothing was sent.")

    def send_now(self):
        s = self.session
        res = s.analysis.render()
        s.result, s.sent_result, s.sent = res, res, True       # frozen: re-hydration uses exactly this
        provider = self._provider
        dest = providers.describe_destination(provider, self._model(provider), self._custom_url())
        diagnostics.event("review", ok=True, entity_total=sum(res.counts.values()))
        self.inspector.set_locked(True)
        if self.review:
            self.review.lock(f"Sent to {dest.label} · {sum(res.counts.values())} items hidden.")
        self._set_working(f"Waiting for {dest.label}…")
        worker = FnWorker("query", providers.send, provider, stores.get_api_key(provider), dest.model,
                          providers.BASE_SYSTEM_PROMPT, res.text, self._custom_url(),
                          log_fields={"provider": provider, "model": dest.model})
        self._start(worker, self._on_response, self._on_failed)

    def _on_response(self, payload):
        raw, model_used = payload
        s = self.session
        s.raw_response, s.model = raw, model_used
        final, unresolved = redaction.rehydrate(raw, s.sent_result.mapping)
        s.final_text = final
        self._set_working(None)
        self.answer = AnswerCard(final, raw, model_used, self.dark,
                                 self.fill_placeholders, self.copy_result, self.save_result)
        self._add(self.answer)
        diagnostics.event("rehydrate", unresolved=len(unresolved),
                          orphan_tags=len(redaction.find_fill_candidates(final)), model=model_used)
        self.composer.set_busy(False)
        self.composer.focus()
        self._idle_caption()

    def _on_failed(self, cls, msg):
        self._set_working(None)
        s = self.session
        message, atts, sent = s.message, s.attachments, s.sent
        self.composer.set_busy(False)
        if not sent:                      # nothing left this Mac: put the work back in the composer
            self._clear_thread()
            self.stack.setCurrentIndex(0)
            self.composer.set_text(message)
            self.composer.set_attachments(atts)
        self.note("Stopped.")
        QMessageBox.warning(self, "Something went wrong", msg)

    # ------------------------------------------------------------------ answer actions
    def fill_placeholders(self):
        if not self.answer:
            return
        text = self.answer.final.toPlainText()
        for item in redaction.find_fill_candidates(text):
            val, ok = QInputDialog.getText(self, "Fill placeholder", f"Value for {item}:")
            if ok and val:
                text = text.replace(item, val)
        self.answer.final.setPlainText(text)
        self.session.final_text = text
        self.answer.update_fill_state()

    def copy_result(self):
        if self.answer:
            QApplication.clipboard().setText(self.answer.final.toPlainText())
            self.note("Answer copied.")

    def save_result(self):
        if not self.answer:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save answer", "aegis-answer.md", "Markdown / Text (*.md *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.answer.final.toPlainText())
            self.note(f"Saved {os.path.basename(path)}")

    def new_task(self):
        if any(w.isRunning() and w._name in ("redact", "query") for w in self._workers):
            self.note("Wait for the current step to finish first.")
            return
        self._clear_thread()
        self.session = Session()          # drops the analysis and the hydration mapping
        self._last_session = None
        self.redo_btn.setEnabled(False)
        self.inspector.set_session(self.session)
        self.inspector.set_locked(False)
        self.stack.setCurrentIndex(0)
        self.composer.set_busy(self.engine is None)
        self.composer.focus()
        self._idle_caption()

    def redo_task(self):
        """Restore the last task's message and attachments for quick retry."""
        if not self._last_session:
            return
        if any(w.isRunning() and w._name in ("redact", "query") for w in self._workers):
            self.note("Wait for the current step to finish first.")
            return
        self._clear_thread()
        self.session = Session()
        self.composer.set_text(self._last_session.message)
        self.composer.set_attachments(self._last_session.attachments)
        self.inspector.set_session(self.session)
        self.inspector.set_locked(False)
        self.stack.setCurrentIndex(0)
        self.composer.set_busy(self.engine is None)
        self.composer.focus()
        self.note("Last task restored. Press ↑ to submit again.")

    # ------------------------------------------------------------------ inactivity wipe
    def _restart_idle(self):
        minutes = stores.settings().value("idle_minutes", 0, type=int)
        if minutes > 0:
            self._idle.start(minutes * 60 * 1000)
        else:
            self._idle.stop()

    def _on_idle(self):
        busy = any(w.isRunning() and w._name in ("redact", "query") for w in self._workers)
        if busy or not (self.session.analysis or self.answer):
            self._restart_idle()
            return
        self.new_task()
        self.note("Workspace cleared after inactivity.")

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.KeyPress, QEvent.MouseButtonPress) and self._idle.isActive():
            self._restart_idle()
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_R and event.modifiers() & Qt.ControlModifier:
                self.redo_task()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self.folders.shutdown()
        for w in list(self._workers):
            w.wait(1500)
        super().closeEvent(event)


def main():
    diagnostics.init(APP_VERSION)
    QCoreApplication.setOrganizationName("Aegis")
    QCoreApplication.setApplicationName("AegisPrivacyAirlock")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # predictable QSS rendering; the tokens supply the macOS look
    window = MainWindow()
    window.show()
    code = app.exec()
    window.deleteLater()               # destroy widgets before the QApplication to avoid a crash at quit
    app.processEvents()
    del window
    sys.exit(code)


if __name__ == "__main__":
    main()
