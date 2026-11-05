"""Identidad configurable de la aplicación y rutas de usuario."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_APP_NAME = "OpenLaser Studio"
APP_VERSION = "0.6.1"
APP_ORG = "OpenLaser"


def app_name() -> str:
    """Nombre visible. Se puede cambiar con la variable OPENLASER_APP_NAME."""
    return os.environ.get("OPENLASER_APP_NAME", "").strip() or DEFAULT_APP_NAME


def app_slug() -> str:
    return "".join(ch for ch in app_name() if ch.isalnum()) or "OpenLaserStudio"


def user_dir() -> Path:
    override = os.environ.get("OPENLASER_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / app_slug()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / app_slug()
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / app_slug().lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return user_dir() / "settings.json"


def log_dir() -> Path:
    d = user_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
