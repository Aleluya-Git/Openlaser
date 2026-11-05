import json

from openlaser.devices.profile import DeviceProfile, OriginCorner, TransportKind
from openlaser.document.model import Document, demo_document
from openlaser.persistence.atomic import atomic_write_json, read_json
from openlaser.persistence.settings import AppSettings, SavedPosition, load_settings, save_settings


def test_atomic_write_and_backup(tmp_path):
    p = tmp_path / "a.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert read_json(p) == {"v": 2}
    assert json.loads((tmp_path / "a.json.bak").read_text()) == {"v": 1}
    assert not [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]


def test_corrupted_file_falls_back_to_backup(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, {"ok": True})
    atomic_write_json(p, {"ok": "nuevo"})
    p.write_text("{corrupto", encoding="utf-8")
    assert read_json(p) == {"ok": True}


def test_settings_round_trip(tmp_path):
    s = AppSettings()
    s.language = "en"
    s.saved_positions.append(SavedPosition("pieza", 10.5, 20))
    prof = DeviceProfile(name="Serie", transport=TransportKind.SERIAL, port="COM3", origin=OriginCorner.REAR_LEFT)
    s.profiles.append(prof)
    s.safety.max_fire_percent = 99  # se limita al cargar
    path = tmp_path / "settings.json"
    save_settings(path, s)
    loaded = load_settings(path)
    assert loaded.language == "en"
    assert loaded.saved_positions[0].x == 10.5
    assert loaded.profiles[-1].transport == TransportKind.SERIAL
    assert loaded.profiles[-1].origin == OriginCorner.REAR_LEFT
    assert loaded.safety.max_fire_percent <= 5
    assert "jog_x_plus" in loaded.keymap


def test_unknown_fields_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 99, "theme": "light", "campo_futuro": 1,
                                "profiles": [{"name": "x", "nuevo": 2}]}), encoding="utf-8")
    s = load_settings(path)
    assert s.theme == "light" and s.profiles[0].name == "x"
    assert load_settings(tmp_path / "no_existe.json").language == "es"


def test_document_round_trip():
    doc = demo_document()
    again = Document.from_dict(json.loads(json.dumps(doc.to_dict())))
    assert again.to_dict() == doc.to_dict()
