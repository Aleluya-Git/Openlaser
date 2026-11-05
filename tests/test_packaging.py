"""Instalador de Windows: archivos presentes y versión coherente en todas partes."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"


def _version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert m
    return m.group(1)


def test_packaging_files_exist():
    for name in ("openlaser.spec", "installer.iss", "launcher.py", "openlaser.ico", "version_info.txt"):
        assert (PKG / name).is_file(), name
    assert (ROOT / "build_installer.bat").is_file()
    ico = (PKG / "openlaser.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") >= 5  # ICO con varios tamaños


def test_versions_match_everywhere():
    from openlaser import __version__
    from openlaser.app.config import APP_VERSION
    v = _version()
    assert __version__ == v == APP_VERSION
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert f'#define AppVersion "{v}"' in iss
    info = (PKG / "version_info.txt").read_text(encoding="utf-8")
    nums = tuple(int(x) for x in v.split("."))
    assert f"filevers=({nums[0]}, {nums[1]}, {nums[2]}, 0)" in info and f"'FileVersion', '{v}'" in info
    bat = (ROOT / "build_installer.bat").read_text(encoding="utf-8")
    assert f'set "VER={v}"' in bat


def test_spec_and_installer_reference_existing_paths():
    spec = (PKG / "openlaser.spec").read_text(encoding="utf-8")
    for rel in ("examples", "LICENSE", "docs/LICENCIAS.md", "packaging/launcher.py", "packaging/openlaser.ico"):
        assert (ROOT / rel).exists(), rel
    assert '"PySide6.QtMultimedia"' not in spec  # la cámara lo necesita: no se excluye
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "OpenLaserStudio.exe" in iss and "SafetyNote" in iss
    assert (ROOT / "build_installer.bat").read_bytes().count(b"\r\n") > 10  # .bat con finales CRLF


def test_launcher_entry_point():
    src = (PKG / "launcher.py").read_text(encoding="utf-8")
    assert "from openlaser.ui.app import main" in src


def test_spec_bundles_language_files():
    spec = (ROOT / "packaging" / "openlaser.spec").read_text(encoding="utf-8")
    assert '"openlaser/i18n/locales"' in spec
    assert '"openlaser.i18n" = ["locales/*.json"]' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_logo_and_icon_assets():
    assets = ROOT / "src" / "openlaser" / "ui" / "assets"
    for name in ("logo.png", "app_icon.png"):
        data = (assets / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
    ico = (ROOT / "packaging" / "openlaser.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") >= 5  # varias resoluciones
    spec = (ROOT / "packaging" / "openlaser.spec").read_text(encoding="utf-8")
    assert '"openlaser/ui/assets"' in spec
