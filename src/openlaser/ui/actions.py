"""Registro central de acciones (menús, barras de iconos y atajos).

Cada acción declara texto, icono, atajo, ayuda y la fase en la que se
implementa. Las acciones de fases posteriores aparecen deshabilitadas con un
tooltip que lo indica, de modo que la estructura de menús es la definitiva
desde la Fase 1 sin prometer funciones que todavía no existen.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

from openlaser.i18n import N_, tr
from openlaser.ui import icons


@dataclass(frozen=True)
class ActionSpec:
    id: str
    text: str
    icon: str | None = None
    shortcut: str | QKeySequence.StandardKey | None = None
    tip: str = ""
    phase: int = 1
    checkable: bool = False


SK = QKeySequence.StandardKey
CURRENT_PHASE = 6

SPECS: list[ActionSpec] = [
    # Archivo
    ActionSpec("file.new", N_("Nuevo proyecto"), "new", SK.New, N_("Crea un documento vacío")),
    ActionSpec("file.open", N_("Abrir proyecto…"), "open", SK.Open, N_("Abre un proyecto nativo")),
    ActionSpec("file.recent", N_("Abrir reciente"), None, None, ""),
    ActionSpec("file.save", N_("Guardar"), "save", SK.Save, N_("Guarda el proyecto")),
    ActionSpec("file.save_as", N_("Guardar como…"), None, SK.SaveAs, ""),
    ActionSpec("file.autosave", N_("Guardado automático"), None, None, "", checkable=True),
    ActionSpec("file.recover", N_("Recuperar proyecto…"), None, None, ""),
    ActionSpec("file.import", N_("Importar…"), "import", "Ctrl+I", N_("Importa SVG, DXF o imágenes")),
    ActionSpec("file.export", N_("Exportar…"), "export", "Ctrl+E", N_("Exporta el diseño")),
    ActionSpec("file.open_gcode", N_("Abrir G-code…"), "open", "Ctrl+Shift+O", N_("Carga un archivo G-code para ejecutarlo")),
    ActionSpec("file.save_gcode", N_("Guardar G-code…"), "export", "Ctrl+Shift+E", N_("Guarda el G-code del trabajo actual")),
    ActionSpec("file.send_gcode", N_("Enviar a la tarjeta SD…"), None, None,
               N_("Guarda el trabajo en la SD de un controlador FluidNC o ESP3D sin ponerlo en marcha"), phase=4),
    ActionSpec("file.import_settings", N_("Importar configuración…"), None, None, N_("Carga preferencias y perfiles desde JSON")),
    ActionSpec("file.export_settings", N_("Exportar configuración…"), None, None, N_("Guarda preferencias y perfiles en JSON")),
    ActionSpec("file.backup", N_("Copia de seguridad…"), None, None, N_("Copia de seguridad de la configuración")),
    ActionSpec("file.restore", N_("Restaurar copia…"), None, None, N_("Restaura una copia de seguridad")),
    ActionSpec("file.doc_props", N_("Propiedades del documento…"), None, None, ""),
    ActionSpec("file.page", N_("Configuración del área de trabajo…"), None, None, ""),
    ActionSpec("file.print_summary", N_("Imprimir resumen del trabajo…"), None, None, ""),
    ActionSpec("file.quit", N_("Salir"), None, SK.Quit, ""),
    # Editar
    ActionSpec("edit.undo", N_("Deshacer"), "undo", SK.Undo, N_("Deshace el último cambio")),
    ActionSpec("edit.redo", N_("Rehacer"), "redo", SK.Redo, N_("Rehace el cambio deshecho")),
    ActionSpec("edit.cut", N_("Cortar"), "cut", SK.Cut, ""),
    ActionSpec("edit.copy", N_("Copiar"), "copy", SK.Copy, ""),
    ActionSpec("edit.paste", N_("Pegar"), "paste", SK.Paste, ""),
    ActionSpec("edit.paste_in_place", N_("Pegar en posición"), None, "Ctrl+Shift+V", ""),
    ActionSpec("edit.duplicate", N_("Duplicar"), None, "Ctrl+D", ""),
    ActionSpec("edit.delete", N_("Eliminar"), "delete", SK.Delete, ""),
    ActionSpec("edit.select_all", N_("Seleccionar todo"), None, SK.SelectAll, ""),
    ActionSpec("edit.deselect", N_("Deseleccionar"), None, "Ctrl+Shift+A", ""),
    ActionSpec("edit.invert_selection", N_("Invertir selección"), None, "Ctrl+Shift+I", ""),
    ActionSpec("edit.select_layer", N_("Seleccionar por capa"), None, None, ""),
    ActionSpec("edit.select_type", N_("Seleccionar por tipo"), None, None, ""),
    ActionSpec("edit.lock", N_("Bloquear / desbloquear"), None, None, ""),
    ActionSpec("edit.toggle_visible", N_("Mostrar / ocultar objetos"), None, None, ""),
    ActionSpec("edit.preferences", N_("Preferencias…"), "settings", SK.Preferences, N_("Idioma, tema, unidades, seguridad y atajos")),
    ActionSpec("edit.devices", N_("Configuración de dispositivo…"), "device", None, N_("Perfiles de máquina y conexión")),
    ActionSpec("edit.machine", N_("Configuración de máquina (GRBL $$)…"), None, None, N_("Lee y edita parámetros GRBL")),
    ActionSpec("edit.plugins", N_("Gestión de plugins…"), "plug", None, N_("Adaptadores de controlador disponibles")),
    # Dibujar (Fase 2)
    ActionSpec("draw.select", N_("Seleccionar"), "select", "V", N_("Herramienta de selección"), checkable=True),
    ActionSpec("draw.nodes", N_("Editar nodos"), "nodes", "N", "", checkable=True),
    ActionSpec("draw.line", N_("Línea"), "line", "L", "", checkable=True),
    ActionSpec("draw.rect", N_("Rectángulo"), "rect", "R", "", checkable=True),
    ActionSpec("draw.ellipse", N_("Elipse"), "ellipse", "E", "", checkable=True),
    ActionSpec("draw.polygon", N_("Polígono / estrella"), "polygon", "P", "", checkable=True),
    ActionSpec("draw.text", N_("Texto"), "text", "T", "", checkable=True),
    ActionSpec("draw.image", N_("Insertar imagen…"), "image", None, "", phase=3),
    ActionSpec("draw.vectorize", N_("Vectorizar imagen…"), "vectorize", None, "", phase=3),
    ActionSpec("draw.measure", N_("Medir"), "measure", "M", "", checkable=True),
    ActionSpec("draw.text_on_path", N_("Texto sobre trayectoria…"), None, None, "", phase=4),
    ActionSpec("image.settings", N_("Ajustes de imagen…"), "image", "Ctrl+Alt+I",
               N_("Brillo, contraste, tramado y resolución de la imagen seleccionada")),
    # Organizar (Fase 2)
    ActionSpec("arrange.group", N_("Agrupar"), "group", "Ctrl+G", ""),
    ActionSpec("arrange.ungroup", N_("Desagrupar"), "ungroup", "Ctrl+Shift+G", ""),
    ActionSpec("arrange.align", N_("Alinear"), "align", None, N_("Alinear la selección")),
    ActionSpec("arrange.align_left", N_("Alinear a la izquierda"), None, None, ""),
    ActionSpec("arrange.align_right", N_("Alinear a la derecha"), None, None, ""),
    ActionSpec("arrange.align_top", N_("Alinear arriba"), None, None, ""),
    ActionSpec("arrange.align_bottom", N_("Alinear abajo"), None, None, ""),
    ActionSpec("arrange.align_hcenter", N_("Centrar horizontalmente"), None, None, ""),
    ActionSpec("arrange.align_vcenter", N_("Centrar verticalmente"), None, None, ""),
    ActionSpec("arrange.align_center", N_("Centrar"), None, None, ""),
    ActionSpec("arrange.distribute", N_("Distribuir"), "distribute", None, N_("Distribuir la selección")),
    ActionSpec("arrange.distribute_h", N_("Distribuir horizontalmente"), None, None, ""),
    ActionSpec("arrange.distribute_v", N_("Distribuir verticalmente"), None, None, ""),
    ActionSpec("arrange.same_width", N_("Igualar anchura"), None, None, ""),
    ActionSpec("arrange.same_height", N_("Igualar altura"), None, None, ""),
    ActionSpec("arrange.front", N_("Traer al frente"), None, "Ctrl+Shift+Up", ""),
    ActionSpec("arrange.back", N_("Enviar al fondo"), None, "Ctrl+Shift+Down", ""),
    ActionSpec("arrange.raise", N_("Subir un nivel"), None, "Ctrl+Up", ""),
    ActionSpec("arrange.lower", N_("Bajar un nivel"), None, "Ctrl+Down", ""),
    ActionSpec("arrange.union", N_("Unir"), "union", None, N_("Unión booleana de la selección")),
    ActionSpec("arrange.subtract", N_("Restar"), "subtract", None, N_("Resta de la forma inferior menos las superiores")),
    ActionSpec("arrange.intersect", N_("Intersección"), "intersect", None, ""),
    ActionSpec("arrange.xor", N_("Exclusión (XOR)"), None, None, ""),
    ActionSpec("arrange.combine", N_("Combinar trayectorias"), None, "Ctrl+L", ""),
    ActionSpec("arrange.break_apart", N_("Separar trayectorias"), None, "Ctrl+Shift+L", ""),
    ActionSpec("arrange.offset", N_("Offset…"), "offset", None, N_("Offset interior o exterior")),
    ActionSpec("arrange.kerf", N_("Compensación de kerf…"), None, None, N_("Offset de la mitad del kerf de la capa")),
    ActionSpec("arrange.array", N_("Matriz rectangular…"), "array", None, ""),
    ActionSpec("arrange.array_circular", N_("Matriz circular…"), None, None, ""),
    ActionSpec("arrange.copies_path", N_("Copias sobre trayectoria…"), None, None, ""),
    ActionSpec("arrange.mirror", N_("Voltear horizontalmente"), "mirror", "H", ""),
    ActionSpec("arrange.flip_v", N_("Voltear verticalmente"), None, "Shift+H", ""),
    ActionSpec("arrange.rotate", N_("Rotar…"), "rotate", None, ""),
    ActionSpec("arrange.rotate_cw", N_("Girar 90° horario"), None, "Ctrl+]", ""),
    ActionSpec("arrange.rotate_ccw", N_("Girar 90° antihorario"), None, "Ctrl+[", ""),
    ActionSpec("arrange.snap_grid", N_("Ajustar a cuadrícula"), None, None, "", checkable=True),
    ActionSpec("arrange.snap_nodes", N_("Ajustar a nodos y objetos"), None, None, "", checkable=True),
    ActionSpec("arrange.snap_centers", N_("Ajustar a centros"), None, None, "", checkable=True),
    ActionSpec("arrange.move_to_page", N_("Centrar en el área de trabajo"), None, None, ""),
    ActionSpec("arrange.move_to_origin", N_("Mover al origen de máquina"), None, None,
               N_("Coloca la esquina del diseño en el origen de máquina")),
    ActionSpec("arrange.move_to_laser", N_("Mover selección a la posición del láser"), None, None, ""),
    ActionSpec("arrange.center_on_laser", N_("Centrar selección en la posición del láser"), None, None, ""),
    ActionSpec("arrange.laser_to_selection", N_("Mover el láser a la selección"), None, None,
               N_("Desplaza el cabezal (G0, láser apagado) al centro de la selección")),
    ActionSpec("path.close", N_("Cerrar trayectorias"), None, None, ""),
    ActionSpec("path.join", N_("Unir nodos y reparar"), None, None, N_("Une tramos abiertos contiguos y cierra los que se tocan")),
    ActionSpec("path.simplify", N_("Simplificar trayectoria"), None, None, ""),
    ActionSpec("path.dedupe", N_("Eliminar nodos duplicados"), None, None, ""),
    ActionSpec("path.reverse", N_("Invertir sentido"), None, None, ""),
    ActionSpec("path.delete_duplicates", N_("Eliminar geometría duplicada"), None, None, ""),
    ActionSpec("path.round_corners", N_("Redondear esquinas…"), "round", None,
               N_("Sustituye las esquinas rectas por arcos")),
    ActionSpec("path.tabs", N_("Puentes / tabs…"), "tabs", None,
               N_("Deja puentes sin cortar para que las piezas no se suelten")),
    ActionSpec("layers.add", N_("Nueva capa"), "new", None, ""),
    ActionSpec("layers.delete", N_("Eliminar capa"), "delete", None, ""),
    ActionSpec("layers.settings", N_("Ajustes de capa…"), "settings", None, ""),
    ActionSpec("layers.copy_settings", N_("Copiar ajustes a otras capas…"), None, None, ""),
    ActionSpec("layers.save_preset", N_("Guardar preajuste…"), None, None, ""),
    ActionSpec("layers.apply_preset", N_("Aplicar preajuste…"), None, None, ""),
    ActionSpec("layers.assign", N_("Asignar selección a la capa actual"), None, None, ""),
    # Vista
    ActionSpec("view.zoom_in", N_("Acercar"), "zoom", SK.ZoomIn, ""),
    ActionSpec("view.zoom_out", N_("Alejar"), None, SK.ZoomOut, ""),
    ActionSpec("view.fit", N_("Ajustar a pantalla"), "fit", "Ctrl+0", N_("Muestra toda el área de trabajo")),
    ActionSpec("view.fit_selection", N_("Ajustar a la selección"), "fit_selection", "Ctrl+2", ""),
    ActionSpec("view.center", N_("Centrar en el láser"), "center", None, N_("Centra la vista en la posición del cabezal")),
    ActionSpec("view.fullscreen", N_("Pantalla completa"), None, SK.FullScreen, "", checkable=True),
    ActionSpec("view.reset_layout", N_("Restablecer distribución"), None, None, N_("Restaura paneles y barras por defecto")),
    # Láser
    ActionSpec("laser.preview", N_("Vista previa"), "preview", "Alt+P", N_("Muestra recorridos de corte y desplazamiento")),
    ActionSpec("laser.frame", N_("Frame rectangular"), "frame", "Ctrl+F", N_("Recorre el rectángulo envolvente con el láser apagado")),
    ActionSpec("laser.frame_contour", N_("Frame por contorno"), None, "Ctrl+Shift+F", N_("Recorre la envolvente convexa con el láser apagado")),
    ActionSpec("laser.start", N_("Iniciar trabajo"), "start", "Ctrl+R", N_("Comprobaciones previas y ejecución")),
    ActionSpec("laser.pause", N_("Pausar"), "pause", "Ctrl+P", N_("Retención de avance (feed hold)")),
    ActionSpec("laser.resume", N_("Reanudar"), "resume", "Ctrl+Shift+R", N_("Reanuda tras una pausa")),
    ActionSpec("laser.stop", N_("Detener"), "stop", "Esc", N_("Detiene el trabajo y vacía la cola")),
    ActionSpec("laser.optimize", N_("Optimizar trayectorias"), "optimize", None,
               N_("Activa el orden interior→exterior y la optimización de desplazamientos e informa del ahorro")),
    ActionSpec("laser.simulate", N_("Simulación…"), "simulate", "F6",
               N_("Reproduce el trabajo paso a paso con potencia, velocidad y diagnósticos")),
    ActionSpec("laser.material_test", N_("Prueba de materiales…"), "material", None, "", phase=4),
    ActionSpec("laser.focus_test", N_("Prueba de enfoque…"), None, None, "", phase=4),
    ActionSpec("laser.interval_test", N_("Prueba de intervalo…"), None, None, "", phase=4),
    ActionSpec("laser.print_cut", N_("Print and Cut…"), None, None, "", phase=4),
    ActionSpec("laser.center_finder", N_("Center Finder…"), None, None, "", phase=4),
    ActionSpec("laser.rotary", N_("Configuración rotativa…"), "rotary", None, "", phase=4),
    ActionSpec("laser.cylinder", N_("Corrección cilíndrica…"), None, None, "", phase=4),
    # Máquina
    ActionSpec("machine.connect", N_("Conectar"), "connect", "Ctrl+K", N_("Conecta con el dispositivo activo")),
    ActionSpec("machine.disconnect", N_("Desconectar"), "disconnect", "Ctrl+Shift+K", N_("Cierra la conexión (envía reinicio si hay actividad)")),
    ActionSpec("machine.home", N_("Home"), "home", "Ctrl+H", N_("Ciclo de homing ($H)")),
    ActionSpec("machine.unlock", N_("Desbloquear alarma…"), "unlock", None, N_("Envía $X tras confirmación")),
    ActionSpec("machine.set_origin", N_("Establecer origen"), "origin_set", None, N_("La posición actual pasa a ser X0 Y0 de trabajo")),
    ActionSpec("machine.clear_origin", N_("Borrar origen"), "origin_clear", None, N_("El origen de trabajo vuelve al de máquina")),
    ActionSpec("machine.go_origin", N_("Ir al origen de trabajo"), "origin_go", None, N_("G0 a X0 Y0 con láser apagado")),
    ActionSpec("machine.read_position", N_("Leer posición"), "position", None, N_("Solicita un informe de estado")),
    ActionSpec("machine.goto", N_("Ir a coordenadas…"), "goto", "Ctrl+J", ""),
    ActionSpec("machine.positions", N_("Posiciones guardadas"), "list", None, ""),
    ActionSpec("machine.grbl_settings", N_("Parámetros GRBL…"), None, None, N_("Lee, explica, valida y edita $$")),
    ActionSpec("machine.macros", N_("Macros…"), "macro", None, N_("Editor de macros")),
    ActionSpec("machine.console", N_("Consola"), "console", "Ctrl+`", ""),
    ActionSpec("machine.diagnostics", N_("Diagnóstico…"), None, None, N_("Estado interno, cola y eventos de seguridad")),
    ActionSpec("machine.backup_config", N_("Copia de seguridad de configuración GRBL…"), None, None, ""),
    ActionSpec("machine.comm_log", N_("Exportar registro de comunicación…"), None, None, ""),
    ActionSpec("machine.estop", N_("PARADA DE SOFTWARE"), "estop", "Ctrl+Shift+Space",
               N_("Reinicio inmediato del controlador. No sustituye la parada de emergencia física")),
    ActionSpec("machine.camera", N_("Cámara"), "camera", None, N_("Vista de cámara"), checkable=False),
    ActionSpec("machine.settings", N_("Configuración del dispositivo"), "device", None, N_("Perfiles de máquina y conexión")),
    # Cámara (Fase 4)
    ActionSpec("camera.detect", N_("Detectar cámaras"), None, None, "", phase=4),
    ActionSpec("camera.calibrate_lens", N_("Calibrar lente…"), None, None, "", phase=4),
    ActionSpec("camera.calibrate_area", N_("Calibrar área de trabajo…"), None, None, "", phase=4),
    ActionSpec("camera.capture", N_("Capturar imagen"), None, None, "", phase=4),
    # Herramientas
    ActionSpec("tools.sim_door_open", N_("Simulador: abrir puerta"), "door", None, N_("Sólo con el simulador GRBL")),
    ActionSpec("tools.sim_door_close", N_("Simulador: cerrar puerta"), None, None, N_("Sólo con el simulador GRBL")),
    ActionSpec("tools.sim_unplug", N_("Simulador: desconectar cable"), None, None, N_("Prueba la pérdida de conexión")),
    ActionSpec("tools.material_library", N_("Biblioteca de materiales…"), None, None, "", phase=4),
    ActionSpec("tools.shape_library", N_("Biblioteca de formas…"), None, None, "", phase=4),
    # Ayuda
    ActionSpec("help.safety", N_("Seguridad…"), "laser", None, ""),
    ActionSpec("help.shortcuts", N_("Atajos de teclado…"), None, None, ""),
    ActionSpec("help.licenses", N_("Licencias…"), None, None, ""),
    ActionSpec("help.about", N_("Acerca de…"), "help", None, ""),
]


class ActionRegistry:
    def __init__(self, parent: QWidget) -> None:
        self.actions: dict[str, QAction] = {}
        self.specs: dict[str, ActionSpec] = {}
        for spec in SPECS:
            act = QAction(tr(spec.text), parent)
            if spec.icon:
                act.setIcon(icons.icon(spec.icon))
            if spec.shortcut is not None:
                seq = QKeySequence(spec.shortcut)
                act.setShortcut(seq)
            act.setCheckable(spec.checkable)
            tip = tr(spec.tip) if spec.tip else tr(spec.text)
            shortcut_text = act.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if spec.phase > CURRENT_PHASE:
                tip = tr("{t} — disponible en la fase {n}", t=tip, n=spec.phase)
                act.setEnabled(False)
            full = f"{tr(spec.text)}" + (f" ({shortcut_text})" if shortcut_text else "")
            act.setToolTip(f"<b>{full}</b><br>{tip}" if tip != tr(spec.text) else full)
            act.setStatusTip(tip)
            act.setWhatsThis(tip)
            act.setObjectName(spec.id)
            act.setData(spec.phase)
            self.actions[spec.id] = act
            self.specs[spec.id] = spec

    def __getitem__(self, key: str) -> QAction:
        return self.actions[key]

    def implemented(self, key: str) -> bool:
        return self.specs[key].phase <= CURRENT_PHASE
