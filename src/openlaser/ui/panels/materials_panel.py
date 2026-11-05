"""Panel de la biblioteca de materiales."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from openlaser.i18n import N_, tr
from openlaser.materials.library import MaterialEntry, MaterialLibrary
from openlaser.ui import icons

OPERATION_LABELS = {"cut": N_("Corte"), "line": N_("Grabado línea"), "fill": N_("Relleno"), "image": N_("Imagen")}
RESULT_LABELS = {"": N_("sin validar"), "ok": N_("correcto"), "regular": N_("regular"), "fail": N_("fallido")}
WARNING = N_("Ningún valor es universal: cada combinación debe validarse en su máquina, módulo láser y material reales.")


class MaterialDialog(QDialog):
    def __init__(self, entry: MaterialEntry, photos_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Material"))
        self.entry = MaterialEntry.from_dict(entry.to_dict())
        self.photos_dir = photos_dir
        e = self.entry
        root = QVBoxLayout(self)
        grid = QHBoxLayout()
        f1, f2 = QFormLayout(), QFormLayout()
        grid.addLayout(f1)
        grid.addLayout(f2)
        root.addLayout(grid)
        self.material = QLineEdit(e.material)
        self.variant = QLineEdit(e.variant)
        self.thickness = self._d(0, 200, e.thickness_mm, 2, " mm")
        self.operation = QComboBox()
        for k, label in OPERATION_LABELS.items():
            self.operation.addItem(tr(label), k)
        self.operation.setCurrentIndex(max(0, self.operation.findData(e.operation)))
        self.speed = self._d(1, 100000, e.speed_mm_min, 0, " mm/min")
        self.pmin = self._d(0, 100, e.power_min_pct, 1, " %")
        self.pmax = self._d(0, 100, e.power_max_pct, 1, " %")
        self.passes = QSpinBox()
        self.passes.setRange(1, 100)
        self.passes.setValue(e.passes)
        self.interval = self._d(0.01, 5, e.interval_mm, 3, " mm")
        self.air = QCheckBox()
        self.air.setChecked(e.air_assist)
        self.freq = self._d(0, 100000, e.frequency, 0)
        self.freq.setSpecialValueText(tr("no se usa"))
        for label, w in ((N_("Material"), self.material), (N_("Variante"), self.variant),
                         (N_("Espesor"), self.thickness), (N_("Operación"), self.operation),
                         (N_("Velocidad"), self.speed), (N_("Potencia mínima"), self.pmin),
                         (N_("Potencia máxima"), self.pmax)):
            f1.addRow(tr(label), w)
        self.machine = QLineEdit(e.machine)
        self.module = QLineEdit(e.laser_module)
        self.validated = QCheckBox(tr("Validado el"))
        self.validated.setChecked(bool(e.validated_on))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        from PySide6.QtCore import QDate
        self.date.setDate(QDate.fromString(e.validated_on, "yyyy-MM-dd") if e.validated_on else QDate.currentDate())
        vrow = QHBoxLayout()
        vrow.addWidget(self.validated)
        vrow.addWidget(self.date)
        self.result_box = QComboBox()
        for k, label in RESULT_LABELS.items():
            self.result_box.addItem(tr(label), k)
        self.result_box.setCurrentIndex(max(0, self.result_box.findData(e.result)))
        for label, w in ((N_("Pasadas"), self.passes), (N_("Intervalo"), self.interval), (N_("Air Assist"), self.air),
                         (N_("Frecuencia / PPI"), self.freq), (N_("Máquina"), self.machine),
                         (N_("Módulo láser"), self.module)):
            f2.addRow(tr(label), w)
        f2.addRow(tr("Validación"), vrow)
        f2.addRow(tr("Resultado"), self.result_box)
        self.notes = QPlainTextEdit(e.notes)
        self.notes.setMaximumHeight(70)
        root.addWidget(QLabel(tr("Notas")))
        root.addWidget(self.notes)
        prow = QHBoxLayout()
        self.photo = QLabel(tr("Sin fotografía"))
        self.photo.setMinimumSize(160, 100)
        self.photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo.setStyleSheet("border: 1px dashed palette(mid);")
        prow.addWidget(self.photo)
        pcol = QVBoxLayout()
        b_add = QPushButton(icons.icon("image"), tr("Añadir fotografía…"))
        b_add.clicked.connect(self._pick_photo)
        b_del = QPushButton(tr("Quitar fotografía"))
        b_del.clicked.connect(lambda: self._set_photo(""))
        pcol.addWidget(b_add)
        pcol.addWidget(b_del)
        pcol.addStretch(1)
        prow.addLayout(pcol)
        root.addLayout(prow)
        if e.demo:
            demo = QLabel("⚠ " + tr("Entrada de DEMOSTRACIÓN con valores ficticios"))
            demo.setStyleSheet("color: #d4a000; font-weight: 700;")
            root.addWidget(demo)
        warn = QLabel(tr(WARNING))
        warn.setWordWrap(True)
        warn.setProperty("role", "muted")
        root.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self._set_photo(e.photo)

    @staticmethod
    def _d(lo: float, hi: float, v: float, dec: int, suffix: str = "") -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setValue(v)
        if suffix:
            w.setSuffix(suffix)
        return w

    def _pick_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Fotografía del resultado"), "",
                                              tr("Imágenes") + " (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        target = self.photos_dir / f"{self.entry.id}{Path(path).suffix.lower()}"
        try:
            shutil.copyfile(path, target)
        except OSError as exc:
            QMessageBox.warning(self, tr("Fotografía"), str(exc))
            return
        self._set_photo(str(target))

    def _set_photo(self, path: str) -> None:
        self.entry.photo = path
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            self.photo.setPixmap(QPixmap())
            self.photo.setText(tr("Sin fotografía"))
        else:
            self.photo.setPixmap(pm.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))

    def _accept(self) -> None:
        if not self.material.text().strip():
            QMessageBox.warning(self, tr("Material"), tr("Indique el nombre del material"))
            return
        e = self.entry
        e.material = self.material.text().strip()
        e.variant = self.variant.text().strip()
        e.thickness_mm = self.thickness.value()
        e.operation = str(self.operation.currentData())
        e.speed_mm_min = self.speed.value()
        e.power_min_pct = min(self.pmin.value(), self.pmax.value())
        e.power_max_pct = self.pmax.value()
        e.passes = self.passes.value()
        e.interval_mm = self.interval.value()
        e.air_assist = self.air.isChecked()
        e.frequency = self.freq.value()
        e.machine = self.machine.text().strip()
        e.laser_module = self.module.text().strip()
        e.validated_on = self.date.date().toString("yyyy-MM-dd") if self.validated.isChecked() else ""
        e.result = str(self.result_box.currentData())
        if e.validated_on and e.result == "ok":
            e.demo = False
        e.notes = self.notes.toPlainText()
        self.accept()


class MaterialsPanel(QWidget):
    """Lista filtrable; aplica una entrada a la capa actual o guarda la capa como material."""
    apply_requested = Signal(object)        # MaterialEntry
    save_layer_requested = Signal()
    changed = Signal()

    # Tabla compacta: el nombre del material necesita el ancho; los parámetros se ven en el detalle.
    COLUMNS = [N_("Material"), "mm", N_("Operación"), "✔"]

    def __init__(self, library: MaterialLibrary, photos_dir: Path, machine_name: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.photos_dir = photos_dir
        self.machine_name = machine_name
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        filt = QGridLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar material, variante, notas…"))
        self.search.setClearButtonEnabled(True)
        self.op = QComboBox()
        self.op.addItem(tr("Todas las operaciones"), "")
        for k, label in OPERATION_LABELS.items():
            self.op.addItem(tr(label), k)
        self.only_valid = QCheckBox(tr("Sólo validados"))
        self.only_machine = QCheckBox(tr("Sólo esta máquina"))
        filt.addWidget(self.search, 0, 0, 1, 2)
        filt.addWidget(self.op, 1, 0)
        filt.addWidget(self.only_valid, 1, 1)
        filt.addWidget(self.only_machine, 2, 1)
        root.addLayout(filt)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) if c[0].isupper() else c for c in self.COLUMNS])
        for col, tip in enumerate((N_("Material"), N_("Espesor"), N_("Operación"), N_("Resultado"))):
            header = self.table.horizontalHeaderItem(col)
            if header is not None:
                header.setToolTip(tr(tip))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName(tr("Biblioteca de materiales"))
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _i: self._edit())
        root.addWidget(self.table, 1)
        bar = QGridLayout()

        def b(icon_name: str | None, text: str, tip: str, fn, r: int, c: int) -> QPushButton:  # noqa: ANN001
            btn = QPushButton(icons.icon(icon_name), text) if icon_name else QPushButton(text)
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            btn.clicked.connect(lambda _c=False: fn())
            bar.addWidget(btn, r, c)
            return btn

        self.btn_apply = b("layers", tr("Aplicar a la capa"), tr("Copia los parámetros a la capa actual"),
                           self._apply, 0, 0)
        b("save", tr("Guardar capa…"), tr("Crea una entrada con los parámetros de la capa actual"),
          lambda: self.save_layer_requested.emit(), 0, 1)
        b("new", tr("Nuevo"), tr("Nueva entrada"), self._new, 1, 0)
        b(None, tr("Editar…"), tr("Editar la entrada"), self._edit, 1, 1)
        b("copy", tr("Duplicar"), tr("Duplicar la entrada"), self._duplicate, 2, 0)
        b("delete", tr("Eliminar"), tr("Eliminar la entrada"), self._delete, 2, 1)
        b("import", tr("Importar…"), tr("Añadir entradas desde un archivo"), self._import, 3, 0)
        b("export", tr("Exportar…"), tr("Guardar la biblioteca en un archivo"), self._export, 3, 1)
        root.addLayout(bar)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)
        warn = QLabel("⚠ " + tr(WARNING))
        warn.setWordWrap(True)
        warn.setProperty("role", "muted")
        root.addWidget(warn)
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.op.currentIndexChanged.connect(lambda _i: self.refresh())
        self.only_valid.toggled.connect(lambda _v: self.refresh())
        self.only_machine.toggled.connect(lambda _v: self.refresh())
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.refresh()

    # -------------------------------------------------------------- datos
    def visible_entries(self) -> list[MaterialEntry]:
        return self.library.search(self.search.text(), str(self.op.currentData() or ""),
                                   self.machine_name if self.only_machine.isChecked() else "",
                                   self.only_valid.isChecked())

    def refresh(self, select: str | None = None) -> None:
        entries = self.visible_entries()
        current = select or self.current_id()
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            title = ("⚠ " if e.demo else "") + e.material + (f" ({e.variant})" if e.variant else "")
            symbol = "✔" if e.validated else {"ok": "✓", "regular": "~", "fail": "✗"}.get(e.result, "·")
            vals = [title, f"{e.thickness_mm:g}" if e.thickness_mm else "—", tr(OPERATION_LABELS[e.operation]),
                    symbol]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, e.id)
                tip = f"{e.title()} · {self._params(e)} · {tr(RESULT_LABELS.get(e.result, ''))}"
                if e.demo:
                    tip += "\n" + tr("Entrada de DEMOSTRACIÓN con valores ficticios")
                it.setToolTip(tip)
                self.table.setItem(row, col, it)
            if e.id == current:
                self.table.selectRow(row)
        self._show_detail()

    def current_id(self) -> str | None:
        it = self.table.item(self.table.currentRow(), 0)
        return str(it.data(Qt.ItemDataRole.UserRole)) if it is not None and self.table.selectedItems() else None

    def current(self) -> MaterialEntry | None:
        cid = self.current_id()
        return self.library.get(cid) if cid else None

    def _show_detail(self) -> None:
        e = self.current()
        self.btn_apply.setEnabled(e is not None)
        if e is None:
            self.detail.setText(tr("{n} entradas", n=len(self.library.entries)))
            return
        parts = [f"<b>{e.title()}</b>", self._params(e), tr("Máquina: {m} · módulo: {l}", m=e.machine or "—", l=e.laser_module or "—"),
                 tr("Validación: {d} ({r})", d=e.validated_on or "—", r=tr(RESULT_LABELS.get(e.result, "")))]
        if e.notes:
            parts.append(e.notes.replace("\n", "<br>"))
        if e.demo:
            parts.append("<span style='color:#d4a000'><b>⚠ " + tr("Entrada de DEMOSTRACIÓN con valores ficticios")
                         + "</b></span>")
        self.detail.setText("<br>".join(parts))

    @staticmethod
    def _params(e: MaterialEntry) -> str:
        return tr("{v:g} mm/min · {p:g} % · {n} pasada(s)", v=e.speed_mm_min, p=e.power_max_pct, n=e.passes)

    def _save(self) -> None:
        try:
            self.library.save()
        except OSError as exc:
            QMessageBox.warning(self, tr("Biblioteca de materiales"), str(exc))
        self.changed.emit()

    # ------------------------------------------------------------ acciones
    def _apply(self) -> None:
        e = self.current()
        if e is None:
            return
        if e.demo or not e.validated:
            res = QMessageBox.question(
                self, tr("Aplicar material"),
                tr("«{t}» no está validado{d}. Úselo sólo como punto de partida y haga antes una prueba de material. "
                   "¿Aplicar?", t=e.title(), d=tr(" (valores ficticios de demostración)") if e.demo else ""))
            if res != QMessageBox.StandardButton.Yes:
                return
        self.apply_requested.emit(e)

    def add_entry(self, entry: MaterialEntry, edit: bool = True) -> MaterialEntry | None:
        if edit:
            dlg = MaterialDialog(entry, self.photos_dir, self)
            if not dlg.exec():
                return None
            entry = dlg.entry
        self.library.add(entry)
        self._save()
        self.refresh(select=entry.id)
        return entry

    def _new(self) -> None:
        self.add_entry(MaterialEntry(tr("Nuevo material"), machine=self.machine_name))

    def _edit(self) -> None:
        e = self.current()
        if e is None:
            return
        dlg = MaterialDialog(e, self.photos_dir, self)
        if dlg.exec():
            self.library.update(dlg.entry)
            self._save()
            self.refresh(select=e.id)

    def _duplicate(self) -> None:
        e = self.current()
        if e is not None:
            d = self.library.duplicate(e.id)
            self._save()
            self.refresh(select=d.id if d else None)

    def _delete(self) -> None:
        e = self.current()
        if e is None:
            return
        if QMessageBox.question(self, tr("Eliminar"), tr("¿Eliminar «{t}» de la biblioteca?", t=e.title())) \
                == QMessageBox.StandardButton.Yes:
            self.library.remove(e.id)
            self._save()
            self.refresh()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Importar materiales"), "", tr("Biblioteca (*.json)"))
        if not path:
            return
        try:
            other = MaterialLibrary.load(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("Importar materiales"), str(exc))
            return
        n = self.library.merge(other.entries)
        self._save()
        self.refresh()
        QMessageBox.information(self, tr("Importar materiales"), tr("{n} entradas añadidas", n=n))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar materiales"),
                                              f"materiales_{time.strftime('%Y%m%d')}.json", tr("Biblioteca (*.json)"))
        if path:
            try:
                MaterialLibrary(self.library.entries).save(path)
            except OSError as exc:
                QMessageBox.warning(self, tr("Exportar materiales"), str(exc))
