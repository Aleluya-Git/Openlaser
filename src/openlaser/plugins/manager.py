"""Gestor de plugins.

Un plugin es una carpeta en ``<datos de usuario>/plugins/<id>/`` con:

* ``plugin.json``: ``id``, ``name``, ``version``, ``api`` (versión de la API, hoy 1),
  ``entry`` (módulo Python, por defecto ``plugin.py``), ``author``, ``license`` y
  ``description``.
* el módulo, que define ``register(api)``.

Los plugins ejecutan código con los mismos permisos que la aplicación, así que
**nunca se activan solos**: el usuario los habilita en *Editar → Plugins…* y la
lista se guarda en las preferencias. Un fallo al cargar un plugin se registra y
no impide arrancar.

La API permite añadir acciones al menú *Herramientas → Plugins*, registrar
adaptadores de controlador (sólo con documentación autorizada del fabricante),
generadores de pruebas y escribir en el registro. No da acceso directo al
controlador de la máquina: para mover o encender el láser un plugin debe generar
geometría o G-code que pasa por la vista previa y la comprobación previa.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from openlaser.plugins.controllers import ControllerAdapter, register as register_adapter_global

API_VERSION = 1
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
_log = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    api: int
    entry: str
    path: Path
    author: str = ""
    license: str = ""
    description: str = ""
    error: str = ""
    loaded: bool = False

    @property
    def compatible(self) -> bool:
        return self.api == API_VERSION


@dataclass
class PluginAction:
    plugin_id: str
    text: str
    callback: Callable[[], Any]


class PluginAPI:
    """Objeto que recibe ``register(api)``. Cada plugin tiene el suyo."""

    version = API_VERSION

    def __init__(self, manager: "PluginManager", plugin_id: str) -> None:
        self._m = manager
        self.plugin_id = plugin_id
        self.log = logging.getLogger(f"openlaser.plugin.{plugin_id}")

    def add_action(self, text: str, callback: Callable[[], Any]) -> None:
        self._m.actions.append(PluginAction(self.plugin_id, str(text), callback))

    def register_adapter(self, adapter: ControllerAdapter) -> None:
        register_adapter_global(adapter)
        self._m.adapters.append(adapter.id)

    def register_test(self, kind: Any) -> None:
        from openlaser.materials.test_patterns import TEST_KINDS
        if kind.id in TEST_KINDS:
            raise ValueError(f"Ya existe una prueba con id {kind.id}")
        TEST_KINDS[kind.id] = kind
        self._m.tests.append(kind.id)

    @property
    def context(self) -> Any:
        """Contexto de la aplicación (documento, deshacer, avisos). ``None`` sin interfaz."""
        return self._m.context


@dataclass
class PluginManager:
    root: Path
    enabled: set[str] = field(default_factory=set)
    context: Any = None
    plugins: dict[str, PluginInfo] = field(default_factory=dict)
    actions: list[PluginAction] = field(default_factory=list)
    adapters: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    modules: dict[str, ModuleType] = field(default_factory=dict)

    def discover(self) -> list[PluginInfo]:
        previous = self.plugins
        self.plugins = {}
        if not self.root.is_dir():
            return []
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            manifest = folder / "plugin.json"
            if not manifest.is_file():
                continue
            info = self._read_manifest(folder, manifest)
            if info.id in self.plugins:
                info.error = "id duplicado"
                info.id = f"{info.id}@{folder.name}"
            old = previous.get(info.id)
            if old is not None and old.loaded:  # un módulo cargado no se descarga hasta reiniciar
                info.loaded = True
            self.plugins[info.id] = info
        return list(self.plugins.values())

    def _read_manifest(self, folder: Path, manifest: Path) -> PluginInfo:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("plugin.json debe ser un objeto")
        except (OSError, ValueError) as exc:
            return PluginInfo(folder.name, folder.name, "?", 0, "", folder, error=f"plugin.json no válido: {exc}")
        pid = str(data.get("id", ""))
        info = PluginInfo(pid or folder.name, str(data.get("name", pid or folder.name)), str(data.get("version", "?")),
                          int(data.get("api", 0) or 0), str(data.get("entry", "plugin.py")), folder,
                          str(data.get("author", "")), str(data.get("license", "")), str(data.get("description", "")))
        if not _ID.match(pid):
            info.error = "id no válido (minúsculas, números, «.», «_» o «-»)"
        elif not info.compatible:
            info.error = f"API {info.api} no compatible (la aplicación usa la {API_VERSION})"
        elif ".." in Path(info.entry).parts or Path(info.entry).is_absolute() or not info.entry.endswith(".py"):
            info.error = "entry no válido"
        elif not (folder / info.entry).is_file():
            info.error = f"no existe {info.entry}"
        return info

    def load_enabled(self) -> list[PluginInfo]:
        """Carga los plugins habilitados y válidos. Devuelve los que se han cargado."""
        loaded = []
        for info in self.plugins.values():
            if info.id not in self.enabled or info.error or info.loaded:
                continue
            if self._load(info):
                loaded.append(info)
        return loaded

    def _load(self, info: PluginInfo) -> bool:
        name = f"openlaser_plugin_{info.id.replace('.', '_').replace('-', '_')}"
        try:
            spec = importlib.util.spec_from_file_location(name, info.path / info.entry)
            if spec is None or spec.loader is None:
                raise ImportError("no se puede cargar el módulo")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("el módulo no define register(api)")
            register(PluginAPI(self, info.id))
        except Exception as exc:  # noqa: BLE001 - un plugin defectuoso no debe impedir arrancar
            info.error = f"{type(exc).__name__}: {exc}"
            _log.error("Plugin %s: %s\n%s", info.id, exc, traceback.format_exc())
            sys.modules.pop(name, None)
            self.actions = [a for a in self.actions if a.plugin_id != info.id]
            return False
        info.loaded = True
        self.modules[info.id] = module
        return True

    def actions_for(self, plugin_id: str) -> list[PluginAction]:
        return [a for a in self.actions if a.plugin_id == plugin_id]
