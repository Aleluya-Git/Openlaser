"""Panel de control del láser: origen del trabajo, análisis, ejecución y overrides."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from openlaser.gcode.analyzer import Analysis, format_duration
from openlaser.i18n import tr
from openlaser.machine import grbl_protocol as gp
from openlaser.machine.events import JobProgressEvent, MachineStatus
from openlaser.machine.states import Action, JobState
from openlaser.ui import icons, theme


PANEL_MIN_WIDTH = 240


class _FitBody(QWidget):
    """Contenido que nunca obliga a desplazarse en horizontal: su ancho mínimo es el del panel."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), PANEL_MIN_WIDTH - 20), hint.height())


class JobPanel(QScrollArea):
    source_changed = Signal()
    load_gcode_requested = Signal()
    preview_requested = Signal()
    simulate_requested = Signal()
    frame_requested = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    override_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        # El contenido se adapta al ancho del panel: sin barra horizontal (sólo vertical si falta altura).
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(PANEL_MIN_WIDTH)
        body = _FitBody()
        self.setWidget(body)
        root = QVBoxLayout(body)
        # sin esto el diseño fija el ancho mínimo del contenido y vuelve a aparecer la barra horizontal
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(6, 6, 6, 6)

        src = QGroupBox(tr("Trabajo"))
        sl = QGridLayout(src)
        self.src_doc = QRadioButton(tr("Documento actual"))
        self.src_file = QRadioButton(tr("Archivo G-code"))
        self.src_doc.setChecked(True)
        self.src_doc.toggled.connect(lambda _c: self.source_changed.emit())
        radios = QHBoxLayout()
        radios.addWidget(self.src_doc)
        radios.addWidget(self.src_file)
        radios.addStretch(1)
        sl.addLayout(radios, 0, 0, 1, 2)
        sl.setColumnStretch(1, 1)
        self.btn_load = QPushButton(icons.icon("open"), tr("Cargar G-code…"))
        self.btn_load.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_load.clicked.connect(lambda: self.load_gcode_requested.emit())
        sl.addWidget(self.btn_load, 1, 0, 1, 2)
        self.file_label = QLabel(tr("Ningún archivo cargado"))
        self.file_label.setProperty("role", "muted")
        self.file_label.setWordWrap(True)
        sl.addWidget(self.file_label, 2, 0, 1, 2)
        sl.addWidget(QLabel(tr("Origen")), 3, 0)
        self.origin = QComboBox()
        self.origin.addItem(tr("Coordenadas absolutas"), "absolute")
        self.origin.addItem(tr("Origen de usuario"), "user")
        # el texto largo no debe fijar el ancho mínimo del panel
        self.origin.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.origin.setMinimumContentsLength(10)
        self.origin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.origin.setToolTip(tr("Absolutas: el diseño se coloca donde está en el lienzo. "
                                  "Usuario: la esquina inferior izquierda del diseño va al origen de trabajo."))
        self.origin.currentIndexChanged.connect(lambda _i: self.source_changed.emit())
        sl.addWidget(self.origin, 3, 1)
        sl.addWidget(QLabel(tr("Copias")), 4, 0)
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        self.copies.setToolTip(tr("Repite el trabajo completo N veces en la misma posición"))
        self.copies.valueChanged.connect(lambda _v: self.source_changed.emit())
        sl.addWidget(self.copies, 4, 1)
        root.addWidget(src)

        info = QGroupBox(tr("Análisis"))
        il = QVBoxLayout(info)
        self.analysis_label = QLabel("—")
        self.analysis_label.setWordWrap(True)
        il.addWidget(self.analysis_label)
        self.btn_simulate = QPushButton(icons.icon("simulate"), tr("Simular…"))
        self.btn_simulate.setToolTip(tr("Reproduce el trabajo paso a paso con potencia, velocidad y diagnósticos"))
        self.btn_simulate.setAccessibleName(tr("Simular"))
        self.btn_simulate.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_simulate.clicked.connect(lambda: self.simulate_requested.emit())
        il.addWidget(self.btn_simulate)
        root.addWidget(info)

        ctrl = QGroupBox(tr("Ejecución"))
        cl = QGridLayout(ctrl)

        def b(icon_name: str, text: str, tip: str, signal: object, role: str | None = None) -> QPushButton:
            # sobre fondo de color (Iniciar, Detener) el icono va en el color del texto del botón
            ic = (icons.icon_on(icon_name, theme.current().accent_text) if role == "accent" else
                  icons.icon_on(icon_name) if role == "danger" else icons.icon(icon_name))
            btn = QPushButton(ic, text)
            btn.setToolTip(tip)
            btn.setAccessibleName(text)
            if role:
                btn.setProperty("role", role)
            btn.clicked.connect(lambda: signal.emit())  # type: ignore[attr-defined]
            return btn

        self.btn_preview = b("preview", tr("Vista previa"), tr("Muestra los recorridos sobre el lienzo"), self.preview_requested)
        self.btn_frame = b("frame", tr("Frame"), tr("Recorre el contorno con el láser apagado"), self.frame_requested)
        self.btn_start = b("start", tr("Iniciar"), tr("Comprobaciones de seguridad y ejecución"), self.start_requested, "accent")
        self.btn_pause = b("pause", tr("Pausa"), tr("Retención de avance"), self.pause_requested)
        self.btn_resume = b("resume", tr("Reanudar"), tr("Continuar tras pausa"), self.resume_requested)
        self.btn_stop = b("stop", tr("Detener"), tr("Detener y vaciar la cola"), self.stop_requested, "danger")
        for i, w in enumerate((self.btn_preview, self.btn_frame, self.btn_start, self.btn_pause,
                               self.btn_resume, self.btn_stop)):
            w.setMinimumWidth(0)
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)  # se reparten el ancho disponible
            cl.addWidget(w, i // 2, i % 2)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat("%p %")
        self.progress.setAccessibleName(tr("Progreso del trabajo"))
        cl.addWidget(self.progress, 3, 0, 1, 2)
        self.times = QLabel(tr("Transcurrido {e} · Restante {r}", e="--:--", r="--:--"))
        self.times.setWordWrap(True)
        cl.addWidget(self.times, 4, 0, 1, 2)
        self.current_line = QLabel("")
        self.current_line.setProperty("role", "coord")
        self.current_line.setWordWrap(True)
        cl.addWidget(self.current_line, 5, 0, 1, 2)
        root.addWidget(ctrl)

        ov = QGroupBox(tr("Overrides en tiempo real"))
        ol = QGridLayout(ov)
        self.ov_labels: dict[str, QLabel] = {}

        def ov_row(row: int, name: str, buttons: list[tuple[str, bytes, str]]) -> None:
            ol.addWidget(QLabel(name), row, 0)
            lab = QLabel("100 %")
            lab.setProperty("role", "coord")
            self.ov_labels[name] = lab
            ol.addWidget(lab, row, 1)
            hb = QHBoxLayout()
            hb.setSpacing(3)
            for text, code, tip in buttons:
                btn = QPushButton(text)
                btn.setMinimumWidth(0)
                btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                btn.setToolTip(tip)
                btn.setAccessibleName(f"{name} {text}")
                btn.clicked.connect(lambda _c=False, c=code: self.override_requested.emit(c))
                hb.addWidget(btn)
                self._ov_buttons.append(btn)
            ol.addLayout(hb, row, 2)

        self._ov_buttons: list[QPushButton] = []
        ol.setColumnStretch(2, 1)
        ov_row(0, tr("Avance"), [("−10", gp.RT_FEED_OV_MINUS10, tr("Avance −10 %")),
                                 ("100", gp.RT_FEED_OV_RESET, tr("Avance 100 %")),
                                 ("+10", gp.RT_FEED_OV_PLUS10, tr("Avance +10 %"))])
        ov_row(1, tr("Rápido"), [("25", gp.RT_RAPID_OV_25, tr("Rápidos al 25 %")),
                                 ("50", gp.RT_RAPID_OV_50, tr("Rápidos al 50 %")),
                                 ("100", gp.RT_RAPID_OV_100, tr("Rápidos al 100 %"))])
        ov_row(2, tr("Potencia"), [("−10", gp.RT_SPINDLE_OV_MINUS10, tr("Potencia −10 %")),
                                   ("100", gp.RT_SPINDLE_OV_RESET, tr("Potencia 100 %")),
                                   ("+10", gp.RT_SPINDLE_OV_PLUS10, tr("Potencia +10 %"))])
        root.addWidget(ov)
        root.addStretch(1)

    # --- API -----------------------------------------------------------------------
    def source(self) -> str:
        return "file" if self.src_file.isChecked() else "document"

    def origin_mode(self) -> str:
        return str(self.origin.currentData())

    def set_file(self, name: str | None) -> None:
        self.file_label.setText(name or tr("Ningún archivo cargado"))
        if name:
            self.src_file.setChecked(True)

    def show_pending(self) -> None:
        self.analysis_label.setText(tr("Calculando el trabajo…"))

    def show_analysis(self, a: Analysis | None, skipped: list[str] | None = None, bounds_msg: str = "",
                      warnings: list[str] | None = None) -> None:
        if a is None:
            self.analysis_label.setText(tr("Sin G-code"))
            return
        parts = [tr("{n} líneas · tiempo estimado {t}", n=a.line_count, t=format_duration(a.estimated_seconds)),
                 tr("Corte {c:.0f} mm · desplazamiento {d:.0f} mm", c=a.cut_distance, d=a.travel_distance)]
        if a.bounds_laser:
            x0, y0, x1, y1 = a.bounds_laser
            parts.append(tr("Zona de láser X {x0:.1f}…{x1:.1f}  Y {y0:.1f}…{y1:.1f} mm", x0=x0, x1=x1, y0=y0, y1=y1))
        parts.append(tr("S máximo {s:g}", s=a.max_s))
        if skipped:
            parts.append("⚠ " + tr("Capas sin geometría que generar: {l}", l=", ".join(skipped)))
        for w in (warnings or [])[:4] + a.warnings[:4]:
            parts.append("⚠ " + w)
        if bounds_msg:
            parts.append("⛔ " + bounds_msg)
        self.analysis_label.setText("\n".join(parts))

    def update_status(self, st: MachineStatus, allowed: set[Action]) -> None:
        self.btn_frame.setEnabled(Action.FRAME in allowed)
        self.btn_start.setEnabled(Action.START in allowed)
        self.btn_pause.setEnabled(Action.PAUSE in allowed)
        self.btn_resume.setEnabled(Action.RESUME in allowed and st.substate in (0, None))
        self.btn_stop.setEnabled(Action.STOP in allowed)
        for b in self._ov_buttons:
            b.setEnabled(Action.OVERRIDE in allowed)
        f, r, s = st.overrides
        self.ov_labels[tr("Avance")].setText(f"{f} %")
        self.ov_labels[tr("Rápido")].setText(f"{r} %")
        self.ov_labels[tr("Potencia")].setText(f"{s} %")
        busy = st.job in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING)
        for w in (self.src_doc, self.src_file, self.btn_load, self.origin, self.copies):
            w.setEnabled(not busy)

    def update_progress(self, ev: JobProgressEvent) -> None:
        frac = ev.acked / ev.total if ev.total else 0.0
        self.progress.setValue(int(frac * 1000))
        self.times.setText(tr("Transcurrido {e} · Restante {r}", e=format_duration(ev.elapsed),
                              r=format_duration(ev.remaining)))
        self.current_line.setText(tr("Línea {a}/{t}: {l}", a=ev.acked, t=ev.total, l=ev.current_line))
