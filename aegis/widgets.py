"""Reusable widgets: flow layout, attachment chips, and the chat-style composer."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from aegis.tips import TIPS


class FlowLayout(QLayout):
    """Wraps children onto new lines (Qt's classic flow-layout example)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items, self._space = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None
    def expandingDirections(self): return Qt.Orientation(0)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self._layout(QRect(0, 0, w, 0), True)
    def setGeometry(self, rect): super().setGeometry(rect); self._layout(rect, False)
    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, test_only):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > r.right() and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._space, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._space
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


class Chip(QFrame):
    removed = Signal()

    def __init__(self, text: str, tooltip: str = "", removable: bool = True):
        super().__init__()
        self.setProperty("chip", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 4 if removable else 10, 3)
        lay.setSpacing(4)
        lbl = QLabel(text)
        lbl.setProperty("role", "chip")
        lay.addWidget(lbl)
        if tooltip:
            self.setToolTip(tooltip)
        if removable:
            x = QToolButton()
            x.setText("×")
            x.setProperty("kind", "chipx")
            x.setToolTip("Remove")
            x.setCursor(Qt.PointingHandCursor)
            x.clicked.connect(self.removed)
            lay.addWidget(x)


class ComposerEdit(QTextEdit):
    submit = Signal()
    focus_changed = Signal(bool)
    files_dropped = Signal(list)

    MIN_H, MAX_H = 44, 190

    def __init__(self):
        super().__init__()
        self.setObjectName("ComposerEdit")
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_):
        h = int(self.document().size().height()) + 14
        self.setFixedHeight(max(self.MIN_H, min(self.MAX_H, h)))

    def canInsertFromMimeData(self, source):
        return source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        """Dropping or pasting files attaches them. Without this Qt inserts the file path as text."""
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                return
        super().insertFromMimeData(source)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focus_changed.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focus_changed.emit(False)


class Composer(QFrame):
    """Chat-style input: attachment chips, auto-growing text, attach menu, region, send."""
    submitted = Signal()
    attach_requested = Signal(str)      # "document" | "inbox"
    attachment_removed = Signal(int)
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setProperty("composer", True)
        self._atts = []
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 10, 8)
        root.setSpacing(4)

        self.chip_host = QWidget()
        self.chip_flow = FlowLayout(self.chip_host)
        self.chip_host.hide()
        root.addWidget(self.chip_host)

        self.edit = ComposerEdit()
        self.edit.setPlaceholderText("Ask Aegis to summarise, draft, explain…  Drop files here or paste text.")
        self.edit.submit.connect(self._submit)
        self.edit.focus_changed.connect(self._focus)
        self.edit.files_dropped.connect(self.files_dropped)
        self.edit.setToolTip(TIPS["composer"])
        root.addWidget(self.edit)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.attach_btn = QToolButton()
        self.attach_btn.setText("+")
        self.attach_btn.setProperty("kind", "round")
        self.attach_btn.setToolTip("Attach a document (opens Finder)")
        self.attach_btn.setCursor(Qt.PointingHandCursor)
        self.attach_btn.clicked.connect(lambda: self.attach_requested.emit("document"))
        bar.addWidget(self.attach_btn)

        self.inbox_btn = QToolButton()
        self.inbox_btn.setText("📧")
        self.inbox_btn.setProperty("kind", "round")
        self.inbox_btn.setToolTip("Attach inbox digest")
        self.inbox_btn.setCursor(Qt.PointingHandCursor)
        self.inbox_btn.clicked.connect(lambda: self.attach_requested.emit("inbox"))
        bar.addWidget(self.inbox_btn)

        bar.addStretch()

        self.send_btn = QToolButton()
        self.send_btn.setText("↑")
        self.send_btn.setProperty("kind", "send")
        self.send_btn.setToolTip(TIPS["send"])
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._submit)
        bar.addWidget(self.send_btn)
        root.addLayout(bar)

    # -- public API
    def text(self) -> str: return self.edit.toPlainText().strip()
    def attachments(self): return list(self._atts)
    def set_text(self, t: str): self.edit.setPlainText(t)
    def focus(self): self.edit.setFocus()

    def set_attachments(self, atts):
        self._atts = list(atts)
        while self.chip_flow.count():
            it = self.chip_flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, a in enumerate(self._atts):
            tip = f"{a.name}\n{a.chars:,} characters. The file name is not sent."
            chip = Chip(a.name, tip)
            chip.removed.connect(lambda i=i: self.attachment_removed.emit(i))
            self.chip_flow.addWidget(chip)
        self.chip_host.setVisible(bool(self._atts))

    def clear(self):
        self.edit.clear()
        self.set_attachments([])

    def set_busy(self, busy: bool):
        for w in (self.edit, self.attach_btn, self.inbox_btn, self.send_btn):
            w.setEnabled(not busy)

    # -- internals
    def _submit(self):
        if self.send_btn.isEnabled():
            self.submitted.emit()

    def _focus(self, on: bool):
        self.setProperty("focused", on)
        self.style().unpolish(self)
        self.style().polish(self)
