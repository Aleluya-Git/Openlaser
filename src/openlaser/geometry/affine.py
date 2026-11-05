"""Transformaciones afines 2D (matriz 3×3 implícita)."""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Affine:
    """``x' = a·x + c·y + e``, ``y' = b·x + d·y + f`` (misma convención que SVG ``matrix(a b c d e f)``)."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    @staticmethod
    def identity() -> "Affine":
        return Affine()

    @staticmethod
    def translate(dx: float, dy: float) -> "Affine":
        return Affine(e=dx, f=dy)

    @staticmethod
    def scale(sx: float, sy: float | None = None, origin: Point = (0.0, 0.0)) -> "Affine":
        sy = sx if sy is None else sy
        ox, oy = origin
        return Affine(sx, 0.0, 0.0, sy, ox - sx * ox, oy - sy * oy)

    @staticmethod
    def rotate(deg: float, origin: Point = (0.0, 0.0)) -> "Affine":
        r = math.radians(deg)
        cs, sn = math.cos(r), math.sin(r)
        ox, oy = origin
        return Affine(cs, sn, -sn, cs, ox - cs * ox + sn * oy, oy - sn * ox - cs * oy)

    @staticmethod
    def skew(ax_deg: float, ay_deg: float) -> "Affine":
        return Affine(1.0, math.tan(math.radians(ay_deg)), math.tan(math.radians(ax_deg)), 1.0, 0.0, 0.0)

    def __matmul__(self, other: "Affine") -> "Affine":
        """``self @ other`` aplica primero ``other`` y después ``self``."""
        a1, b1, c1, d1, e1, f1 = self.a, self.b, self.c, self.d, self.e, self.f
        a2, b2, c2, d2, e2, f2 = other.a, other.b, other.c, other.d, other.e, other.f
        return Affine(a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
                      a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)

    def apply(self, p: Point) -> Point:
        x, y = p
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def determinant(self) -> float:
        return self.a * self.d - self.b * self.c

    def is_identity(self, eps: float = 1e-12) -> bool:
        return all(abs(v - w) < eps for v, w in zip((self.a, self.b, self.c, self.d, self.e, self.f),
                                                      (1, 0, 0, 1, 0, 0)))

    def scale_factor(self) -> float:
        """Escala media (para tolerancias de aplanado)."""
        return math.sqrt(abs(self.determinant())) or 1.0
