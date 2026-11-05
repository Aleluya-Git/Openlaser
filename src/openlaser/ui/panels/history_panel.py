"""Panel de historial: lista de pasos deshacibles; un clic vuelve a ese punto."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from openlaser.document.commands import UndoStack
from openlaser.i18n import tr


class HistoryPanel(QWidget):
    def __init__(self, undo: UndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Historial"))
        self.list.itemClicked.connect(self._clicked)
        lay.addWidget(self.list)
        hint = QLabel(tr("Haga clic en un paso para volver a ese estado; los pasos en gris se pueden rehacer."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        lay.addWidget(hint)
        undo.listeners.append(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        texts, applied = self.undo.history()
        self.list.blockSignals(True)
        self.list.clear()
        start = QListWidgetItem("⌂ " + tr("Estado inicial"))
        start.setData(Qt.ItemDataRole.UserRole, 0)
        self.list.addItem(start)
        for i, text in enumerate(texts, 1):
            it = QListWidgetItem(f"{i}. {text}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            if i > applied:
                it.setForeground(QColor(128, 128, 128))
            self.list.addItem(it)
        self.list.setCurrentRow(applied)
        self.list.blockSignals(False)

    def _clicked(self, item: QListWidgetItem) -> None:
        target = int(item.data(Qt.ItemDataRole.UserRole))
        if target != self.undo.history()[1]:
            self.undo.go_to(target)
