"""Envío a la tarjeta SD por HTTP (FluidNC/ESP3D) contra un servidor local que imita el manejador."""
import json
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openlaser.devices import sd_upload
from openlaser.devices.sd_upload import UploadError, UploadTarget


class FakeController:
    """Servidor HTTP mínimo con el comportamiento de /upload de FluidNC (subida y listado)."""

    def __init__(self, status="Ok", fail_code=0, report_size=True):
        self.files: dict[str, bytes] = {}
        self.requests: list[dict] = []
        self.status, self.fail_code, self.report_size = status, fail_code, report_size
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencio
                pass

            def _reply(self, folder):
                if outer.fail_code:
                    self.send_response(outer.fail_code)
                    self.end_headers()
                    return
                files = [{"name": k.rsplit("/", 1)[-1], "size": str(len(v)) if outer.report_size else "1 KB"}
                         for k, v in outer.files.items() if k.startswith(folder)]
                body = json.dumps({"files": files, "path": folder, "status": outer.status,
                                   "total": "4 GB", "used": "1 MB", "occupation": "1"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                outer.requests.append({"method": "GET", "path": self.path})
                self._reply(q.get("path", ["/"])[0])

            def do_POST(self):
                n = int(self.headers["Content-Length"])
                raw = self.rfile.read(n)
                msg = BytesParser(policy=HTTP).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
                fields, folder = {}, "/"
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    fname = part.get_filename()
                    payload = part.get_payload(decode=True)
                    if fname:
                        fields["file"] = (name, fname, payload)
                    else:
                        fields[name] = payload.decode()
                folder = fields.get("path", "/")
                outer.requests.append({"method": "POST", "path": self.path, "fields": fields})
                if not outer.fail_code and "file" in fields:
                    _n, fname, payload = fields["file"]
                    outer.files[fname] = payload
                self._reply(folder)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ctrl():
    c = FakeController()
    yield c
    c.close()


def test_safe_filename():
    assert sd_upload.safe_filename("Caja ñandú 3mm.nc") == "Caja_nandu_3mm.gcode"
    assert sd_upload.safe_filename("../../etc/passwd") == "etc_passwd.gcode"
    assert sd_upload.safe_filename("   ") == "trabajo.gcode"
    assert len(sd_upload.safe_filename("x" * 200)) <= sd_upload.MAX_NAME


def test_target_validation():
    assert UploadTarget("10.0.0.5", 8080, "sdfiles", "trabajos").url() == "http://10.0.0.5:8080/sdfiles"
    assert UploadTarget("h", folder="a/b").remote_folder() == "/a/b/"
    assert UploadTarget("h").remote_folder() == "/"
    for bad in (UploadTarget("http://x/y"), UploadTarget(""), UploadTarget("h", port=0)):
        with pytest.raises(UploadError):
            bad.url()
    with pytest.raises(UploadError):
        UploadTarget("h", folder="../x").remote_folder()


def test_job_text_always_ends_laser_off():
    data = sd_upload.job_text(["G90", "G1X1S100", ""], ["Trabajo ñ"]).decode()
    assert data.startswith("; Trabajo n\n")  # acentos y eñes se quitan del comentario
    assert data.rstrip().splitlines()[-1] == "M5"
    assert sd_upload.job_text(["G1X1", "M5"], []).decode().count("M5") == 1


def test_upload_roundtrip(ctrl):
    target = UploadTarget("127.0.0.1", ctrl.port, "/upload", "/jobs")
    data = sd_upload.job_text(["G90", "G21", "M5"], ["x"])
    res = sd_upload.upload(target, "Mi trabajo", data, timeout=5)
    assert res.remote_path == "/jobs/Mi_trabajo.gcode" and res.confirmed
    assert ctrl.files["/jobs/Mi_trabajo.gcode"] == data
    post = ctrl.requests[-1]
    assert post["method"] == "POST" and post["path"] == "/upload"
    f = post["fields"]
    assert f["path"] == "/jobs/" and f["/jobs/Mi_trabajo.gcodeS"] == str(len(data))
    assert f["file"][0] == "myfile[]"
    listing = sd_upload.list_files(target, timeout=5)
    assert "Mi_trabajo.gcode" in listing.files
    assert all(r["method"] != "POST" or r is post for r in ctrl.requests)  # el listado no sube nada
    # nunca se envía ninguna orden de ejecución
    assert not any("run" in r["path"].lower() or "ESP220" in r["path"] for r in ctrl.requests)


def test_upload_errors():
    c = FakeController(fail_code=500)
    try:
        with pytest.raises(UploadError) as e:
            sd_upload.upload(UploadTarget("127.0.0.1", c.port), "a", b"G90\n", timeout=5)
        assert e.value.params["code"] == 500 and "500" in e.value.translated()
    finally:
        c.close()
    c = FakeController(status="Error: no space")
    try:
        with pytest.raises(UploadError):
            sd_upload.upload(UploadTarget("127.0.0.1", c.port), "a", b"G90\n", timeout=5)
    finally:
        c.close()
    c = FakeController(report_size=False)
    try:
        assert sd_upload.upload(UploadTarget("127.0.0.1", c.port), "a", b"G90\n", timeout=5).confirmed
    finally:
        c.close()
    with pytest.raises(UploadError):
        sd_upload.upload(UploadTarget("127.0.0.1", 1), "a", b"G90\n", timeout=2)  # nadie escucha
    with pytest.raises(UploadError):
        sd_upload.upload(UploadTarget("127.0.0.1", 1), "a", b"", timeout=2)
