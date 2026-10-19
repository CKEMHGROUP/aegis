"""
Design tokens and one QSS generator for light and dark.
Everything visual comes from the token tables, so dark mode needs no separate stylesheet
and no hard-coded colours leak into widgets.
"""
from __future__ import annotations

LIGHT = dict(
    bg="#F5F5F7", surface="#FFFFFF", surface_alt="#FAFAFC", header="#FFFFFF",
    text="#1D1D1F", text2="#6E6E73", text3="#8E8E93",
    border="#E5E5EA", border_strong="#D1D1D6", hover="#F2F2F7", pressed="#E5E5EA",
    accent="#007AFF", accent_hover="#0062CC", accent_text="#FFFFFF", accent_soft="#E5F1FF",
    danger="#FF3B30", danger_hover="#D70015", danger_soft="#FFE9E7",
    success="#34C759", warning="#FF9500", segment_bg="#E9E9EB", segment_on="#FFFFFF",
    tag_bg="#DCEBFF", tag_fg="#0A4FA8", manual_bg="#FFE1DE",
)
DARK = dict(
    bg="#1C1C1E", surface="#2C2C2E", surface_alt="#242426", header="#2C2C2E",
    text="#F5F5F7", text2="#AEAEB2", text3="#8E8E93",
    border="#3A3A3C", border_strong="#48484A", hover="#3A3A3C", pressed="#48484A",
    accent="#0A84FF", accent_hover="#409CFF", accent_text="#FFFFFF", accent_soft="#0B2A4A",
    danger="#FF453A", danger_hover="#FF6961", danger_soft="#4A1F1C",
    success="#30D158", warning="#FF9F0A", segment_bg="#3A3A3C", segment_on="#636366",
    tag_bg="#12365F", tag_fg="#7CB8FF", manual_bg="#5A2623",
)


def tokens(dark: bool) -> dict:
    return DARK if dark else LIGHT


def build_qss(dark: bool) -> str:
    t = tokens(dark)
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {t['text2']}; font-size: 12px; }}
QLabel[role="caption"] {{ color: {t['text3']}; font-size: 11px; }}
QLabel[role="caption"][status="active"] {{ color: {t['accent']}; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="h1"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="chip"] {{ font-size: 12px; }}
QLabel[role="warn"] {{ color: {t['danger']}; font-weight: 600; }}

QFrame#TopHeader {{ background: {t['header']}; border-bottom: 1px solid {t['border']}; }}
QFrame[card="true"] {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 12px; }}

QTextEdit, QPlainTextEdit {{
    background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: 8px;
    padding: 10px; selection-background-color: {t['accent']}; selection-color: {t['accent_text']};
}}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {t['accent']}; background: {t['surface']}; }}

QLineEdit, QSpinBox, QDateEdit {{
    background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 7px;
    padding: 4px 8px; selection-background-color: {t['accent']};
}}
QLineEdit:focus, QSpinBox:focus, QDateEdit:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled {{ color: {t['text3']}; background: {t['hover']}; }}

