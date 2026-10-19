"""Dialogs and small shared widgets."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from aegis import diagnostics, mailbox, ocr, providers, stores, theme
from aegis.redaction import AUTO_REGION, CATEGORIES, REGION_LANG
from aegis.workers import FnWorker

IMAP_PRESETS = {
    "Gmail / Google Workspace": ("imap.gmail.com", 993,
        "Use an app password: Google Account > Security > 2-Step Verification > App passwords. "
        "Google Workspace admins can switch IMAP or app passwords off."),
    "iCloud Mail": ("imap.mail.me.com", 993,
        "Use an app-specific password from appleid.apple.com > Sign-In and Security."),
    "Outlook / Microsoft 365": ("outlook.office365.com", 993,
        "Microsoft usually requires OAuth for IMAP, so an app password often fails. OAuth sign-in is not supported yet."),
    "Yahoo Mail": ("imap.mail.yahoo.com", 993, "Use an app password from Yahoo Account Security."),
    "Fastmail": ("imap.fastmail.com", 993, "Use an app password from Fastmail Settings > Privacy & Security."),
    "ProtonMail (Local Bridge)": ("127.0.0.1", 1143, "Needs Proton Mail Bridge running on this Mac."),
    "Custom IMAP Server": ("", 993, "Ask your provider for the IMAP host and port."),
}

ENTITY_LABELS = {
    "PERSON": "Names", "EMAIL_ADDRESS": "Email addresses", "PHONE_NUMBER": "Phone numbers",
    "LOCATION": "Locations", "ORGANIZATION": "Organisations", "IBAN_CODE": "IBANs",
    "CREDIT_CARD": "Card numbers", "PT_NIF": "PT tax numbers (NIF)", "ES_DNI_NIE": "ES DNI / NIE",
    "PT_CITIZEN_CARD": "PT citizen cards", "US_SSN": "US SSNs", "IP_ADDRESS": "IP addresses",
    "URL": "URLs", "PRESET": "Your presets", "MANUAL": "Manual redactions", "NRP": "Nationality / religion / political",
    "DATE_TIME": "Dates", "MEDICAL_LICENSE": "Medical licences",
}


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)


def button(text, kind=None, slot=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(slot)
    return b


def label(text, role=None, word_wrap=False) -> QLabel:
    l = QLabel(text)
    if role:
        l.setProperty("role", role)
    l.setWordWrap(word_wrap)
    return l


def pill(text, kind) -> QLabel:
    l = QLabel(text)
    l.setProperty("pill", kind)
    l.setAlignment(Qt.AlignCenter)
    return l


def card() -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    return f


# ----------------------------------------------------------------------------- highlighting
class TagHighlighter(QSyntaxHighlighter):
    """Shades placeholders like <PERSON_1> so the user can see exactly what was replaced."""
    _RE = re.compile(r"<[A-Z][A-Z_]*_\d+>")

    def __init__(self, document, dark=False):
        super().__init__(document)
        self.set_dark(dark, rehighlight=False)

    def set_dark(self, dark, rehighlight=True):
        t = theme.tokens(dark)
        self._fmt = QTextCharFormat()
        self._fmt.setBackground(QColor(t["tag_bg"]))
        self._fmt.setForeground(QColor(t["tag_fg"]))
        self._fmt.setFontWeight(QFont.DemiBold)
        if rehighlight:
            self.rehighlight()

    def highlightBlock(self, text):
        for m in self._RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt)


# ----------------------------------------------------------------------------- inbox
TIMEFRAMES = ["Last 24 Hours", "Today", "Last 7 Days", "Last 30 Days", "Custom Range"]


class InboxDialog(QDialog):
    """Pick which saved accounts and which period to attach."""

    def __init__(self, parent, accounts):
        super().__init__(parent)
        self.setWindowTitle("Attach inbox digest")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Attach inbox digest", "h2"))
        lay.addWidget(label("Reads your mailbox read-only: nothing is marked as read, moved or deleted. "
                            "The digest is redacted before anything is sent.", "muted", True))
        self.account_list = QListWidget()
        self.account_list.setMaximumHeight(110)
        for a in accounts:
            it = QListWidgetItem(a.label)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if a.enabled else Qt.Unchecked)
            it.setData(Qt.UserRole, a.id)
            self.account_list.addItem(it)
        self.account_list.setToolTip("Tick the accounts to include. Manage accounts in Settings > Email.")
        lay.addWidget(label("Accounts", "muted"))
        lay.addWidget(self.account_list)
        row = QHBoxLayout()
        row.addWidget(label("Period", "muted"))
        self.timeframe = QComboBox()
        self.timeframe.addItems(TIMEFRAMES)
        self.timeframe.currentIndexChanged.connect(self._toggle)
        row.addWidget(self.timeframe, 1)
        lay.addLayout(row)
        self.custom = QWidget()
        cl = QHBoxLayout(self.custom)
        cl.setContentsMargins(0, 0, 0, 0)
        self.from_edit, self.to_edit = QDateEdit(), QDateEdit()
        for e, d in ((self.from_edit, -1), (self.to_edit, 0)):
            e.setCalendarPopup(True)
            e.setDisplayFormat("dd MMM yyyy")
            e.setDate(QDate.currentDate().addDays(d))
        cl.addWidget(label("From", "muted"))
        cl.addWidget(self.from_edit)
        cl.addWidget(label("To", "muted"))
        cl.addWidget(self.to_edit)
        lay.addWidget(self.custom)
        self._toggle()
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(button("Cancel", slot=self.reject))
        btns.addWidget(button("Attach", "primary", self.accept))
        lay.addLayout(btns)

    def _toggle(self):
        self.custom.setVisible(self.timeframe.currentText() == "Custom Range")
        self.adjustSize()

    def selected_ids(self):
        return [self.account_list.item(i).data(Qt.UserRole) for i in range(self.account_list.count())
                if self.account_list.item(i).checkState() == Qt.Checked]

    def date_range(self):
        sel = self.timeframe.currentText()
        now = datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if sel == "Last 24 Hours":
            return now - timedelta(hours=24), now, True
        if sel == "Today":
            return midnight, now, False
        if sel == "Last 7 Days":
            return midnight - timedelta(days=6), now, False
        if sel == "Last 30 Days":
            return midnight - timedelta(days=29), now, False
        a, b = self.from_edit.date(), self.to_edit.date()
        return datetime(a.year(), a.month(), a.day()), datetime(b.year(), b.month(), b.day()), False


class AccountDialog(QDialog):
    """Add or edit one mail account. Credentials go to the Keychain, never to a file."""

    def __init__(self, parent=None, account=None):
        super().__init__(parent)
        self.setWindowTitle("Mail account")
        self.setMinimumWidth(460)
        self._acct = account
        self._workers = set()
        user, pw = stores.account_credentials(account.id) if account else ("", "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.name = QLineEdit(account.label if account else "")
        self.name.setPlaceholderText("e.g. Work Gmail (only shown in this app)")
        self.provider = QComboBox()
        self.provider.addItems(list(IMAP_PRESETS))
        if account and account.provider in IMAP_PRESETS:
            self.provider.setCurrentText(account.provider)
        self.host = QLineEdit(account.host if account else "")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(account.port if account else 993)
        self.user = QLineEdit(user)
        self.user.setPlaceholderText("you@domain.com")
        self.pw = QLineEdit(pw)
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("App password")
        self.pw.setToolTip("An app password, not your normal login password. Stored in the macOS Keychain.")
        self.enabled = QCheckBox("Include this account in inbox digests")
        self.enabled.setChecked(account.enabled if account else True)
        form.addRow("Name:", self.name)
        form.addRow("Provider:", self.provider)
        form.addRow("IMAP host:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("Email address:", self.user)
        form.addRow("App password:", self.pw)
        form.addRow("", self.enabled)
        lay.addLayout(form)
        self.hint = label("", "caption", True)
        lay.addWidget(self.hint)
        self.provider.currentIndexChanged.connect(lambda *_: self._provider_changed(False))
        self._provider_changed(initial=bool(account))

        self.result_label = label("", "muted", True)
        lay.addWidget(self.result_label)
        row = QHBoxLayout()
        self.test_btn = button("Test connection", slot=self._test)
        self.test_btn.setToolTip("Sign in once to check these details. Reads nothing.")
        row.addWidget(self.test_btn)
        row.addStretch()
        row.addWidget(button("Cancel", slot=self.reject))
        row.addWidget(button("Save", "primary", self._save))
        lay.addLayout(row)

    def _provider_changed(self, initial):
        host, port, hint = IMAP_PRESETS[self.provider.currentText()]
        custom = self.provider.currentText() == "Custom IMAP Server"
        if not initial or not self.host.text():
            self.host.setText(host)
            self.port.setValue(port)
        self.host.setEnabled(custom)
        self.hint.setText(hint)

    def _test(self):
        host, port, user, pw = self.host.text().strip(), self.port.value(), self.user.text().strip(), self.pw.text()
        if not (host and user and pw):
            self.result_label.setText("Fill in host, email address and app password first.")
            return
        self.test_btn.setEnabled(False)
        self.result_label.setText("Connecting…")
        w = FnWorker("imap_test", mailbox.test_login, host, port, user, pw)
        self._workers.add(w)
        w.done.connect(lambda _r: self._tested("Connected. These details work."))
        w.failed.connect(lambda cls, msg: self._tested(f"Could not sign in: {msg}"))
        w.finished.connect(lambda w=w: self._workers.discard(w))
        w.start()

    def _tested(self, text):
        self.result_label.setText(text)
        self.test_btn.setEnabled(True)

    def _save(self):
        if not (self.name.text().strip() and self.host.text().strip() and self.user.text().strip() and self.pw.text()):
            self.result_label.setText("Name, host, email address and app password are all required.")
            return
        acct = self._acct or stores.Account(stores.new_account_id(), "", "", "", 993)
        acct.label, acct.provider = self.name.text().strip(), self.provider.currentText()
        acct.host, acct.port, acct.enabled = self.host.text().strip(), self.port.value(), self.enabled.isChecked()
        try:
            stores.set_account_credentials(acct.id, self.user.text().strip(), self.pw.text())
        except RuntimeError as e:
            self.result_label.setText(str(e))
            return
        accounts = [a for a in stores.load_accounts() if a.id != acct.id] + [acct]
        stores.save_accounts(accounts)
        self.accept()

    def done(self, r):
        for w in list(self._workers):
            w.wait(1000)
        super().done(r)


# ----------------------------------------------------------------------------- presets
class PresetsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Always-Redacted Terms")
        self.resize(480, 400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(label("Always-redacted terms", "h2"))
        lay.addWidget(label("Tax numbers, your address, passport ID, client names or project codenames. "
                            "Matched case-insensitively in every document. Stored in your macOS Keychain, not in a file.",
                            "muted", True))
        self.list = QListWidget()
        self.list.addItems(stores.load_presets())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(button("Add…", slot=self._add))
        row.addWidget(button("Remove", "danger", self._remove))
        row.addStretch()
        row.addWidget(button("Cancel", slot=self.reject))
        row.addWidget(button("Save", "primary", self._save))
        lay.addLayout(row)

    def _add(self):
        term, ok = QInputDialog.getText(self, "Add term", "Term to always redact:")
        if ok and term.strip():
            self.list.addItem(term.strip())

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _save(self):
        try:
            stores.save_presets([self.list.item(i).text() for i in range(self.list.count())])
        except RuntimeError as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return
        self.accept()


# ----------------------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 580)
        s = stores.settings()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        tabs = QTabWidget()
        tabs.tabBar().setExpanding(False)
        tabs.setStyleSheet("QTabBar::tab { min-width: 0px; padding: 5px 14px; }")
        lay.addWidget(tabs, 1)

        # ---- General
        g = QWidget()
        gf = QFormLayout(g)
        gf.setVerticalSpacing(12)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Follow System", "auto")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(s.value("theme", "auto"))))
        self.region_combo = QComboBox()
        self.region_combo.addItems([AUTO_REGION] + list(REGION_LANG))
        saved = s.value("region", AUTO_REGION)
        self.region_combo.setCurrentText(saved if saved in [AUTO_REGION] + list(REGION_LANG) else AUTO_REGION)
        self.region_combo.setToolTip("Decides which language model finds names and places.\n"
                                     "Automatic reads each document and picks the language itself.")
        gf.addRow("Appearance:", self.theme_combo)
        gf.addRow("Document language:", self.region_combo)
        gf.addRow("", label("Automatic works for Portuguese, Spanish, German and English. "
                            "Pick a fixed language only if your documents are always in it.", "caption", True))
        tabs.addTab(g, "General")

        # ---- Privacy
        p = QWidget()
        pl = QVBoxLayout(p)
        pl.setSpacing(6)
        pl.addWidget(label("Hide these kinds of data before anything is sent", "title"))
        self.cat_checks = {}
        for key, (name, _types, default) in CATEGORIES.items():
            cb = QCheckBox(name)
            cb.setChecked(s.value(f"categories/{key}", default, type=bool))
            cb.setToolTip("Untick to stop hiding this kind of data. You still see it in the Inspector.")
            self.cat_checks[key] = cb
            pl.addWidget(cb)
        pl.addSpacing(8)
        row = QHBoxLayout()
        self.terms_label = label("", "muted")
        row.addWidget(self.terms_label)
        row.addStretch()
        row.addWidget(button("Manage always-redacted terms…", slot=self._manage_terms))
        pl.addLayout(row)
        self.legacy_box = QFrame()
        self.legacy_box.setProperty("card", True)
        ll = QVBoxLayout(self.legacy_box)
        ll.setContentsMargins(12, 10, 12, 10)
        ll.addWidget(label("An old plain-text terms file was found", "title"))
        ll.addWidget(label(f"{stores.LEGACY_PRESETS_FILE} holds your always-redacted terms unprotected. "
                           "Moving them into the macOS Keychain encrypts them and deletes the file.", "muted", True))
        ll.addWidget(button("Move to Keychain and delete file", "primary", self._migrate))
        pl.addWidget(self.legacy_box)
        self.skip_local = QCheckBox("Skip the review step for models running on this Mac")
        self.skip_local.setChecked(s.value("skip_review_local", False, type=bool))
        self.skip_local.setToolTip("Only applies when the destination is on this Mac and nothing needs attention.")
        pl.addWidget(self.skip_local)
        idle = QHBoxLayout()
        idle.addWidget(label("Clear the workspace after", "muted"))
        self.idle = QSpinBox()
        self.idle.setRange(0, 240)
        self.idle.setSuffix(" min")
        self.idle.setSpecialValueText("never")
        self.idle.setValue(s.value("idle_minutes", 0, type=int))
        self.idle.setToolTip("Wipes the answer and the placeholder mapping from memory after this long without activity.")
        idle.addWidget(self.idle)
        idle.addStretch()
        pl.addLayout(idle)
        pl.addStretch()
        self._refresh_terms()
        tabs.addTab(p, "Privacy")

        # ---- AI providers
        a = QWidget()
        af = QFormLayout(a)
        af.setVerticalSpacing(10)
        self.key_edits, self.model_edits = {}, {}
        for key, name in providers.PROVIDERS.items():
            kedit = QLineEdit()
            kedit.setEchoMode(QLineEdit.Password)
            kedit.setText(stores.get_api_key(key))
            kedit.setPlaceholderText("optional" if key == providers.CUSTOM else "API key")
            kedit.setToolTip("Stored in the macOS Keychain.")
            medit = QLineEdit(s.value(f"model/{key}", providers.DEFAULT_MODELS[key]))
            medit.setToolTip("The model name exactly as the provider spells it.")
            self.key_edits[key], self.model_edits[key] = kedit, medit
            af.addRow(f"{name} key:", kedit)
            af.addRow("      model:", medit)
        self.custom_url = QLineEdit(s.value("custom_url", "") or stores.get_secret(stores.LEGACY_CUSTOM_ENDPOINT))
        self.custom_url.setPlaceholderText("http://localhost:11434/v1")
        self.custom_url.setToolTip("An OpenAI-compatible endpoint. http is only allowed for this Mac.")
        af.addRow("Custom base URL:", self.custom_url)
        tabs.addTab(a, "AI Providers")

        # ---- Email
        e = QWidget()
        el = QVBoxLayout(e)
        el.addWidget(label("Mail accounts for inbox digests. Tick the ones to include by default.", "muted", True))
        self.acct_list = QListWidget()
        self.acct_list.itemDoubleClicked.connect(lambda *_: self._edit_account())
        el.addWidget(self.acct_list, 1)
        row = QHBoxLayout()
        row.addWidget(button("Add account…", slot=self._add_account))
        row.addWidget(button("Edit…", slot=self._edit_account))
        row.addWidget(button("Remove", "danger", self._remove_account))
        row.addStretch()
        el.addLayout(row)
        el.addWidget(label("Mail is opened read-only: nothing is marked as read, moved or deleted. "
                           "Passwords are stored in the macOS Keychain.", "caption", True))
        self._fill_accounts()
        tabs.addTab(e, "Email")

        # ---- Documents & folders
        f = QWidget()
        ff = QFormLayout(f)
        ff.setVerticalSpacing(10)
        self.ocr_check = QCheckBox("Read scanned pages and images (OCR)")
        self.ocr_check.setChecked(s.value("ocr/enabled", True, type=bool))
        self.ocr_check.setToolTip("Turns photos and scanned PDFs into text on this Mac. Nothing is uploaded.")
        ff.addRow("", self.ocr_check)
        ff.addRow("", label(ocr.describe(), "caption", True))
        out_row = QHBoxLayout()
        self.out_dir = QLineEdit(s.value("folders/out_dir", ""))
        self.out_dir.setPlaceholderText(stores.default_out_dir())
        self.out_dir.setToolTip("Where sanitised copies of a folder are saved. Share this location, not your originals.")
        out_row.addWidget(self.out_dir, 1)
        out_row.addWidget(button("Choose…", slot=self._choose_out_dir))
        ff.addRow("Save sanitised copies in:", out_row)
        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(s.value("folders/recursive", True, type=bool))
        self.auto_check = QCheckBox("Sanitise new and changed files automatically")
        self.auto_check.setChecked(s.value("folders/auto", True, type=bool))
        self.auto_check.setToolTip("Runs as soon as you drop a folder or a file in it changes. Nothing is sent anywhere.")
        self.watch_check = QCheckBox("Keep checking the folder for changes while the Folders view is open")
        self.watch_check.setChecked(s.value("folders/watch", True, type=bool))
        for cb in (self.recursive_check, self.auto_check, self.watch_check):
            ff.addRow("", cb)
        ff.addRow("", label("Items the detector is unsure about are hidden in folder copies, to be safe.", "caption", True))
        tabs.addTab(f, "Documents & Folders")

        # ---- Advanced
        d = QWidget()
        dl = QVBoxLayout(d)
        dl.addWidget(label("Diagnostics", "title"))
        dl.addWidget(label("Logs hold event types, timings, counts and error classes. Never document text.", "muted", True))
        row = QHBoxLayout()
        row.addWidget(button("Open log folder", slot=lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(diagnostics.LOG_DIR))))
        row.addWidget(button("Clear logs", "danger", self._clear_logs))
        row.addStretch()
        dl.addLayout(row)
        self.adv_note = label("", "caption")
        dl.addWidget(self.adv_note)
        dl.addStretch()
        tabs.addTab(d, "Advanced")

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", slot=self.reject))
        row.addWidget(button("Save", "primary", self._save))
        lay.addLayout(row)

    # -- privacy tab
    def _refresh_terms(self):
        n = len(stores.load_presets())
        self.terms_label.setText(f"{n} always-redacted term{'s' if n != 1 else ''} stored in the Keychain")
        self.legacy_box.setVisible(stores.legacy_presets_available())

    def _manage_terms(self):
        PresetsDialog(self).exec()
        self._refresh_terms()

    def _migrate(self):
        try:
            n = stores.import_legacy_presets()
            stores.delete_legacy_presets()
        except Exception as e:
            QMessageBox.warning(self, "Migration failed", f"Your file was left untouched. ({type(e).__name__})")
            return
        QMessageBox.information(self, "Done", f"Moved {n} terms into the Keychain and removed the file.")
        self._refresh_terms()

    # -- email tab
    def _fill_accounts(self):
        self.acct_list.clear()
        for a in stores.load_accounts():
            it = QListWidgetItem(f"{a.label}   ·   {a.provider}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if a.enabled else Qt.Unchecked)
            it.setData(Qt.UserRole, a.id)
            self.acct_list.addItem(it)

    def _sync_enabled(self):
        accts = stores.load_accounts()
        state = {self.acct_list.item(i).data(Qt.UserRole): self.acct_list.item(i).checkState() == Qt.Checked
                 for i in range(self.acct_list.count())}
        for a in accts:
            a.enabled = state.get(a.id, a.enabled)
        stores.save_accounts(accts)

    def _add_account(self):
        self._sync_enabled()
        if AccountDialog(self).exec():
            self._fill_accounts()

    def _edit_account(self):
        it = self.acct_list.currentItem()
        if not it:
            return
        self._sync_enabled()
        acct = next((a for a in stores.load_accounts() if a.id == it.data(Qt.UserRole)), None)
        if acct and AccountDialog(self, acct).exec():
            self._fill_accounts()

    def _remove_account(self):
        it = self.acct_list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "Remove account", f"Remove “{it.text().split('   ·')[0]}” and its saved password?") \
                == QMessageBox.Yes:
            self._sync_enabled()
            stores.delete_account(it.data(Qt.UserRole))
            self._fill_accounts()

    def _choose_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Save sanitised copies in", self.out_dir.text() or stores.default_out_dir())
        if path:
            self.out_dir.setText(path)

    # -- advanced tab
    def _clear_logs(self):
        try:
            for name in os.listdir(diagnostics.LOG_DIR):
                path = os.path.join(diagnostics.LOG_DIR, name)
                if os.path.isfile(path):
                    open(path, "w").close() if path == diagnostics.LOG_FILE else os.remove(path)
            self.adv_note.setText("Logs cleared.")
        except OSError:
            self.adv_note.setText("Could not clear the logs.")

    def _save(self):
        s = stores.settings()
        s.setValue("theme", self.theme_combo.currentData())
        s.setValue("region", self.region_combo.currentText())
        for key, cb in self.cat_checks.items():
            s.setValue(f"categories/{key}", cb.isChecked())
        s.setValue("skip_review_local", self.skip_local.isChecked())
        s.setValue("idle_minutes", self.idle.value())
        s.setValue("ocr/enabled", self.ocr_check.isChecked())
        s.setValue("folders/out_dir", self.out_dir.text().strip())
        s.setValue("folders/recursive", self.recursive_check.isChecked())
        s.setValue("folders/auto", self.auto_check.isChecked())
        s.setValue("folders/watch", self.watch_check.isChecked())
        for key in providers.PROVIDERS:
            s.setValue(f"model/{key}", self.model_edits[key].text().strip() or providers.DEFAULT_MODELS[key])
        s.setValue("custom_url", self.custom_url.text().strip())
        self._sync_enabled()
        try:
            for key in providers.PROVIDERS:
                stores.set_secret(stores.SECRET_KEY_NAMES[key], self.key_edits[key].text())
        except RuntimeError as err:
            QMessageBox.critical(self, "Could not save", str(err))
            return
        self.accept()


def disabled_categories() -> frozenset:
    """Categories switched off in Settings (defaults apply until the user changes something)."""
    s = stores.settings()
    return frozenset(k for k, (_n, _t, default) in CATEGORIES.items()
                     if not s.value(f"categories/{k}", default, type=bool))


# ----------------------------------------------------------------------------- data handling
DATA_HANDLING_HTML = """
<h3>What Aegis does with your data</h3>
<p><b>Stays on this Mac:</b> your original documents and emails, the mapping between placeholders and real values
(held in memory only and cleared with <i>Clear Workspace</i>), your always-redacted terms and your API keys (macOS Keychain),
and diagnostic logs (counts, timings and error types only, never document text).</p>
<p><b>Leaves this Mac:</b> only the sanitised text you approve in the review step, sent to the provider you selected.
Choosing a local endpoint on this Mac sends nothing outside it.</p>
<h3>What this does not guarantee</h3>
<ul>
<li>Automatic detection misses things. Always read the review step.</li>
<li>Replacing names with placeholders is <i>pseudonymisation</i>: context in the remaining text can still identify a person,
and under GDPR pseudonymised data is still personal data.</li>
<li>Whether your use of a cloud AI provider is lawful depends on your organisation's legal basis, the provider's terms
(including a data processing agreement) and transfer safeguards. Aegis reduces exposure; it does not replace that assessment.</li>
</ul>
"""


class DataHandlingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data Handling")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        view = QTextBrowser()
        view.setHtml(DATA_HANDLING_HTML)
        view.setFrameShape(QFrame.NoFrame)
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Close", "primary", self.accept))
        lay.addLayout(row)
