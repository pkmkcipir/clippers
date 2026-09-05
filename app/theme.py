"""
Visual identity for AI Klipers.

Palette rationale (deliberately not the generic "AI app" defaults):
  - Base is a deep charcoal-navy (#14151C), not pure black -- easier on the
    eyes for long editing sessions, and reads as "editing suite" rather
    than "chat window".
  - A single confident accent, electric indigo (#6E56F8), marks anything
    interactive/selected. It doesn't fight with...
  - ...a warm coral (#FF6B5B) reserved *only* for the viral-score signature
    element (see widgets/score_ring.py) and destructive actions, so the
    "hot clip" signal stays visually unique instead of blending into
    generic UI chrome.
  - Segoe UI is used as the primary typeface on purpose: this ships as a
    native Windows .exe, so leaning on the OS's own system font (like
    CapCut/Premiere do on Windows) reads as "at home on the platform"
    rather than a web page pasted into a window.
"""
from __future__ import annotations

COLORS = {
    "dark": {
        "bg": "#14151C",
        "surface": "#1C1E29",
        "surface_alt": "#20222E",
        "elevated": "#262838",
        "border": "#2E3142",
        "text": "#F2F1F7",
        "text_muted": "#8B8D9E",
        "accent": "#6E56F8",
        "accent_hover": "#8171FA",
        "accent_text": "#FFFFFF",
        "coral": "#FF6B5B",
        "success": "#3DDC97",
        "warning": "#F5B759",
    },
    "light": {
        "bg": "#F2F1F7",
        "surface": "#FFFFFF",
        "surface_alt": "#F7F7FB",
        "elevated": "#FFFFFF",
        "border": "#E1E1EA",
        "text": "#1B1C24",
        "text_muted": "#6B6C7B",
        "accent": "#6E56F8",
        "accent_hover": "#5A44E0",
        "accent_text": "#FFFFFF",
        "coral": "#E85446",
        "success": "#1FA971",
        "warning": "#C98A2C",
    },
}

FONT_STACK = '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'


def get_stylesheet(theme: str = "dark") -> str:
    c = COLORS.get(theme, COLORS["dark"])
    return f"""
    * {{
        font-family: {FONT_STACK};
        outline: none;
    }}
    QMainWindow, QWidget#pageRoot {{
        background-color: {c['bg']};
        color: {c['text']};
    }}
    QWidget {{
        color: {c['text']};
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel[role="title"] {{
        font-size: 22px;
        font-weight: 600;
    }}
    QLabel[role="subtitle"] {{
        color: {c['text_muted']};
        font-size: 13px;
    }}
    QLabel[role="muted"] {{
        color: {c['text_muted']};
    }}

    /* ---- Sidebar ---- */
    QWidget#sidebar {{
        background-color: {c['surface']};
        border-right: 1px solid {c['border']};
    }}
    QLabel#sidebarBrand {{
        font-size: 17px;
        font-weight: 700;
        padding: 20px 18px 12px 18px;
        color: {c['text']};
    }}
    QPushButton.navItem {{
        text-align: left;
        padding: 10px 18px;
        border: none;
        border-left: 3px solid transparent;
        background: transparent;
        color: {c['text_muted']};
        font-size: 13px;
        font-weight: 500;
        border-radius: 0px;
    }}
    QPushButton.navItem:hover {{
        background-color: {c['surface_alt']};
        color: {c['text']};
    }}
    QPushButton.navItem:checked {{
        background-color: {c['elevated']};
        color: {c['text']};
        border-left: 3px solid {c['accent']};
        font-weight: 600;
    }}

    /* ---- Cards ---- */
    QFrame.card {{
        background-color: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 12px;
    }}
    QFrame.card:hover {{
        border: 1px solid {c['accent']};
    }}

    /* ---- Buttons ---- */
    QPushButton.primary {{
        background-color: {c['accent']};
        color: {c['accent_text']};
        border: none;
        border-radius: 8px;
        padding: 9px 18px;
        font-weight: 600;
        font-size: 13px;
    }}
    QPushButton.primary:hover {{ background-color: {c['accent_hover']}; }}
    QPushButton.primary:disabled {{ background-color: {c['border']}; color: {c['text_muted']}; }}

    QPushButton.secondary {{
        background-color: transparent;
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 9px 18px;
        font-size: 13px;
    }}
    QPushButton.secondary:hover {{ border: 1px solid {c['accent']}; color: {c['accent']}; }}

    QPushButton.danger {{
        background-color: transparent;
        color: {c['coral']};
        border: 1px solid {c['coral']};
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 12px;
    }}
    QPushButton.danger:hover {{ background-color: {c['coral']}; color: white; }}

    /* ---- Inputs ---- */
    QLineEdit, QComboBox, QTextEdit, QSpinBox {{
        background-color: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px 10px;
        color: {c['text']};
        font-size: 13px;
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
        border: 1px solid {c['accent']};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}

    /* ---- Progress ---- */
    QProgressBar {{
        background-color: {c['surface_alt']};
        border: none;
        border-radius: 6px;
        height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 6px;
    }}

    /* ---- Tables / Lists ---- */
    QTableWidget, QListWidget {{
        background-color: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        gridline-color: {c['border']};
    }}
    QHeaderView::section {{
        background-color: {c['surface_alt']};
        color: {c['text_muted']};
        padding: 8px;
        border: none;
        font-size: 12px;
        font-weight: 600;
    }}
    QTableWidget::item {{ padding: 6px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['text_muted']}; }}

    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; }}
    QTabBar::tab {{
        background: transparent; color: {c['text_muted']};
        padding: 8px 16px; font-size: 13px;
    }}
    QTabBar::tab:selected {{ color: {c['text']}; font-weight: 600; border-bottom: 2px solid {c['accent']}; }}
    """
