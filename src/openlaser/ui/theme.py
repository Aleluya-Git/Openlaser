"""Temas claro, oscuro y alto contraste (paleta Qt + hoja de estilos)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    ok: str
    warn: str
    danger: str
    info: str
    canvas_bg: str
    canvas_area: str
    grid_minor: str
    grid_major: str
    console_bg: str


DARK = Theme("dark", bg="#15171c", surface="#1d2027", surface2="#262a33", border="#363b47", text="#e7e9ee",
             muted="#9aa3b2", accent="#ff8a3d", accent_text="#1a0f05", ok="#3fb950", warn="#e3b341",
             danger="#ff5c57", info="#58a6ff", canvas_bg="#0f1115", canvas_area="#1b1e24",
             grid_minor="#262a32", grid_major="#353a45", console_bg="#111318")
LIGHT = Theme("light", bg="#eef0f3", surface="#ffffff", surface2="#f5f6f8", border="#c9ced8", text="#1d2128",
              muted="#5c6575", accent="#c2520c", accent_text="#ffffff", ok="#1a7f37", warn="#9a6700",
              danger="#cf222e", info="#0969da", canvas_bg="#d9dde3", canvas_area="#ffffff",
              grid_minor="#eceef2", grid_major="#d5d9e0", console_bg="#fbfbfc")
CONTRAST = Theme("contrast", bg="#000000", surface="#000000", surface2="#0d0d0d", border="#ffffff",
                 text="#ffffff", muted="#e0e0e0", accent="#ffd400", accent_text="#000000", ok="#00ff66",
                 warn="#ffd400", danger="#ff3b3b", info="#00c8ff", canvas_bg="#000000", canvas_area="#0a0a0a",
                 grid_minor="#2a2a2a", grid_major="#5a5a5a", console_bg="#000000")

THEMES = {t.name: t for t in (DARK, LIGHT, CONTRAST)}
_current: Theme = DARK


def current() -> Theme:
    return _current


def apply_theme(app: QApplication, name: str) -> Theme:
    global _current
    t = THEMES.get(name, DARK)
    _current = t
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.bg, QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface, QPalette.ColorRole.AlternateBase: t.surface2,
        QPalette.ColorRole.Text: t.text, QPalette.ColorRole.Button: t.surface2,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_text, QPalette.ColorRole.ToolTipBase: t.surface2,
        QPalette.ColorRole.ToolTipText: t.text, QPalette.ColorRole.PlaceholderText: t.muted,
        QPalette.ColorRole.Link: t.info,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted).darker(140 if t.name != "light" else 80))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))
    return t


def stylesheet(t: Theme) -> str:
    bw = "2px" if t.name == "contrast" else "1px"
    return f"""
QWidget {{ color: {t.text}; }}
QMainWindow, QDialog {{ background: {t.bg}; }}
QDockWidget::title {{ background: {t.surface2}; padding: 4px 8px; border-bottom: {bw} solid {t.border}; }}
QToolBar {{ background: {t.surface}; border: none; border-bottom: {bw} solid {t.border}; spacing: 2px; padding: 2px; }}
QToolButton {{ border: {bw} solid transparent; border-radius: 5px; padding: 3px; }}
QToolButton:hover {{ background: {t.surface2}; border-color: {t.border}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.accent}; color: {t.accent_text}; }}
QToolButton:disabled {{ color: {t.muted}; }}
QPushButton {{ background: {t.surface2}; border: {bw} solid {t.border}; border-radius: 5px; padding: 5px 10px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.accent}; color: {t.accent_text}; }}
QPushButton:disabled {{ color: {t.muted}; border-style: dashed; }}
QPushButton:focus, QToolButton:focus {{ outline: none; border: 2px solid {t.info}; }}
QPushButton[role="danger"] {{ background: {t.danger}; color: #ffffff; font-weight: 700; border: 2px solid {t.danger}; }}
QPushButton[role="danger"]:hover {{ border: 2px solid {t.text}; }}
QPushButton[role="estop"] {{ background: #b3001b; color: #ffffff; font-weight: 800; border: 3px solid #ffd400;
    border-radius: 6px; padding: 6px 12px; }}
QPushButton[role="estop"]:hover {{ background: #d4001f; }}
QPushButton[role="accent"] {{ background: {t.accent}; color: {t.accent_text}; font-weight: 700; border-color: {t.accent}; }}
QPushButton[role="jog"] {{ min-width: 38px; min-height: 34px; font-size: 15px; font-weight: 700; }}
QPushButton[role="chip"] {{ padding: 3px 6px; border-radius: 10px; }}
QPushButton[role="chip"]:checked {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTableWidget, QTreeWidget {{
    background: {t.surface}; border: {bw} solid {t.border}; border-radius: 4px; padding: 2px 4px;
    selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {t.info}; }}
QGroupBox {{ border: {bw} solid {t.border}; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {t.muted}; }}
QHeaderView::section {{ background: {t.surface2}; border: none; border-right: 1px solid {t.border}; padding: 3px; }}
QStatusBar {{ background: {t.surface}; border-top: {bw} solid {t.border}; }}
QMenuBar {{ background: {t.surface}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QMenu {{ background: {t.surface}; border: {bw} solid {t.border}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QProgressBar {{ border: {bw} solid {t.border}; border-radius: 4px; text-align: center; background: {t.surface2}; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
QTabBar::tab {{ background: {t.surface2}; padding: 4px 10px; border: {bw} solid {t.border}; }}
QTabBar::tab:selected {{ background: {t.surface}; border-bottom-color: {t.accent}; }}
QToolTip {{ background: {t.surface2}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
QFrame[role="banner"] {{ background: {t.surface}; border-bottom: {bw} solid {t.border}; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="coord"] {{ font-family: "Consolas", "DejaVu Sans Mono", monospace; font-size: 13px; }}
QLabel[role="big-coord"] {{ font-family: "Consolas", "DejaVu Sans Mono", monospace; font-size: 16px; font-weight: 700; }}
"""
