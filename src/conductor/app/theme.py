"""The app's look: one light palette, one style sheet, colours as named tokens.

`apply(app)` runs once in `main()`. It pins the Fusion style and an explicit light
`QPalette` so a dark Windows theme cannot flip the app, raises the base font, and loads
`QSS`. Panels use the tokens below instead of hex literals. Every text colour here
reaches WCAG AA (4.5:1) on each background it is drawn on: white, `ALT_BASE`, its own
tint (`RED` on `RED_BG`, …), `BAND`, `WINDOW`; selected rows draw `TEXT` on `SELECT`.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# ---------------------------------------------------------------- tokens
TEXT = "#1f2328"          # body text
MUTED = "#555d68"         # hints, routine rows, secondary text
WINDOW = "#f3f4f6"        # window / panel background
BASE = "#ffffff"          # tables, trees, text boxes, cards
ALT_BASE = "#eef1f5"      # alternate table rows
BAND = "#e4e9f0"          # repo header rows, table headers
BORDER = "#c3c9d2"        # decorative: panel frames, header separators, gridlines
INPUT_BORDER = "#778497"  # the edge of an input or button: 3:1 on BASE, WINDOW, HOVER, BAND
HOVER = "#e9eef5"
SELECT = "#cfe0f6"        # selection highlight (text stays TEXT)
ACCENT = "#0b5cad"        # primary buttons, selected tab, running
ACCENT_DARK = "#08498a"
DISABLED_BG = "#eceef1"
DISABLED_TEXT = "#626973"  # 4.5:1 on DISABLED_BG, WINDOW, BASE and BAND

RED = "#b3261e"
RED_BG = "#fdecea"
AMBER = "#8a4b00"
AMBER_BG = "#fff3dc"
GREEN = "#1b6e2a"
GREEN_BG = "#e6f4e9"
GREEN_DARK = "#145220"
BLUE = ACCENT              # running
TEAL = "#00695c"           # staged
PURPLE = "#6b2fa3"         # questions: ask / answer
# State icons and the header dot are graphics, not text: 3:1 on WINDOW is the bar.
ICON_ASKING = "#a86500"   # 3:1 on WINDOW, HOVER and BAND too
ICON_IDLE = "#7d848e"
SCROLL = "#7d848e"         # scrollbar handle

# Event kinds by family, for the Events table and trail.
ROUTINE_KINDS = {"enqueue", "worktree", "stage", "queued", "queue_empty", "adopt", "read",
                 "moved", "config_reload", "idle", "recover", "created", "item"}
QUESTION_KINDS = {"ask", "answer", "default", "late", "question_closed"}
HOLD_KINDS = {"hold", "warning", "drift"}
GOOD_KINDS = {"finish", "merge"}

BASE_POINT_SIZE = 10.5
MONO_POINT_SIZE = 10.0


def kind_style(kind: str, red_kinds, status: str | None = None) -> tuple[str | None, str | None, bool]:
    """(text colour, row background, bold) for an event kind; None leaves the default.
    A `finish` is coloured by its outcome (`status`): green only when done."""
    if kind in red_kinds:
        return RED, RED_BG, True
    if kind in HOLD_KINDS:
        return AMBER, AMBER_BG, True
    if kind == "finish" and status is not None and status != "done":
        return (RED, None, True) if status == "red" else (AMBER, None, True)
    if kind in GOOD_KINDS:
        return GREEN, None, False
    if kind in QUESTION_KINDS:
        return PURPLE, None, False
    if kind in ROUTINE_KINDS:
        return MUTED, None, False
    return None, None, False


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setFamilies(["Cascadia Mono", "Consolas", f.family()])
    f.setPointSizeF(MONO_POINT_SIZE)
    return f


QSS = f"""
QMainWindow, QDialog {{ background: {WINDOW}; }}
QToolTip {{ background: {BASE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QLabel[muted="true"] {{ color: {MUTED}; }}

QToolBar {{ background: {WINDOW}; border: none; border-bottom: 1px solid {BORDER}; spacing: 4px; padding: 3px; }}
QToolBar QToolButton {{ padding: 4px 10px; border: 1px solid transparent; border-radius: 4px; color: {TEXT}; }}
QToolBar QToolButton:hover {{ background: {HOVER}; border-color: {BORDER}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; background: {WINDOW}; top: -1px; }}
/* Unselected tab text takes the bar's colour, so a tab given its own colour with
   QTabBar.setTabTextColor (Questions, Proposed) keeps it; the selected tab is TEXT. */
QTabBar {{ color: {MUTED}; }}
QTabBar::tab {{ background: transparent; padding: 7px 18px 6px 18px;
               border: none; border-bottom: 3px solid transparent; margin-right: 2px; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; }}
QTabBar::tab:selected {{ color: {TEXT}; font-weight: bold; border-bottom: 3px solid {ACCENT}; }}

QPushButton {{ background: {BASE}; color: {TEXT}; border: 1px solid {INPUT_BORDER}; border-radius: 4px;
              padding: 5px 14px; }}
QPushButton:hover {{ background: {HOVER}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BAND}; }}
QPushButton:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px dashed {INPUT_BORDER}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; font-weight: bold; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[role="approve"] {{ background: {GREEN}; color: #ffffff; border-color: {GREEN}; font-weight: bold; }}
QPushButton[role="approve"]:hover {{ background: {GREEN_DARK}; }}
QPushButton[role="primary"]:disabled, QPushButton[role="approve"]:disabled {{
    background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px dashed {INPUT_BORDER}; font-weight: normal; }}
QPushButton[role="compact"] {{ padding: 1px 10px; }}
QPushButton[tone="danger"] {{ color: {RED}; border: 1px solid {RED}; font-weight: bold; }}
QPushButton[tone="danger"]:hover {{ background: {RED_BG}; }}
QPushButton[tone="default"] {{ border: 2px solid {AMBER}; font-weight: bold; }}
QPushButton[tone="default"]:hover {{ background: {AMBER_BG}; }}
QLabel[pill="block"], QLabel[pill="default"], QLabel[pill="answered"] {{
    border-radius: 4px; padding: 4px 10px; font-weight: bold; font-size: {BASE_POINT_SIZE * 1.25}pt; }}
QLabel[pill="block"] {{ color: {RED}; background: {RED_BG}; border: 1px solid {RED}; }}
QLabel[pill="default"] {{ color: {AMBER}; background: {AMBER_BG}; border: 1px solid {AMBER}; }}
QLabel[pill="answered"] {{ color: {GREEN}; background: {GREEN_BG}; border: 1px solid {GREEN}; }}

QLineEdit, QComboBox, QDateTimeEdit, QPlainTextEdit {{ background: {BASE}; color: {TEXT};
    border: 1px solid {INPUT_BORDER}; border-radius: 3px; padding: 3px 5px;
    selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}

QTreeView, QTableView {{ background: {BASE}; alternate-background-color: {ALT_BASE}; color: {TEXT};
    border: 1px solid {BORDER}; gridline-color: #e1e5ea;
    selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QTreeView::item {{ min-height: 26px; }}
QTableView::item {{ padding-left: 6px; }}
QHeaderView::section {{ background: {BAND}; color: {TEXT}; font-weight: bold; padding: 5px 8px;
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; }}

QGroupBox[card="true"] {{ background: {BASE}; border: 1px solid {BORDER}; border-left: 6px solid {BORDER};
    margin: 0; padding: 6px 8px 6px 8px; }}
QGroupBox[severity="block"] {{ border-left-color: {RED}; }}
QGroupBox[severity="default"] {{ border-left-color: {AMBER}; }}
QGroupBox[severity="answered"] {{ border-left-color: {GREEN}; }}
QScrollArea {{ border: none; background: {WINDOW}; }}
QScrollArea > QWidget > QWidget {{ background: {WINDOW}; }}
QSplitter::handle {{ background: {WINDOW}; }}
QScrollBar:vertical {{ background: {WINDOW}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {WINDOW}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {SCROLL}; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QStatusBar {{ background: {WINDOW}; color: {TEXT}; border-top: 1px solid {BORDER}; }}
"""


def palette() -> QPalette:
    p = QPalette()
    roles = QPalette.ColorRole
    for role, value in ((roles.Window, WINDOW), (roles.WindowText, TEXT), (roles.Base, BASE),
                        (roles.AlternateBase, ALT_BASE), (roles.Text, TEXT), (roles.Button, BASE),
                        (roles.ButtonText, TEXT), (roles.ToolTipBase, BASE), (roles.ToolTipText, TEXT),
                        (roles.PlaceholderText, MUTED), (roles.Highlight, SELECT),
                        (roles.HighlightedText, TEXT), (roles.Link, ACCENT), (roles.BrightText, RED),
                        (roles.Light, BASE), (roles.Midlight, BAND), (roles.Mid, INPUT_BORDER),
                        (roles.Dark, INPUT_BORDER), (roles.Shadow, "#5a616b")):
        p.setColor(role, QColor(value))
    dis = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        p.setColor(dis, role, QColor(DISABLED_TEXT))
    p.setColor(dis, roles.Button, QColor(DISABLED_BG))
    # An unfocused window keeps the same readable selection.
    p.setColor(QPalette.ColorGroup.Inactive, roles.Highlight, QColor(SELECT))
    p.setColor(QPalette.ColorGroup.Inactive, roles.HighlightedText, QColor(TEXT))
    return p


def apply(app) -> None:
    """Fusion + the light palette + the base font + the style sheet, app-wide."""
    app.setStyle("Fusion")
    app.setPalette(palette())
    f = app.font()
    if sys.platform == "win32":
        f.setFamily("Segoe UI")
    f.setPointSizeF(BASE_POINT_SIZE)
    app.setFont(f)
    app.setStyleSheet(QSS)


def repolish(w) -> None:
    """Re-apply the style sheet after a dynamic property (`role`, `severity`) changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
