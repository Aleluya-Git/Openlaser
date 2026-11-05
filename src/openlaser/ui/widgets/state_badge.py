"""Indicadores de estado con color, símbolo y texto (nunca sólo color)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget

from openlaser.machine.states import DisplayState
from openlaser.ui import theme


def state_colors(state: DisplayState) -> tuple[str, str]:
    t = theme.current()
    table = {
        DisplayState.DISCONNECTED: (t.surface2, t.muted),
        DisplayState.CONNECTING: (t.info, "#ffffff"),
        DisplayState.IDLE: (t.ok, "#ffffff"),
        DisplayState.RUN: (t.accent, t.accent_text),
        DisplayState.HOLD: (t.warn, "#000000"),
        DisplayState.PAUSED: (t.warn, "#000000"),
        DisplayState.JOG: (t.info, "#ffffff"),
        DisplayState.HOME: (t.info, "#ffffff"),
        DisplayState.CHECK: (t.info, "#ffffff"),
        DisplayState.DOOR: (t.danger, "#ffffff"),
        DisplayState.ALARM: (t.danger, "#ffffff"),
        DisplayState.SLEEP: (t.surface2, t.text),
        DisplayState.ERROR: (t.danger, "#ffffff"),
        DisplayState.ESTOP: ("#b3001b", "#ffffff"),
    }
    return table[state]


class Pill(QLabel):
    """Etiqueta redondeada con fondo de color y texto con símbolo."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(80)
        self.set_style(theme.current().surface2, theme.current().text)

    def set_style(self, bg: str, fg: str, border: str | None = None) -> None:
        b = border or bg
        css = (f"QLabel {{ background: {bg}; color: {fg}; border: 2px solid {b}; border-radius: 10px;"
               f" padding: 2px 10px; font-weight: 700; }}")
        if css != getattr(self, "_css", None):  # evita recalcular estilos 5 veces por segundo
            self._css = css
            self.setStyleSheet(css)

    def _text_size(self) -> QSize:
        font = self.font()
        font.setBold(True)
        fm = QFontMetrics(font)
        # Margen generoso: los símbolos (⌂ ▮ ◐…) pueden venir de una fuente de reserva más ancha.
        return QSize(int(fm.horizontalAdvance(self.text()) * 1.12) + 40, fm.height() + 10)

    def sizeHint(self) -> QSize:
        s = self._text_size()
        return QSize(max(s.width(), self.minimumWidth()), s.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def setText(self, arg__1: str) -> None:
        if arg__1 != self.text():
            super().setText(arg__1)
            self.updateGeometry()

    # --- modo compacto: sólo el símbolo, con el texto completo en el tooltip ---
    def set_label(self, full: str) -> None:
        """Texto «símbolo + palabras»; en modo compacto se muestra sólo el símbolo."""
        self._full = full
        self.setToolTip(full)
        self.setAccessibleName(full)
        self.setText(self._shown())

    def _shown(self) -> str:
        full = getattr(self, "_full", self.text())
        if getattr(self, "_compact", False) and " " in full:
            return full.split(" ", 1)[0]
        return full

    def full_width(self) -> int:
        font = self.font()
        font.setBold(True)
        return int(QFontMetrics(font).horizontalAdvance(getattr(self, "_full", self.text())) * 1.12) + 40

    def set_compact(self, compact: bool) -> None:
        if compact != getattr(self, "_compact", False):
            self._compact = compact
            self.setMinimumWidth(0 if compact else 80)
            self.setText(self._shown())


class StateBadge(Pill):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setMinimumWidth(150)
        self.set_state(DisplayState.DISCONNECTED)

    def set_state(self, state: DisplayState) -> None:
        bg, fg = state_colors(state)
        self.setText(f"{state.symbol}  {state.label}")
        self.setAccessibleName(state.label)
        self.set_style(bg, fg)
