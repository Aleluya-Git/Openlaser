import json
import zipfile

import pytest

from openlaser.devices.profile import demo_profile
from openlaser.document.model import demo_document
from openlaser.persistence.project import (
    ProjectData, ProjectError, autosave_info, autosave_path, load_project, save_project,
)


def test_save_and_load(tmp_path):
    p = tmp_path / "demo.olsp"
    doc = demo_document()
    save_project(p, ProjectData(doc, demo_profile().to_dict(), {"state": "abc"}, resources={"img.png": b"\x89PNG"}))
    with zipfile.ZipFile(p) as zf:
        assert zf.namelist()[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    data = load_project(p)
    assert data.document.to_dict() == doc.to_dict()
    assert data.device_profile["name"] == demo_profile().name
    assert data.layout == {"state": "abc"} and data.resources == {"img.png": b"\x89PNG"}
    assert data.manifest["version"] == 1 and "modified" in data.manifest


def test_resave_keeps_backup(tmp_path):
    p = tmp_path / "a.olsp"
    save_project(p, ProjectData(demo_document()))
    save_project(p, ProjectData(demo_document()))
    assert (tmp_path / "a.olsp.bak").exists()


def test_rejects_bad_files(tmp_path):
    bad = tmp_path / "bad.olsp"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ProjectError):
        load_project(bad)
    other = tmp_path / "other.olsp"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "x"}))
        zf.writestr("document.json", "{}")
    with pytest.raises(ProjectError):
        load_project(other)
    newer = tmp_path / "newer.olsp"
    with zipfile.ZipFile(newer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "openlaser-project", "version": 99}))
        zf.writestr("document.json", "{}")
    with pytest.raises(ProjectError):
        load_project(newer)


def test_loads_phase1_json(tmp_path):
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "examples" / "documento_demo.json"
    data = load_project(src)
    assert len(data.document.shapes) == 5 and data.document.shapes[0].subpaths


def test_autosave_helpers(tmp_path):
    p = autosave_path(tmp_path)
    assert autosave_info(p) is None
    save_project(p, ProjectData(demo_document()))
    assert autosave_info(p)["path"] == str(p)
