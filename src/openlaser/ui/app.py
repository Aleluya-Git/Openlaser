"""Punto de entrada de la aplicación de escritorio.

Opciones de línea de comandos útiles para pruebas automáticas::

    python -m openlaser --screenshot captura.png   # abre, conecta al simulador, captura y sale
    python -m openlaser --lang en --theme light
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from openlaser.app.config import APP_ORG, APP_VERSION, app_name, log_dir, settings_path
from openlaser.diagnostics.logging_setup import setup_logging, shutdown_logging
from openlaser.i18n import set_language
from openlaser.persistence.settings import load_settings

_log = logging.getLogger(__name__)


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="openlaser", description=app_name())
    p.add_argument("--lang", help="es | en")
    p.add_argument("--theme", help="dark | light | contrast")
    p.add_argument("--settings", type=Path, help="Archivo de preferencias alternativo")
    p.add_argument("--screenshot", type=Path, help="Captura la ventana tras una demostración con el simulador y sale")
    p.add_argument("--reset-layout", action="store_true", help="Ignora la distribución de paneles guardada")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    setup_logging(log_dir())
    path = args.settings or settings_path()
    settings = load_settings(path)
    if args.lang:
        settings.language = args.lang
    if args.theme:
        settings.theme = args.theme
    if args.reset_layout or args.screenshot:
        settings.window_state = ""
        settings.window_geometry = ""
    set_language(settings.language)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)
    app.setApplicationName(app_name())
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORG)
    from openlaser.ui import theme
    theme.apply_theme(app, settings.theme)
    from openlaser.ui import icons
    icons.set_color_mode(settings.color_icons)

    from openlaser.ui.main_window import MainWindow
    win = MainWindow(settings, path, interactive=not args.screenshot)
    win.show()
    _log.info("%s %s iniciado", app_name(), APP_VERSION)

    if args.screenshot:
        _schedule_demo_screenshot(win, args.screenshot)
    code = app.exec()
    shutdown_logging()
    return int(code)


def _schedule_demo_screenshot(win, target: Path) -> None:  # noqa: ANN001 - MainWindow (import diferido)
    """Conecta al simulador, hace Home, mueve, muestra la vista previa y captura la ventana."""
    from openlaser.devices.profile import TransportKind

    win.resize(1600, 980)
    steps: list[tuple[int, object]] = []
    if win.profile.transport == TransportKind.SIMULATOR:
        win._simulator.speed_factor = 20.0
        win.profile.firmware = "marlin"  # Fase 6: el simulador imita Marlin (envío-respuesta, M114, láser en línea)
        steps = [
            (300, win._connect),
            # Marlin tarda ~1,5 s en conectar (sondas M115 hasta que dejan de responder): Home después.
            (2500, lambda: win.controller.home()),
            (3800, lambda: win.controller.jog(120, 160, 0, 0, 6000)),
            (4500, lambda: win.canvas.view.set_selection([s.id for s in win.doc.shapes[:1]])),
            (4800, lambda: win.registry["laser.simulate"].trigger()),
            (5100, lambda: win._show_dock(win.dock_console)),
        ]
    for delay, fn in steps:
        QTimer.singleShot(delay, fn)  # type: ignore[arg-type]

    def grab() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        win.grab().save(str(target))
        print(f"Captura guardada en {target}")
        win.close()
        QTimer.singleShot(200, QApplication.quit)

    QTimer.singleShot(6200, grab)


if __name__ == "__main__":
    raise SystemExit(main())
