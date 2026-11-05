"""Preferencias de la aplicación, perfiles, macros y posiciones guardadas.

Se guardan en JSON versionado con escritura atómica. Los campos
desconocidos se ignoran y los ausentes toman su valor por defecto, lo que
permite migrar entre versiones.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openlaser.devices.profile import DeviceProfile, demo_profile
from openlaser.persistence.atomic import atomic_write_json, read_json
from openlaser.safety.manager import SafetyConfig

SETTINGS_VERSION = 1

DEFAULT_KEYMAP: dict[str, str] = {
    "jog_x_plus": "Alt+Right", "jog_x_minus": "Alt+Left", "jog_y_plus": "Alt+Up", "jog_y_minus": "Alt+Down",
    "jog_z_plus": "Alt+PgUp", "jog_z_minus": "Alt+PgDown", "jog_a_plus": "Alt+Shift+Right",
    "jog_a_minus": "Alt+Shift+Left",
}
# Atajos de la Fase 1: las flechas solas ahora desplazan la selección en el lienzo.
_PHASE1_KEYMAP = {
    "jog_x_plus": "Right", "jog_x_minus": "Left", "jog_y_plus": "Up", "jog_y_minus": "Down",
    "jog_z_plus": "PgUp", "jog_z_minus": "PgDown", "jog_a_plus": "Ctrl+Right", "jog_a_minus": "Ctrl+Left",
}


@dataclass
class Macro:
    name: str
    lines: list[str]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def demo_macros() -> list[Macro]:
    """Macros de prueba que NUNCA activan el láser."""
    return [
        Macro("Estado ($G)", ["$G"], "Muestra el estado modal del parser."),
        Macro("Información ($I)", ["$I"], "Versión y opciones del firmware."),
        Macro("Coordenadas ($#)", ["$#"], "Desplazamientos de trabajo guardados."),
        Macro("Ir al centro", ["M5", "G53G0X{cx}Y{cy}"], "Mueve en G0 (láser apagado) al centro del área."),
        Macro("Cuadrado 20 mm sin láser", ["M5", "G91", "G1X20F1500S0", "G1Y20", "G1X-20", "G1Y-20", "G90"],
              "Recorre un cuadrado relativo con S0 y M5."),
    ]


@dataclass
class SavedPosition:
    name: str
    x: float
    y: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AppSettings:
    version: int = SETTINGS_VERSION
    language: str = "es"
    theme: str = "dark"  # light | dark | contrast
    units: str = "mm"
    show_icon_text: bool = False
    color_icons: bool = True        # iconos a color (en alto contraste siempre monocromo)
    undo_limit: int = 0
    grid_mm: float = 10.0
    profiles: list[DeviceProfile] = field(default_factory=lambda: [demo_profile()])
    active_profile_id: str = "demo-sim"
    macros: list[Macro] = field(default_factory=demo_macros)
    saved_positions: list[SavedPosition] = field(default_factory=list)
    jog_step_mm: float = 10.0
    jog_feed_mm_min: float = 3000.0
    jog_continuous: bool = False
    keymap: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KEYMAP))
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    window_geometry: str = ""
    window_state: str = ""
    recent_gcode: list[str] = field(default_factory=list)
    recent_projects: list[str] = field(default_factory=list)
    autosave_minutes: int = 3
    layer_presets: dict[str, dict] = field(default_factory=dict)
    snap_grid: bool = False
    snap_nodes: bool = True
    snap_centers: bool = False
    tool_options: dict[str, float] = field(default_factory=lambda: {"radius": 0.0, "sides": 6, "star": 0,
                                                                     "inner": 0.5})
    camera_device: str = ""
    plugins_enabled: list[str] = field(default_factory=list)
    test_params: dict[str, dict] = field(default_factory=dict)
    console_show_status: bool = False
    console_show_job: bool = False

    def active_profile(self) -> DeviceProfile:
        for p in self.profiles:
            if p.id == self.active_profile_id:
                return p
        if not self.profiles:
            self.profiles.append(demo_profile())
        self.active_profile_id = self.profiles[0].id
        return self.profiles[0]

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items()
             if k not in ("profiles", "macros", "saved_positions", "safety")}
        d["profiles"] = [p.to_dict() for p in self.profiles]
        d["macros"] = [m.to_dict() for m in self.macros]
        d["saved_positions"] = [s.to_dict() for s in self.saved_positions]
        d["safety"] = self.safety.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        s = cls()
        for key, value in data.items():
            if key in ("profiles", "macros", "saved_positions", "safety", "version"):
                continue
            if hasattr(s, key):
                setattr(s, key, value)
        if isinstance(data.get("profiles"), list) and data["profiles"]:
            s.profiles = [DeviceProfile.from_dict(p) for p in data["profiles"]]
        if isinstance(data.get("macros"), list):
            s.macros = [Macro(**{k: v for k, v in m.items() if k in ("name", "lines", "description")})
                        for m in data["macros"]]
        if isinstance(data.get("saved_positions"), list):
            s.saved_positions = [SavedPosition(**p) for p in data["saved_positions"]]
        if isinstance(data.get("safety"), dict):
            s.safety = SafetyConfig.from_dict(data["safety"])
        merged = dict(DEFAULT_KEYMAP)
        for k, v in (s.keymap or {}).items():
            if _PHASE1_KEYMAP.get(k) != v:  # migración: los valores por defecto antiguos se actualizan
                merged[k] = v
        s.keymap = merged
        return s


def load_settings(path: Path) -> AppSettings:
    data = read_json(path, default=None)
    if not isinstance(data, dict):
        return AppSettings()
    try:
        return AppSettings.from_dict(data)
    except (TypeError, ValueError, KeyError):
        return AppSettings()


def save_settings(path: Path, settings: AppSettings) -> None:
    atomic_write_json(path, settings.to_dict())