QComboBox {{
    background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 7px;
    padding: 2px 22px 2px 8px; min-height: 20px;
}}
QComboBox:hover {{ background: {t['hover']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px;
    selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; padding: 4px;
}}

QPushButton {{
    background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 7px;
    padding: 3px 12px; min-height: 20px; font-weight: 500;
}}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:pressed {{ background: {t['pressed']}; }}
QPushButton:disabled {{ color: {t['text3']}; background: {t['hover']}; border-color: {t['border']}; }}
QPushButton[kind="primary"] {{
    background: {t['accent']}; border: none; color: {t['accent_text']}; font-weight: 600;
    border-radius: 7px; padding: 5px 16px; min-height: 22px;
}}
QPushButton[kind="primary"]:hover {{ background: {t['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {t['border_strong']}; color: {t['text3']}; }}
QPushButton[kind="danger"] {{ color: {t['danger']}; }}
QPushButton[kind="danger"]:hover {{ background: {t['danger_soft']}; }}
QPushButton[kind="quiet"] {{ background: transparent; border: none; color: {t['accent']}; padding: 6px 8px; }}
QPushButton[kind="quiet"]:hover {{ background: {t['accent_soft']}; }}

QTabWidget::pane {{ border: none; background: transparent; top: 6px; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar {{ background: {t['segment_bg']}; border-radius: 8px; }}
QTabBar::tab {{
    background: transparent; color: {t['text2']}; padding: 5px 16px; margin: 2px;
    border-radius: 6px; font-weight: 500; min-width: 90px;
}}
QTabBar::tab:selected {{ background: {t['segment_on']}; color: {t['text']}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {t['text']}; }}

QListWidget {{
    background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {t['accent']}; color: {t['accent_text']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['border_strong']}; border-radius: 4px; min-width: 30px; }}

QSplitter::handle {{ background: transparent; width: 10px; }}
QMenuBar {{ background: transparent; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border_strong']}; padding: 4px 8px; }}

QLabel[pill="ok"]   {{ background: {t['accent_soft']}; color: {t['accent']}; border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
QLabel[pill="warn"] {{ background: {t['danger_soft']}; color: {t['danger']}; border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
QLabel[pill="local"] {{ background: {t['accent_soft']}; color: {t['success']}; border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
QLabel[step="idle"]   {{ color: {t['text3']}; font-size: 12px; padding: 2px 8px; }}
QLabel[step="active"] {{ color: {t['accent_text']}; background: {t['accent']}; border-radius: 10px; font-size: 12px; font-weight: 600; padding: 2px 10px; }}
QLabel[step="done"]   {{ color: {t['accent']}; font-size: 12px; font-weight: 600; padding: 2px 8px; }}

/* ---- front window */
QFrame[composer="true"] {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 16px; }}
QFrame[composer="true"][focused="true"] {{ border: 1px solid {t['accent']}; }}
QTextEdit#ComposerEdit {{ background: transparent; border: none; padding: 4px 2px; }}
QTextEdit#ComposerEdit:focus {{ background: transparent; border: none; }}
QFrame[chip="true"] {{ background: {t['segment_bg']}; border-radius: 9px; }}
QToolButton[kind="chipx"] {{ border: none; background: transparent; color: {t['text2']}; font-size: 14px; padding: 0 4px; }}
QToolButton[kind="chipx"]:hover {{ color: {t['danger']}; }}
QToolButton[kind="round"] {{ background: transparent; border: 1px solid {t['border_strong']}; border-radius: 13px;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; font-size: 16px; color: {t['text2']}; }}
QToolButton[kind="round"]:hover {{ background: {t['hover']}; color: {t['text']}; }}
QToolButton[kind="round"]:disabled {{ background: {t['hover']}; color: {t['text3']}; }}
QToolButton[kind="send"] {{ background: {t['accent']}; color: {t['accent_text']}; border: none; border-radius: 14px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 16px; font-weight: 700; }}
QToolButton[kind="send"]:hover {{ background: {t['accent_hover']}; }}
QToolButton[kind="send"]:disabled {{ background: {t['border_strong']}; color: {t['text3']}; }}
QToolButton[kind="bar"] {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 3px 9px; color: {t['text']}; }}
QToolButton[kind="bar"]:hover {{ background: {t['hover']}; }}
QToolButton[kind="bar"]:checked {{ background: {t['accent_soft']}; color: {t['accent']}; }}
QToolButton[kind="bar"]::menu-indicator {{ image: none; }}
QComboBox[kind="ghost"] {{ background: transparent; border: none; color: {t['text2']}; padding: 2px 18px 2px 6px; }}
QComboBox[kind="ghost"]:hover {{ background: {t['hover']}; }}
QPushButton[kind="suggest"] {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 15px; padding: 6px 16px; }}
QPushButton[kind="suggest"]:hover {{ background: {t['hover']}; border-color: {t['accent']}; }}
QFrame[bubble="user"] {{ background: {t['accent_soft']}; border: none; border-radius: 14px; }}
QFrame[bubble="note"] {{ background: transparent; border: none; }}

/* ---- inspector */
QDockWidget {{ font-weight: 600; }}
QDockWidget::title {{ background: {t['header']}; border-bottom: 1px solid {t['border']}; padding: 8px 12px; text-align: left; }}
QTableWidget {{ background: {t['surface']}; alternate-background-color: {t['surface_alt']}; border: 1px solid {t['border']};
    border-radius: 8px; gridline-color: transparent; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
QTableWidget::item {{ padding: 3px 6px; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid {t['border']}; padding: 4px 6px;
    color: {t['text2']}; font-size: 11px; font-weight: 600; }}
QCheckBox {{ spacing: 6px; }}
"""
