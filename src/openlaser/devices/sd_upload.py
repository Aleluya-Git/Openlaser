"""Envío de G-code a la tarjeta SD de controladores con servidor web (FluidNC, ESP3D).

Sólo **guarda** el archivo en la tarjeta: OpenLaser nunca lo pone en marcha. Un trabajo ejecutado
desde la SD (pantalla del controlador o WebUI) queda fuera de la supervisión de OpenLaser.

Protocolo (basado en los manejadores web de ESP3D/FluidNC, no en una especificación formal):

* ``POST <endpoint>`` con ``multipart/form-data``: campo ``path`` (carpeta destino), campo
  ``<nombre>S`` con el tamaño en bytes (el firmware lo usa para comprobar el espacio libre y que la
  subida está completa) y la parte de archivo ``myfile[]`` con ``filename`` = ruta completa.
* FluidNC usa ``/upload`` para la SD (``/files`` es la memoria interna); ESP3D 3.x usa ``/sdfiles``.
* La respuesta es JSON con ``files``, ``path``, ``status``, ``total``, ``used``, ``occupation``.

Los nombres exactos de los campos pueden variar entre versiones de firmware; por eso el destino es
configurable y el resultado se comprueba buscando el archivo en el listado devuelto.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from openlaser.i18n import N_

ENDPOINTS: dict[str, str] = {"/upload": "FluidNC", "/sdfiles": "ESP3D 3.x"}
MAX_NAME = 48  # FluidNC admite nombres largos; se limita para pantallas pequeñas y FAT


class UploadError(Exception):
    """Fallo del envío: mensaje en español marcado con N_ y sus parámetros (la interfaz lo traduce)."""

    def __init__(self, message: str, **params: Any) -> None:
        super().__init__(message.format(**params) if params else message)
        self.message, self.params = message, params

    def translated(self) -> str:
        from openlaser.i18n import tr
        return tr(self.message, **self.params)


@dataclass
class UploadTarget:
    host: str
    port: int = 80
    endpoint: str = "/upload"
    folder: str = "/"

    def url(self) -> str:
        host = self.host.strip()
        if not host or any(c in host for c in "/?#@ "):
            raise UploadError(N_("Dirección del controlador no válida"))
        if not 0 < int(self.port) < 65536:
            raise UploadError(N_("Puerto HTTP no válido"))
        ep = "/" + self.endpoint.strip().lstrip("/")
        return f"http://{host}:{int(self.port)}{ep}"

    def remote_folder(self) -> str:
        parts = [p for p in self.folder.replace("\\", "/").split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise UploadError(N_("Carpeta de destino no válida"))
        return "/" + "/".join(parts) + ("/" if parts else "")


@dataclass
class UploadResult:
    remote_path: str
    size: int
    status: str = ""
    files: list[str] = field(default_factory=list)
    used: str = ""
    total: str = ""
    confirmed: bool = False   # el archivo aparece en el listado devuelto con su tamaño


def safe_filename(name: str) -> str:
    """Nombre seguro para la SD: ASCII, sin separadores ni espacios, con extensión .gcode."""
    stem = re.sub(r"\.(gcode|gc|nc|ngc|txt)$", "", name.strip(), flags=re.IGNORECASE)
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-") or "trabajo"
    return stem[: MAX_NAME - len(".gcode")] + ".gcode"


def build_multipart(target: UploadTarget, filename: str, data: bytes,
                    boundary: str | None = None) -> tuple[bytes, str]:
    folder = target.remote_folder()
    boundary = boundary or "----OpenLaser" + uuid.uuid4().hex
    full = folder + filename

    def part(name: str, value: str) -> bytes:
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()

    body = part("path", folder) + part(f"{full}S", str(len(data)))
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"myfile[]\"; filename=\"{full}\"\r\n"
             "Content-Type: text/plain\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _opener() -> urllib.request.OpenerDirector:
    # Controlador en la red local: nunca a través de un proxy del sistema.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _parse(raw: bytes, remote: str, size: int) -> UploadResult:
    res = UploadResult(remote, size)
    try:
        info: Any = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return res
    if not isinstance(info, dict):
        return res
    res.status = str(info.get("status", ""))
    res.used, res.total = str(info.get("used", "")), str(info.get("total", ""))
    name = remote.rsplit("/", 1)[-1]
    for f in info.get("files") or []:
        if isinstance(f, dict) and "name" in f:
            fname = str(f["name"])
            res.files.append(fname)
            if fname == name:
                fsize = str(f.get("size", ""))
                # el tamaño puede venir en bytes o como texto «12.3 KB»: sólo se exige si es numérico
                res.confirmed = not fsize.isdigit() or int(fsize) == size
    return res


def upload(target: UploadTarget, filename: str, data: bytes, timeout: float = 60.0) -> UploadResult:
    """Sube ``data`` como ``filename`` a la carpeta de la SD. Nunca inicia el trabajo."""
    if not data:
        raise UploadError(N_("El trabajo está vacío"))
    name = safe_filename(filename)
    url = target.url()
    body, ctype = build_multipart(target, name, data)
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype, "Content-Length": str(len(body))})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise UploadError(N_("El controlador rechazó el archivo (HTTP {code})"), code=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UploadError(N_("No se pudo conectar con el controlador")) from exc
    result = _parse(raw, target.remote_folder() + name, len(data))
    low = result.status.lower()
    if any(w in low for w in ("error", "fail", "denied", "not enough", "no space", "cannot")):
        raise UploadError(N_("El controlador informó de un error: {s}"), s=result.status)
    return result


def list_files(target: UploadTarget, timeout: float = 10.0) -> UploadResult:
    """Listado de la carpeta (GET al mismo manejador); sirve como prueba de conexión."""
    url = target.url() + "?" + urllib.parse.urlencode({"path": target.remote_folder()})
    try:
        with _opener().open(url, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise UploadError(N_("El controlador rechazó la petición (HTTP {code})"), code=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UploadError(N_("No se pudo conectar con el controlador")) from exc
    return _parse(raw, target.remote_folder(), 0)


def job_text(lines: list[str], header: list[str]) -> bytes:
    """Texto del trabajo para la SD: cabecera de comentarios, líneas y apagado final garantizado."""
    body = [ln for ln in lines if ln.strip()]
    tail = [ln.strip().upper() for ln in body[-3:]]
    if "M5" not in tail and not any(t.startswith("M5") for t in tail):
        body.append("M5")
    # Comentarios en ASCII sin acentos («Máquina» → «Maquina»): algunas pantallas de controlador no
    # muestran UTF-8 y GRBL sólo admite ASCII.
    head = [unicodedata.normalize("NFKD", f"; {h}").encode("ascii", "ignore").decode("ascii") for h in header]
    return ("\n".join(head + body) + "\n").encode("ascii", "replace")
