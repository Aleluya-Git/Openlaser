"""Modelo de coordenadas: documento ↔ máquina ↔ trabajo.

* **Documento**: milímetros, origen en la esquina superior izquierda del área
  de trabajo, Y hacia abajo (convención SVG / pantalla). Es lo que se dibuja
  en el lienzo.
* **Máquina (MPos)**: milímetros respecto al origen físico (homing). La
  esquina del origen y el sentido de los ejes vienen del perfil.
* **Trabajo (WPos)**: ``WPos = MPos − WCO``, donde WCO es el desplazamiento
  del sistema G54 (+G92) que informa GRBL. El G-code se emite en
  coordenadas de trabajo.

Sólo existen dos transformaciones afines simples y sus inversas, lo que las
hace fáciles de probar (``tests/test_coords.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

from openlaser.devices.profile import DeviceProfile, OriginCorner

Point = tuple[float, float]


@dataclass(frozen=True)
class CoordinateSystem:
    width: float
    height: float
    origin: OriginCorner = OriginCorner.FRONT_LEFT
    invert_x: bool = False
    invert_y: bool = False

    @classmethod
    def from_profile(cls, p: DeviceProfile) -> "CoordinateSystem":
        return cls(p.width_mm, p.height_mm, p.origin, p.invert_x, p.invert_y)

    def _flip_x(self) -> bool:
        right = self.origin in (OriginCorner.FRONT_RIGHT, OriginCorner.REAR_RIGHT)
        return right != self.invert_x

    def _flip_y(self) -> bool:
        # En el documento Y crece hacia "delante" (abajo en pantalla).
        front = self.origin in (OriginCorner.FRONT_LEFT, OriginCorner.FRONT_RIGHT)
        return front != self.invert_y

    def doc_to_machine(self, p: Point) -> Point:
        x, y = p
        mx = self.width - x if self._flip_x() else x
        my = self.height - y if self._flip_y() else y
        return (mx, my)

    def machine_to_doc(self, p: Point) -> Point:
        # La transformación es una involución: aplicar dos veces devuelve el punto.
        return self.doc_to_machine(p)

    @staticmethod
    def machine_to_work(p: Point, wco: Point) -> Point:
        return (p[0] - wco[0], p[1] - wco[1])

    @staticmethod
    def work_to_machine(p: Point, wco: Point) -> Point:
        return (p[0] + wco[0], p[1] + wco[1])

    def doc_to_work(self, p: Point, wco: Point) -> Point:
        return self.machine_to_work(self.doc_to_machine(p), wco)

    def work_to_doc(self, p: Point, wco: Point) -> Point:
        return self.machine_to_doc(self.work_to_machine(p, wco))

    def inside(self, p_machine: Point, eps: float = 1e-6) -> bool:
        x, y = p_machine
        return -eps <= x <= self.width + eps and -eps <= y <= self.height + eps


def bounds(points: list[Point]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def rect_frame(b: tuple[float, float, float, float]) -> list[Point]:
    x0, y0, x1, y1 = b
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def convex_hull(points: list[Point]) -> list[Point]:
    """Envolvente convexa (Andrew monotone chain), cerrada."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts + pts[:1]

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return hull + hull[:1]
