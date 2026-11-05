# -*- mode: python ; coding: utf-8 -*-
"""Receta de PyInstaller para OpenLaser Studio (Windows, carpeta «onedir» sin consola).

Uso (lo hace build_installer.bat):  .venv\Scripts\python.exe -m PyInstaller packaging\openlaser.spec
Resultado: dist\OpenLaserStudio\OpenLaserStudio.exe y sus bibliotecas.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH lo define PyInstaller

# Módulos de Qt que la aplicación no usa: se excluyen para reducir el tamaño (QtMultimedia sí: cámara).
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtWebView",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickWidgets", "QtQml", "QtCharts", "QtDataVisualization",
    "QtGraphs", "QtPdf", "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors",
    "QtSql", "QtTest", "QtDesigner", "QtHelp", "QtRemoteObjects", "QtScxml", "QtSerialBus", "QtSerialPort",
    "QtSpatialAudio", "QtTextToSpeech", "QtHttpServer", "QtStateMachine", "QtUiTools", "QtOpenGLWidgets",
)]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "examples"), "examples"),
        # idiomas: inglés, francés, alemán y la plantilla para crear otros
        (str(ROOT / "src" / "openlaser" / "i18n" / "locales"), "openlaser/i18n/locales"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "docs" / "LICENCIAS.md"), "."),
    ],
    hiddenimports=collect_submodules("openlaser") + collect_submodules("websockets") + ["serial.tools.list_ports"],
    excludes=QT_EXCLUDES + ["tkinter", "pytest", "_pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenLaserStudio",
    console=False,
    icon=str(ROOT / "packaging" / "openlaser.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="OpenLaserStudio", upx=False)
