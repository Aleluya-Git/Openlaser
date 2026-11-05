"""Panel de simulación: reproducción del trabajo, escalas de potencia/velocidad y diagnósticos."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider,
    QSplitter, QVBoxLayout, QWidget,
)

from openlaser.canvas.sim_item import SimulationItem
from openlaser.gcode.analyzer import format_duration
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.simulation.checks import Issue
from openlaser.simulation.timeline import Timeline, build_timeline
from openlaser.ui import icons

SPEEDS = [1, 2, 5, 10, 25, 50, 100, 250, 1000]
_SEV = {"error": "⛔", "warn": "⚠", "info": "ℹ"}


class SimulationPanel(QWidget):
    select_shapes = Signal(list)
    rebuild_requested = Signal()
    closed = Signal()

    def __init__(self, view, parent: QWidget | None = None) -> None:  # noqa: ANN001 - CanvasView
        super().__init__(parent)
        self.view = view
        self.tl: Timeline | None = None
        self.lines: list[str] | None = []
        self._text_src = None
        self.item: SimulationItem | None = None
        self.layer_colors: list[str] = []
        self.sim_time = 0.0
        self._last_tick = 0.0
        self._doc_xy: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._map: tuple[float, float, float, float] = (1.0, 0.0, 1.0, 0.0)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        split.addWidget(left)

        bar = QHBoxLayout()

        def btn(icon_name: str | None, text: str, tip: str) -> QPushButton:
            b = QPushButton(icons.icon(icon_name), text) if icon_name else QPushButton(text)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            bar.addWidget(b)
            return b

        self.btn_rebuild = btn("simulate", tr("Generar"), tr("Genera de nuevo el trabajo y reinicia la simulación"))
        self.btn_start = btn(None, "⏮", tr("Ir al inicio"))
        self.btn_back = btn(None, "◀", tr("Paso atrás (un movimiento)"))
        self.btn_play = btn("start", tr("Reproducir"), tr("Reproducir / pausar la simulación"))
        self.btn_fwd = btn(None, "▶", tr("Paso adelante (un movimiento)"))
        self.btn_end = btn(None, "⏭", tr("Ir al final"))
        bar.addWidget(QLabel(tr("Velocidad")))
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s}×", s)
        self.speed.setCurrentIndex(SPEEDS.index(25))
        self.speed.setToolTip(tr("Velocidad de la simulación respecto al tiempo real estimado"))
        bar.addWidget(self.speed)
        bar.addStretch(1)
        lay.addLayout(bar)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setAccessibleName(tr("Posición de la simulación"))
        row.addWidget(self.slider, 1)
        self.time_label = QLabel("--:-- / --:--")
        self.time_label.setProperty("role", "coord")
        row.addWidget(self.time_label)
        lay.addLayout(row)

        opts = QGridLayout()
        opts.addWidget(QLabel(tr("Capa")), 0, 0)
        self.layer_box = QComboBox()
        self.layer_box.setToolTip(tr("Vista por capa"))
        opts.addWidget(self.layer_box, 0, 1)
        opts.addWidget(QLabel(tr("Color por")), 0, 2)
        self.color_box = QComboBox()
        self.color_box.addItem(tr("Potencia"), "power")
        self.color_box.addItem(tr("Velocidad"), "speed")
        self.color_box.addItem(tr("Capa"), "layer")
        opts.addWidget(self.color_box, 0, 3)
        self.chk_travel = QCheckBox(tr("Desplazamientos"))
        self.chk_travel.setChecked(True)
        self.chk_dir = QCheckBox(tr("Dirección"))
        self.chk_dir.setChecked(True)
        opts.addWidget(self.chk_travel, 0, 4)
        opts.addWidget(self.chk_dir, 0, 5)
        self.legend = QLabel(tr("bajo → alto"))
        self.legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.legend.setStyleSheet("QLabel { color: #000; border-radius: 4px; padding: 1px 8px; background: "
                                  "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgb(40,90,255), stop:0.33 rgb(0,200,210), "
                                  "stop:0.66 rgb(255,200,0), stop:1 rgb(230,30,40)); }")
        opts.addWidget(self.legend, 0, 6)
        lay.addLayout(opts)
        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        lay.addWidget(self.stats)
        self.current = QLabel("")
        self.current.setProperty("role", "coord")
        self.current.setWordWrap(True)
        lay.addWidget(self.current)
        lay.addStretch(1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel(tr("Diagnóstico (doble clic: seleccionar/ir)")))
        self.issues = QListWidget()
        self.issues.setAccessibleName(tr("Diagnóstico"))
        rl.addWidget(self.issues)
        split.addWidget(right)
        split.setSizes([700, 380])

        self.btn_rebuild.clicked.connect(lambda: self.rebuild_requested.emit())
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_start.clicked.connect(lambda: self.seek(0.0))
        self.btn_end.clicked.connect(lambda: self.seek(self.tl.total_time if self.tl else 0.0))
        self.btn_back.clicked.connect(lambda: self.step(-1))
        self.btn_fwd.clicked.connect(lambda: self.step(1))
        self.slider.sliderMoved.connect(self._slider_moved)
        self.layer_box.currentIndexChanged.connect(lambda _i: self._rebuild_item())
        self.color_box.currentIndexChanged.connect(lambda _i: self._rebuild_item())
        self.chk_travel.toggled.connect(self._options_changed)
        self.chk_dir.toggled.connect(self._options_changed)
        self.issues.itemDoubleClicked.connect(self._issue_clicked)
        self._set_enabled(False)

    # ------------------------------------------------------------------ datos
    def _set_enabled(self, on: bool) -> None:
        for w in (self.btn_start, self.btn_back, self.btn_play, self.btn_fwd, self.btn_end, self.slider,
                  self.layer_box, self.color_box):
            w.setEnabled(on)

    def load(self, lines: list, cs: CoordinateSystem, wco: tuple[float, float], layer_colors: dict[str, str],
             rapid_mm_min: float, doc_issues: list[Issue], move_issues_fn,  # noqa: ANN001
             text_lines=None) -> None:  # noqa: ANN001
        """Construye la línea temporal y muestra el recorrido completo.

        ``lines`` puede contener bloques ráster (imágenes); ``text_lines`` devuelve entonces el texto G-code
        (sólo se pide si hay que mostrar una línea concreta).
        """
        self.stop()
        self.lines = lines if all(isinstance(ln, str) for ln in lines) else None
        self._text_src = text_lines
        self.tl = build_timeline(lines, rapid_mm_min)
        tl = self.tl
        # trabajo → documento: transformación separable (escala ±1 y desplazamiento por eje)
        o = cs.work_to_doc((0.0, 0.0), wco)
        ex = cs.work_to_doc((1.0, 0.0), wco)
        ey = cs.work_to_doc((0.0, 1.0), wco)
        ax, bx = ex[0] - o[0], o[0]
        ay, by = ey[1] - o[1], o[1]
        self._map = (ax, bx, ay, by)
        self._doc_xy = (tl.x0 * ax + bx, tl.y0 * ay + by, tl.x1 * ax + bx, tl.y1 * ay + by)
        self.layer_colors = [layer_colors.get(name, "") for name in tl.layers]
        self.layer_box.blockSignals(True)
        self.layer_box.clear()
        self.layer_box.addItem(tr("Todas las capas"), -1)
        for i, name in enumerate(tl.layers):
            self.layer_box.addItem(name, i)
        self.layer_box.blockSignals(False)
        if self.item is None:
            self.item = SimulationItem()
        self.view.show_simulation(self.item)
        self._rebuild_item()
        cut_feeds = tl.feed[tl.on]
        self.stats.setText(tr("Tiempo {t} · corte {c:.1f} m · desplazamiento {d:.1f} m · {n} cambios de capa · "
                              "S máx. {s:g} · avance máx. {f:.0f} mm/min",
                              t=format_duration(tl.total_time), c=tl.cut_distance / 1000,
                              d=tl.travel_distance / 1000, n=tl.layer_changes, s=tl.s_max,
                              f=float(cut_feeds.max()) if cut_feeds.size else 0.0))
        self.issues.clear()
        all_issues = list(move_issues_fn(tl)) + list(doc_issues)
        if not all_issues:
            it = QListWidgetItem("✔ " + tr("Sin problemas detectados"))
            self.issues.addItem(it)
        for issue in all_issues:
            it = QListWidgetItem(f"{_SEV.get(issue.severity, '•')} {issue.message}")
            it.setData(Qt.ItemDataRole.UserRole, (issue.shape_ids, issue.line))
            it.setToolTip(issue.message)
            self.issues.addItem(it)
        self._set_enabled(tl.count > 0)
        self.seek(tl.total_time)  # muestra el recorrido completo; «⏮» y «Reproducir» lo animan

    def _rebuild_item(self) -> None:
        tl, item = self.tl, self.item
        if tl is None or item is None or self._doc_xy is None:
            return
        mode = self.color_box.currentData()
        if mode == "power":
            value = tl.s / tl.s_max if tl.s_max > 0 else np.zeros(tl.count)
        elif mode == "speed":
            fmax = float(tl.feed[tl.on].max()) if tl.on.any() else 1.0
            value = np.clip(tl.feed / max(fmax, 1e-6), 0, 1)
        else:
            value = tl.layer.astype(np.float64)
        self.legend.setVisible(mode != "layer")
        self.legend.setText(tr("lento → rápido") if mode == "speed" else tr("bajo → alto"))
        k = self.layer_box.currentData()
        visible = np.ones(tl.count, bool) if k is None or k < 0 else (tl.layer == k)
        # Las filas de las imágenes no se dibujan como líneas (saturarían el lienzo): van como imagen quemada.
        lines_visible = visible if tl.raster is None else visible & ~tl.raster
        item.set_data(*self._doc_xy, tl.on, value, str(mode), self.layer_colors, lines_visible)
        item.set_overlays(self._overlay_images(str(mode), k))
        item.show_travel = self.chk_travel.isChecked()
        item.show_direction = self.chk_dir.isChecked()
        self._show_time()

    def _overlay_images(self, mode: str, layer_filter: object) -> list:
        """Imagen quemada de cada bloque ráster, coloreada como las líneas (potencia, velocidad o capa)."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage

        from openlaser.canvas.sim_item import BUCKETS, LAYER_FALLBACK, ramp
        tl = self.tl
        if tl is None or not tl.overlays:
            return []
        ax, bx, ay, by = self._map
        fmax = float(tl.feed[tl.on].max()) if tl.on.any() else 1.0
        out = []
        for ov in tl.overlays:
            if isinstance(layer_filter, int) and layer_filter >= 0 and ov.layer != layer_filter:
                continue
            q = ov.q
            rows, cols = q.shape
            mask = q > 0
            rgba = np.zeros((rows, cols, 4), np.uint8)
            if mode == "power":
                keys = np.minimum((q / max(tl.s_max, 1e-9) * BUCKETS).astype(np.int64), BUCKETS - 1)
                cols_ = [ramp((i + 0.5) / BUCKETS) for i in range(BUCKETS)]
                lut = np.array([(c_.red(), c_.green(), c_.blue()) for c_ in cols_], np.uint8)
                rgba[mask, :3] = lut[keys[mask]]
            else:
                if mode == "speed":
                    c = ramp(ov.feed / max(fmax, 1e-6))
                else:
                    name = self.layer_colors[ov.layer] if 0 <= ov.layer < len(self.layer_colors) else ""
                    c = QColor(name or LAYER_FALLBACK[max(ov.layer, 0) % 6])
                rgba[mask, :3] = (c.red(), c.green(), c.blue())
            rgba[mask, 3] = 255
            dxs, dys = ax * ov.x_step, ay * ov.y_step
            left, top0 = ax * ov.x_edge + bx, ay * ov.y_edge + by
            if dxs < 0:
                rgba, left, dxs = rgba[:, ::-1], left + dxs * cols, -dxs
            top = top0 + dys * rows if dys < 0 else top0
            if dys < 0:
                rgba = rgba[::-1]
            data = np.ascontiguousarray(rgba)
            img = QImage(data.data, cols, rows, cols * 4, QImage.Format.Format_RGBA8888).copy()
            out.append((img, QRectF(left, top, dxs * cols, abs(dys) * rows), ov.row_move, top0, dys))
        return out

    def _options_changed(self) -> None:
        if self.item is not None:
            self.item.show_travel = self.chk_travel.isChecked()
            self.item.show_direction = self.chk_dir.isChecked()
            self.item.update()

    # ------------------------------------------------------------- reproducción
    def toggle_play(self) -> None:
        if self.timer.isActive():
            self.stop()
            return
        if self.tl is None or self.tl.count == 0:
            return
        if self.sim_time >= self.tl.total_time:
            self.sim_time = 0.0
        self._last_tick = time.monotonic()
        self.timer.start()
        self.btn_play.setText(tr("Pausar"))

    def stop(self) -> None:
        self.timer.stop()
        self.btn_play.setText(tr("Reproducir"))

    def _tick(self) -> None:
        if self.tl is None:
            return
        now = time.monotonic()
        self.sim_time += (now - self._last_tick) * float(self.speed.currentData())
        self._last_tick = now
        if self.sim_time >= self.tl.total_time:
            self.sim_time = self.tl.total_time
            self.stop()
        self._show_time()

    def seek(self, t: float) -> None:
        if self.tl is None:
            return
        self.sim_time = min(max(t, 0.0), self.tl.total_time)
        self._show_time()

    def step(self, delta: int) -> None:
        if self.tl is None or self.tl.count == 0:
            return
        self.stop()
        i = self.tl.index_at(self.sim_time)
        i = max(0, min(self.tl.count, i + delta))
        self.sim_time = float(self.tl.t1[i - 1]) if i > 0 else 0.0
        self._show_time()

    def _slider_moved(self, v: int) -> None:
        if self.tl is not None:
            self.seek(self.tl.total_time * v / 10000.0)

    def _show_time(self) -> None:
        tl, item = self.tl, self.item
        if tl is None or item is None:
            return
        x, y, i = tl.position_at(self.sim_time)
        ax, bx, ay, by = self._map
        head = (x * ax + bx, y * ay + by)
        item.set_progress(i, head if i < tl.count else None)
        total = tl.total_time
        self.time_label.setText(f"{format_duration(self.sim_time)} / {format_duration(total)}")
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(10000 * self.sim_time / total) if total > 0 else 0)
            self.slider.blockSignals(False)
        if i < tl.count:
            ln = int(tl.line[i])
            layer = tl.layers[int(tl.layer[i])] if 0 <= int(tl.layer[i]) < len(tl.layers) else "—"
            state = tr("láser S{s:g}", s=float(tl.s[i])) if tl.on[i] else tr("sin láser")
            self.current.setText(tr("Línea {n}: {t}  ·  capa {c}  ·  {e}  ·  F {f:.0f}", n=ln + 1,
                                    t=self._line_text(ln), c=layer, e=state, f=float(tl.feed[i])))
        else:
            self.current.setText(tr("Fin del trabajo"))

    def _line_text(self, ln: int) -> str:
        if self.lines is None and self._text_src is not None:
            self.lines = self._text_src()  # texto de las imágenes: se crea una sola vez
        lines = self.lines or []
        return lines[ln].strip()[:60] if 0 <= ln < len(lines) else ""

    def _issue_clicked(self, it: QListWidgetItem) -> None:
        data = it.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        ids, line = data
        if ids:
            self.select_shapes.emit(list(ids))
        if line is not None and self.tl is not None:
            hits = np.flatnonzero(self.tl.line >= line)
            if hits.size:
                self.seek(float(self.tl.t1[hits[0]]))

    def clear(self) -> None:
        self.stop()
        self.view.clear_simulation()
        self.tl = None
        self._set_enabled(False)

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self.stop()
        super().hideEvent(event)
