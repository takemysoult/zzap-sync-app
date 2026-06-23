"""Central visual theme (Qt Style Sheets) for the desktop app.

Design system: a "data-dense dashboard" look — blue primary + amber/red accents on
slate neutrals, native Windows typography (Segoe UI for the UI, Consolas for logs).
Goal: clean, professional, легко для не-технического пользователя.

Screens stay style-free: instead of inline ``setStyleSheet`` they set dynamic
properties (``role``, ``status``, ``severity``, ``class``) that the global stylesheet
targets. Use :func:`set_status` / :func:`repolish` when a property changes at runtime
so Qt re-evaluates the stylesheet for that widget.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QWidget

# --- palette ----------------------------------------------------------------
BG = "#F5F7FA"            # window background (slate-50-ish)
SURFACE = "#FFFFFF"       # cards, inputs, tables
ALT = "#F1F5F9"           # alternating rows / soft fills (slate-100)
HEADER_BG = "#EEF2F7"     # table header
BORDER = "#D8DEE9"        # default border
TEXT = "#1E293B"          # primary text (slate-800)
MUTED = "#64748B"         # secondary text (slate-500)
PRIMARY = "#1E40AF"       # blue-800
PRIMARY_HOVER = "#1D4ED8"  # blue-700
PRIMARY_PRESSED = "#1E3A8A"  # blue-900
SEL = "#DBEAFE"           # selection (blue-100)
SUCCESS = "#15803D"       # green-700
DANGER = "#B91C1C"        # red-700
DANGER_SOLID = "#DC2626"  # red-600 (filled banner)
WARN = "#B45309"          # amber-700 (text + filled split banner)

MONO_FAMILY = "Consolas"
UI_FAMILY = "Segoe UI"

# Per-row status text colors (set on table items, not via QSS).
STATUS_COLORS = {
    "OK": SUCCESS, "RESEND_OK": SUCCESS,
    "FAIL": DANGER, "ERROR": DANGER,
    "STAGED": WARN, "RUNNING": PRIMARY,
}


def color(hex_str: str) -> QColor:
    return QColor(hex_str)


_STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "{UI_FAMILY}";
    font-size: 10pt;
}}
QMainWindow, QDialog {{ background-color: {BG}; }}
QLabel {{ background: transparent; }}

/* Tabs */
QTabWidget::pane {{
    border: 1px solid {BORDER}; border-radius: 8px;
    background: {SURFACE}; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {MUTED};
    padding: 8px 18px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* GroupBox = card */
QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    margin-top: 14px; padding: 14px 12px 12px 12px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {TEXT};
}}

/* Inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QAbstractSpinBox {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px;
    selection-background-color: {PRIMARY}; selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus,
QAbstractSpinBox:focus {{ border: 1px solid {PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {BG}; color: {MUTED};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER};
    selection-background-color: {SEL}; selection-color: {TEXT};
}}

/* Buttons */
QPushButton {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {PRIMARY}; color: {PRIMARY}; }}
QPushButton:pressed {{ background: {ALT}; }}
QPushButton:disabled {{ background: {BG}; color: {MUTED}; border-color: {BORDER}; }}
QPushButton[class="primary"] {{
    background: {PRIMARY}; color: white; border: 1px solid {PRIMARY};
}}
QPushButton[class="primary"]:hover {{
    background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; color: white;
}}
QPushButton[class="primary"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[class="primary"]:disabled {{
    background: {MUTED}; border-color: {MUTED}; color: white;
}}

/* Tables */
QTableView, QTableWidget {{
    background: {SURFACE}; alternate-background-color: {ALT};
    gridline-color: {BORDER}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {SEL}; selection-color: {TEXT};
}}
QTableView::item, QTableWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{
    background: {HEADER_BG}; color: {MUTED};
    padding: 6px 8px; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {HEADER_BG}; border: none; }}

/* List (warehouses, etc.) */
QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
}}
QListWidget::item {{ padding: 5px 6px; }}
QListWidget::item:selected {{ background: {SEL}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {ALT}; }}

/* Label roles */
QLabel[role="hint"] {{ color: {MUTED}; }}
QLabel[role="checklist"] {{
    color: {TEXT}; background: {ALT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 10px;
}}
QLabel[status="ok"] {{ color: {SUCCESS}; font-weight: 600; }}
QLabel[status="error"] {{ color: {DANGER}; font-weight: 600; }}
QLabel[status="info"] {{ color: {MUTED}; }}
QLabel[role="banner"] {{
    color: white; border-radius: 6px; padding: 10px 12px; font-weight: 600;
}}
QLabel[role="banner"][severity="high"] {{ background: {DANGER_SOLID}; }}
QLabel[role="banner"][severity="split"] {{ background: {WARN}; }}

/* Checkboxes / radios — keep native indicators, just space them */
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 6px; min-height: 28px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 6px; min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Tooltip */
QToolTip {{
    background: {TEXT}; color: white; border: none;
    padding: 6px 8px; border-radius: 4px;
}}
"""


def apply_theme(app: QApplication) -> None:
    """Set the application font + global stylesheet."""
    font = QFont(UI_FAMILY, 10)
    app.setFont(font)
    app.setStyleSheet(_STYLESHEET)


def repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet for a widget after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def set_status(label, text: str, kind: str = "") -> None:
    """Set a status label's text + colour role ('ok' | 'error' | 'info' | '')."""
    label.setText(text)
    label.setProperty("status", kind or None)
    repolish(label)


def mono_font(point_size: int = 9) -> QFont:
    f = QFont(MONO_FAMILY, point_size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f
