"""Iconografía original de OpenLaser Studio.

Cada icono es un fragmento SVG de 24×24 dibujado a trazo con ``currentColor``.

Hay dos estilos:

* **Color** (predeterminado en los temas claro y oscuro): bicolor por familias (archivo azul, edición
  violeta, dibujo verde azulado, organizar índigo, vista celeste, láser naranja, máquina cian, cámara
  rosa, materiales ámbar). Las formas cerradas llevan un relleno suave del mismo color, y los iconos
  clave (iniciar, pausa, detener, parada, home, conectar, desbloquear, disparo…) tienen un diseño propio
  con colores con significado (verde = marcha, ámbar = pausa, rojo = parar). Cada tema usa tonos con
  contraste suficiente sobre su fondo.
* **Monocromo**: el trazo con el color del texto del tema (siempre en alto contraste).

Los estados seleccionado/pulsado y deshabilitado se dibujan siempre en monocromo, para que se distingan.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from openlaser.ui import theme

_S = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
_F = 'fill="currentColor" stroke="none"'

ICONS: dict[str, str] = {
    # --- archivo ---
    "new": f'<path {_S} d="M6 3h8l4 4v14H6z"/><path {_S} d="M14 3v4h4"/><path {_S} d="M12 11v6M9 14h6"/>',
    "open": f'<path {_S} d="M3 7h6l2 2h10v10H3z"/><path {_S} d="M3 11h18"/>',
    "save": f'<path {_S} d="M4 4h13l3 3v13H4z"/><path {_S} d="M8 4v5h8V4"/><rect {_S} x="8" y="13" width="8" height="5"/>',
    "import": f'<path {_S} d="M12 3v11M8 10l4 4 4-4"/><path {_S} d="M4 15v5h16v-5"/>',
    "export": f'<path {_S} d="M12 15V4M8 8l4-4 4 4"/><path {_S} d="M4 15v5h16v-5"/>',
    "undo": f'<path {_S} d="M9 5L4 10l5 5"/><path {_S} d="M4 10h10a5 5 0 0 1 0 10h-4"/>',
    "redo": f'<path {_S} d="M15 5l5 5-5 5"/><path {_S} d="M20 10H10a5 5 0 0 0 0 10h4"/>',
    "cut": f'<circle {_S} cx="6" cy="18" r="3"/><circle {_S} cx="18" cy="18" r="3"/><path {_S} d="M8 16L18 3M16 16L6 3"/>',
    "copy": f'<rect {_S} x="8" y="8" width="12" height="12" rx="1"/><path {_S} d="M16 8V4H4v12h4"/>',
    "paste": f'<rect {_S} x="5" y="5" width="14" height="16" rx="1"/><path {_S} d="M9 3h6v4H9z"/><path {_S} d="M9 12h6M9 16h4"/>',
    "delete": f'<path {_S} d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14"/><path {_S} d="M10 11v6M14 11v6"/>',
    # --- herramientas de dibujo ---
    "select": f'<path {_S} d="M5 3l14 8-6 2-3 6z"/>',
    "nodes": f'<path {_S} d="M4 18C8 6 16 18 20 6"/><rect {_F} x="2.5" y="16.5" width="3" height="3"/>'
             f'<rect {_F} x="18.5" y="4.5" width="3" height="3"/><rect {_F} x="10.5" y="10.5" width="3" height="3"/>',
    "line": f'<path {_S} d="M4 20L20 4"/><circle {_F} cx="4" cy="20" r="2"/><circle {_F} cx="20" cy="4" r="2"/>',
    "rect": f'<rect {_S} x="4" y="6" width="16" height="12" rx="1.5"/>',
    "ellipse": f'<ellipse {_S} cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "polygon": f'<path {_S} d="M12 3l8.5 6.2-3.2 10H6.7L3.5 9.2z"/>',
    "text": f'<path {_S} d="M5 5h14M12 5v15M9 20h6"/>',
    "image": f'<rect {_S} x="3" y="5" width="18" height="14" rx="1"/><circle {_S} cx="8.5" cy="10" r="1.6"/>'
             f'<path {_S} d="M3 17l5-4 4 3 3-2 6 4"/>',
    "vectorize": f'<path {_S} d="M4 18c3-9 7-12 16-13"/><path {_S} d="M4 5h4v4H4zM16 15h4v4h-4z" stroke-dasharray="2 2"/>',
    "measure": f'<path {_S} d="M3 16L16 3l5 5L8 21z"/><path {_S} d="M7 12l2 2M10 9l2 2M13 6l2 2"/>',
    "zoom": f'<circle {_S} cx="10.5" cy="10.5" r="6.5"/><path {_S} d="M15.5 15.5L21 21M8 10.5h5M10.5 8v5"/>',
    "fit": f'<path {_S} d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect {_S} x="8" y="8" width="8" height="8"/>',
    "fit_selection": f'<path {_S} d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>'
                     f'<path {_S} stroke-dasharray="2 2" d="M7 7h10v10H7z"/><path {_S} d="M12 10v4M10 12h4"/>',
    "center": f'<circle {_S} cx="12" cy="12" r="7"/><path {_S} d="M12 2v5M12 17v5M2 12h5M17 12h5"/>',
    "align": f'<path {_S} d="M4 3v18"/><rect {_S} x="7" y="5" width="12" height="5"/><rect {_S} x="7" y="14" width="7" height="5"/>',
    "distribute": f'<path {_S} d="M3 4v16M21 4v16"/><rect {_S} x="7" y="8" width="4" height="8"/><rect {_S} x="13" y="8" width="4" height="8"/>',
    "group": f'<rect {_S} x="3" y="3" width="18" height="18" stroke-dasharray="3 2"/><rect {_S} x="6" y="6" width="6" height="6"/>'
             f'<circle {_S} cx="15.5" cy="15.5" r="3"/>',
    "ungroup": f'<rect {_S} x="3" y="3" width="8" height="8"/><circle {_S} cx="16" cy="16" r="4"/><path {_S} d="M14 4l6 6M20 4l-6 6"/>',
    "union": f'<path {_S} d="M4 4h10v6h6v10H10v-6H4z"/>',
    "subtract": f'<rect {_S} x="3" y="3" width="12" height="12"/><rect {_S} x="9" y="9" width="12" height="12" stroke-dasharray="2 2"/>'
                f'<rect {_F} x="3" y="3" width="6" height="12" opacity="0.35"/><rect {_F} x="9" y="3" width="6" height="6" opacity="0.35"/>',
    "intersect": f'<rect {_S} x="3" y="3" width="12" height="12"/><rect {_S} x="9" y="9" width="12" height="12"/>'
                 f'<rect {_F} x="9" y="9" width="6" height="6" opacity="0.55"/>',
    "offset": f'<rect {_S} x="8" y="8" width="8" height="8"/><rect {_S} x="3.5" y="3.5" width="17" height="17" rx="4" stroke-dasharray="3 2"/>',
    "array": f'<rect {_S} x="3" y="3" width="7" height="7"/><rect {_S} x="14" y="3" width="7" height="7"/>'
             f'<rect {_S} x="3" y="14" width="7" height="7"/><rect {_S} x="14" y="14" width="7" height="7"/>',
    "mirror": f'<path {_S} d="M12 3v18" stroke-dasharray="2 2"/><path {_S} d="M9 6L3 18h6zM15 6l6 12h-6z"/>',
    "rotate": f'<path {_S} d="M20 12a8 8 0 1 1-3-6.2"/><path {_S} d="M17 2v4h4"/><rect {_S} x="9" y="9" width="6" height="6"/>',
    # --- láser / trabajo ---
    "preview": f'<path {_S} d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle {_S} cx="12" cy="12" r="3"/>',
    "simulate": f'<path {_S} d="M3 20c3-5 5-1 8-6s5-1 8-6"/><path {_S} d="M4 4l6 3.5L4 11z"/>',
    "round": f'<path {_S} d="M4 20V12a8 8 0 0 1 8-8h8"/><path {_S} stroke-dasharray="2 2" d="M4 8V4h4"/>',
    "tabs": f'<path {_S} d="M3 12h6M15 12h6"/><path {_S} d="M9 9v6M15 9v6"/>',
    "optimize": f'<circle {_S} cx="5" cy="6" r="2"/><circle {_S} cx="19" cy="18" r="2"/><path {_S} d="M7 6h6a4 4 0 0 1 0 8H11a4 4 0 0 0 0 8"/>',
    "frame": f'<path {_S} d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path {_S} d="M8 12h8" stroke-dasharray="2 2"/>',
    "start": f'<path {_S} d="M7 4l13 8-13 8z"/>',
    "pause": f'<rect {_S} x="6" y="4" width="4" height="16" rx="1"/><rect {_S} x="14" y="4" width="4" height="16" rx="1"/>',
    "resume": f'<rect {_S} x="4" y="4" width="3" height="16" rx="1"/><path {_S} d="M11 4l10 8-10 8z"/>',
    "stop": f'<rect {_S} x="5" y="5" width="14" height="14" rx="2"/>',
    "estop": f'<circle {_S} cx="12" cy="12" r="9"/><path {_S} d="M8 8l8 8M16 8l-8 8"/>',
    "home": f'<path {_S} d="M3 11l9-7 9 7"/><path {_S} d="M6 10v10h12V10"/><path {_S} d="M10 20v-5h4v5"/>',
    "unlock": f'<rect {_S} x="5" y="11" width="14" height="10" rx="1.5"/><path {_S} d="M8 11V7a4 4 0 0 1 7.5-2"/>',
    "lock": f'<rect {_S} x="5" y="11" width="14" height="10" rx="1.5"/><path {_S} d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "eye": f'<path {_S} d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle {_S} cx="12" cy="12" r="3"/>',
    "connect": f'<path {_S} d="M7 12h4M13 12h4"/><path {_S} d="M4 9h3v6H4zM17 9h3v6h-3z"/><path {_S} d="M11 9v6M13 9v6"/>',
    "disconnect": f'<path {_S} d="M4 9h3v6H4zM17 9h3v6h-3z"/><path {_S} d="M7 12h2M15 12h2"/><path {_S} d="M10 6l4 12"/>',
    "camera": f'<path {_S} d="M3 8h4l2-3h6l2 3h4v12H3z"/><circle {_S} cx="12" cy="13.5" r="4"/>',
    "rotary": f'<ellipse {_S} cx="12" cy="7" rx="7" ry="3"/><path {_S} d="M5 7v10c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/>'
              f'<path {_S} d="M16 12.5l2 1.5-2 1.5"/>',
    "material": f'<rect {_S} x="3" y="3" width="18" height="18"/><path {_S} d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'
                f'<rect {_F} x="15" y="15" width="6" height="6" opacity="0.6"/><rect {_F} x="9" y="9" width="6" height="6" opacity="0.35"/>',
    "device": f'<rect {_S} x="3" y="5" width="18" height="12" rx="1.5"/><path {_S} d="M8 21h8M12 17v4"/>'
              f'<circle {_S} cx="12" cy="11" r="2.5"/>',
    "settings": f'<circle {_S} cx="12" cy="12" r="3"/><path {_S} d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1'
                f'M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "origin_set": f'<path {_S} d="M4 20V6M4 20h14"/><circle {_S} cx="4" cy="20" r="2.5"/><path {_S} d="M14 6h6M17 3v6"/>',
    "origin_clear": f'<path {_S} d="M4 20V6M4 20h14"/><circle {_S} cx="4" cy="20" r="2.5"/><path {_S} d="M14 4l5 5M19 4l-5 5"/>',
    "origin_go": f'<path {_S} d="M4 20V6M4 20h14"/><circle {_S} cx="4" cy="20" r="2.5"/><path {_S} d="M19 5L9 15M9 9v6h6"/>',
    "goto": f'<circle {_S} cx="12" cy="10" r="3"/><path {_S} d="M12 21s-7-6.5-7-11a7 7 0 0 1 14 0c0 4.5-7 11-7 11z"/>',
    "fire": f'<path {_S} d="M12 2v6"/><path {_S} d="M12 22a5 5 0 0 0 5-5c0-3-5-7-5-7s-5 4-5 7a5 5 0 0 0 5 5z"/>',
    "laser": f'<rect {_S} x="8" y="2" width="8" height="8" rx="1"/><path {_S} d="M12 10v7"/><path {_S} d="M8 21h8M10 18l-2 3M14 18l2 3"/>',
    "console": f'<rect {_S} x="3" y="4" width="18" height="16" rx="1.5"/><path {_S} d="M7 9l3 3-3 3M12 15h5"/>',
    "layers": f'<path {_S} d="M12 3l9 5-9 5-9-5z"/><path {_S} d="M3 13l9 5 9-5"/>',
    "macro": f'<path {_S} d="M4 6h10M4 12h7M4 18h10"/><path {_S} d="M16 10l5 3-5 3z"/>',
    "help": f'<circle {_S} cx="12" cy="12" r="9"/><path {_S} d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7v.5"/>'
            f'<circle {_F} cx="12" cy="17.2" r="1.1"/>',
    "position": f'<path {_S} d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle {_S} cx="12" cy="12" r="5"/><circle {_F} cx="12" cy="12" r="1.5"/>',
    "door": f'<path {_S} d="M5 21V3h11v18"/><path {_S} d="M16 6l3 1v14h-3"/><circle {_F} cx="13" cy="12" r="1.1"/>',
    "plug": f'<path {_S} d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
    "list": f'<path {_S} d="M8 6h13M8 12h13M8 18h13"/><circle {_F} cx="4" cy="6" r="1.3"/><circle {_F} cx="4" cy="12" r="1.3"/>'
            f'<circle {_F} cx="4" cy="18" r="1.3"/>',
    "app": f'<rect {_S} x="3" y="3" width="18" height="18" rx="4"/><path {_S} d="M12 6v6"/><path {_S} d="M7 18l5-6 5 6"/>'
           f'<circle {_F} cx="12" cy="12" r="1.6"/>',
}
# Flechas de movimiento manual (jog): una flecha girada para las 8 direcciones.
_ARROW = f'<path {_S} d="M12 20V5"/><path {_S} d="M6 11l6-6 6 6"/>'
for _name, _deg in (("jog_n", 0), ("jog_ne", 45), ("jog_e", 90), ("jog_se", 135), ("jog_s", 180),
                    ("jog_sw", 225), ("jog_w", 270), ("jog_nw", 315)):
    ICONS[_name] = f'<g transform="rotate({_deg} 12 12)">{_ARROW}</g>'
for _name, _up in (("jog_zp", True), ("jog_zm", False)):
    ICONS[_name] = (f'<path {_S} d="M5 20h14"/>' + (f'<path {_S} d="M12 16V4M7 9l5-5 5 5"/>' if _up else
                                                     f'<path {_S} d="M12 4v12M7 11l5 5 5-5"/>'))
for _name, _cw in (("jog_ap", True), ("jog_am", False)):
    ICONS[_name] = (f'<ellipse {_S} cx="12" cy="12" rx="8" ry="4"/>'
                    + (f'<path {_S} d="M15 5l3 3-3 3"/>' if _cw else f'<path {_S} d="M9 5L6 8l3 3"/>'))

# --------------------------------------------------------------------------- color
# Tonos por tema: más claros sobre fondo oscuro, más profundos sobre fondo claro.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {"blue": "#5aa2ff", "violet": "#b392ff", "teal": "#2fd3c0", "indigo": "#8c93ff", "sky": "#4cc3ff",
             "orange": "#ff9a4d", "green": "#4ade80", "amber": "#fbbf24", "red": "#ff6b6b", "cyan": "#22d3ee",
             "pink": "#f472b6", "brown": "#e0a458", "slate": "#aab3c2", "white": "#ffffff"},
    "light": {"blue": "#1f6feb", "violet": "#7c3aed", "teal": "#0f9488", "indigo": "#4f46e5", "sky": "#0284c7",
              "orange": "#ea580c", "green": "#16a34a", "amber": "#d97706", "red": "#dc2626", "cyan": "#0891b2",
              "pink": "#db2777", "brown": "#a16207", "slate": "#475569", "white": "#ffffff"},
}

_FAMILY = {
    "blue": "new open save import export",
    "violet": "undo redo cut copy paste",
    "teal": "select nodes line rect ellipse polygon text image vectorize measure",
    "sky": "zoom fit fit_selection center preview eye list layers",
    "indigo": "align distribute group ungroup union subtract intersect offset array mirror rotate",
    "orange": "simulate round tabs optimize frame laser fire macro",
    "cyan": "device settings origin_set origin_clear origin_go goto position rotary console plug door "
            "jog_n jog_ne jog_e jog_se jog_s jog_sw jog_w jog_nw",
    "violet_z": "jog_zp jog_zm",
    "pink_a": "jog_ap jog_am",
    "pink": "camera",
    "brown": "material",
    "slate": "lock",
}
FAMILY_OF: dict[str, str] = {name: fam.split("_")[0] for fam, names in _FAMILY.items() for name in names.split()}

# Diseños propios a color. Fichas: {green} {red} … del tema y {ink} (texto del tema).
_O = 'fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
COLOR_ICONS: dict[str, str] = {
    "start": '<path d="M7 4l13 8-13 8z" fill="{green}" stroke="{green}" stroke-width="1.6" stroke-linejoin="round"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1.2" fill="{amber}"/>'
             '<rect x="14" y="4" width="4" height="16" rx="1.2" fill="{amber}"/>',
    "resume": '<rect x="4" y="4" width="3" height="16" rx="1" fill="{amber}"/>'
              '<path d="M11 4l10 8-10 8z" fill="{green}" stroke="{green}" stroke-width="1.4" stroke-linejoin="round"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2.5" fill="{red}"/>',
    "estop": '<circle cx="12" cy="12" r="10" fill="{red}"/><circle cx="12" cy="12" r="7" fill="none" '
             'stroke="{amber}" stroke-width="1.6"/><path d="M9 9l6 6M15 9l-6 6" stroke="{white}" stroke-width="2.2" '
             'stroke-linecap="round"/>',
    "home": '<path d="M4 11l8-6.5 8 6.5v9H4z" fill="{blue}" fill-opacity="0.25" stroke="{blue}" ' + _O[12:] + '/>'
            '<path d="M2.5 11.5L12 4l9.5 7.5" ' + _O.replace('fill="none"', 'fill="none" stroke="{blue}"') + '/>'
            '<rect x="10" y="14" width="4" height="6" rx="0.8" fill="{orange}"/>',
    "connect": '<path d="M7.5 12h9" stroke="{green}" stroke-width="2" stroke-linecap="round"/>'
               '<rect x="2.5" y="8.5" width="5" height="7" rx="1.2" fill="{green}"/>'
               '<rect x="16.5" y="8.5" width="5" height="7" rx="1.2" fill="{green}"/>'
               '<circle cx="12" cy="12" r="2" fill="{white}" stroke="{green}" stroke-width="1.4"/>',
    "disconnect": '<rect x="2.5" y="8.5" width="5" height="7" rx="1.2" fill="{slate}"/>'
                  '<rect x="16.5" y="8.5" width="5" height="7" rx="1.2" fill="{slate}"/>'
                  '<path d="M7.5 12h2M14.5 12h2" stroke="{slate}" stroke-width="2" stroke-linecap="round"/>'
                  '<path d="M10 6l4 12" stroke="{red}" stroke-width="2.2" stroke-linecap="round"/>',
    "unlock": '<path d="M8 11V7a4 4 0 0 1 7.5-2" ' + _O.replace('fill="none"', 'fill="none" stroke="{slate}"') + '/>'
              '<rect x="5" y="11" width="14" height="10" rx="2" fill="{amber}"/>'
              '<circle cx="12" cy="15.5" r="1.6" fill="{white}"/>',
    "lock": '<path d="M8 11V7a4 4 0 0 1 8 0v4" ' + _O.replace('fill="none"', 'fill="none" stroke="{slate}"') + '/>'
            '<rect x="5" y="11" width="14" height="10" rx="2" fill="{slate}"/>'
            '<circle cx="12" cy="15.5" r="1.6" fill="{white}"/>',
    "fire": '<path d="M12 22a6 6 0 0 0 6-6c0-3.6-6-8.5-6-8.5S6 12.4 6 16a6 6 0 0 0 6 6z" fill="{orange}"/>'
            '<path d="M12 21a3 3 0 0 0 3-3c0-1.8-3-4.3-3-4.3S9 16.2 9 18a3 3 0 0 0 3 3z" fill="{amber}"/>'
            '<path d="M12 2v4" stroke="{red}" stroke-width="2" stroke-linecap="round"/>',
    "laser": '<rect x="7.5" y="2" width="9" height="8" rx="1.5" fill="{slate}"/>'
             '<path d="M12 10v8" stroke="{red}" stroke-width="2.2" stroke-linecap="round"/>'
             '<circle cx="12" cy="19" r="2.2" fill="{orange}"/><path d="M7 21.5h10" stroke="{ink}" '
             'stroke-width="1.6" stroke-linecap="round"/>',
    "delete": '<path d="M6 7l1 14h10l1-14z" fill="{red}" fill-opacity="0.22" stroke="{red}" ' + _O[12:] + '/>'
              '<path d="M4 7h16M9 7V4h6v3M10 11v6M14 11v6" ' + _O.replace('fill="none"', 'fill="none" stroke="{red}"')
              + '/>',
    "save": '<path d="M4 4h13l3 3v13H4z" fill="{blue}" fill-opacity="0.22" stroke="{blue}" ' + _O[12:] + '/>'
            '<rect x="8" y="4" width="8" height="5" fill="{blue}"/>'
            '<rect x="7.5" y="13" width="9" height="5" rx="0.8" fill="{white}" stroke="{blue}" stroke-width="1.4"/>',
    "camera": '<path d="M3 8h4l2-3h6l2 3h4v12H3z" fill="{pink}" fill-opacity="0.22" stroke="{pink}" ' + _O[12:] + '/>'
              '<circle cx="12" cy="13.5" r="4" fill="{sky}" stroke="{pink}" stroke-width="1.6"/>'
              '<circle cx="13.2" cy="12.3" r="1.1" fill="{white}"/>',
    "image": '<rect x="3" y="5" width="18" height="14" rx="1.5" fill="{sky}" fill-opacity="0.18" stroke="{teal}" '
             + _O[12:] + '/><circle cx="8.5" cy="10" r="1.8" fill="{amber}"/>'
             '<path d="M3.5 18l5-4.5 4 3 3-2 5 3.5V18.5H3.5z" fill="{green}"/>',
    "help": '<circle cx="12" cy="12" r="9.5" fill="{blue}"/>'
            '<path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7v.5" fill="none" stroke="{white}" '
            'stroke-width="1.9" stroke-linecap="round"/><circle cx="12" cy="17.2" r="1.2" fill="{white}"/>',
    "frame": '<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4" ' + _O.replace('fill="none"', 'fill="none" stroke="{orange}"')
             + '/><path d="M8 12h8" stroke="{sky}" stroke-width="1.8" stroke-dasharray="2 2"/>',
    "preview": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" fill="{sky}" fill-opacity="0.2" stroke="{sky}" '
               + _O[12:] + '/><circle cx="12" cy="12" r="3.2" fill="{blue}"/>',
    "app": '<rect x="3" y="3" width="18" height="18" rx="4" fill="{orange}"/><path d="M12 6v6" stroke="{white}" '
           'stroke-width="1.9" stroke-linecap="round"/><path d="M7 18l5-6 5 6" fill="none" stroke="{white}" '
           'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="1.7" '
           'fill="{amber}"/>',
}

_CLOSED_TAG = re.compile(r'<(rect|circle|ellipse|polygon|path)\b([^>]*?)/>')
_color_mode = True


def set_color_mode(on: bool) -> None:
    """Activa o desactiva los iconos a color (se borra la caché de imágenes)."""
    global _color_mode
    if on != _color_mode:
        _color_mode = on
        _cache.clear()


def color_mode() -> bool:
    return _color_mode and theme.current().name != "contrast"


def palette() -> dict[str, str]:
    t = theme.current()
    pal = dict(PALETTES["light" if t.name == "light" else "dark"])
    pal["ink"] = t.text
    return pal


def _duotone(body: str, color: str) -> str:
    """Trazo del color de la familia y relleno suave en las formas cerradas (no en las discontinuas)."""
    def fill(m: re.Match[str]) -> str:
        tag, attrs = m.group(1), m.group(2)
        if 'fill="none"' in attrs and "stroke-dasharray" not in attrs:
            closed = tag != "path" or re.search(r'd="[^"]*[zZ]\s*"', attrs) is not None
            if closed:
                attrs = attrs.replace('fill="none"', f'fill="{color}" fill-opacity="0.2"')
        return f"<{tag}{attrs}/>"
    return _CLOSED_TAG.sub(fill, body).replace("currentColor", color)


def color_svg(name: str) -> bytes:
    """SVG del icono a color para el tema actual."""
    pal = palette()
    if name in COLOR_ICONS:
        body = COLOR_ICONS[name]
        for key, value in pal.items():
            body = body.replace("{" + key + "}", value)
    else:
        body = _duotone(ICONS.get(name, ICONS["help"]), pal[FAMILY_OF.get(name, "blue")])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{body}</svg>'.encode()


_cache: dict[tuple[str, str, int], QPixmap] = {}


def _svg(name: str, color: str) -> bytes:
    body = ICONS.get(name, ICONS["help"]).replace("currentColor", color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{body}</svg>'.encode()


def pixmap(name: str, color: str, size: int = 24) -> QPixmap:
    """Icono monocromo de ``color``; con ``color == "*"`` el diseño a color del tema actual."""
    key = (name, color if color != "*" else "*" + theme.current().name, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    renderer = QSvgRenderer(QByteArray(color_svg(name) if color == "*" else _svg(name, color)))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    _cache[key] = pm
    return pm


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red() * (1 - t) + cb.red() * t)
    g = round(ca.green() * (1 - t) + cb.green() * t)
    bl = round(ca.blue() * (1 - t) + cb.blue() * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _faded(pm: QPixmap, opacity: float) -> QPixmap:
    """Copia translúcida (estado deshabilitado de los iconos a color: se reconoce el color, pero apagado)."""
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def icon(name: str) -> QIcon:
    t = theme.current()
    disabled = _mix(t.muted, t.bg, 0.45)
    ic = QIcon()
    colored = color_mode()
    for size in (16, 24, 32, 48):
        normal = pixmap(name, "*" if colored else t.text, size)
        ic.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(pixmap(name, "*" if colored else t.accent, size), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(pixmap(name, t.accent_text, size), QIcon.Mode.Selected, QIcon.State.Off)
        ic.addPixmap(pixmap(name, t.accent_text, size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_faded(normal, 0.38) if colored else pixmap(name, disabled, size), QIcon.Mode.Disabled,
                     QIcon.State.Off)
    return ic


def icon_on(name: str, color: str = "#ffffff") -> QIcon:
    """Icono de un solo color para botones con fondo de color (Detener, PARADA, Iniciar…): el icono a color
    se confundiría con el fondo. Deshabilitado: el mismo color, translúcido."""
    ic = QIcon()
    for size in (16, 24, 32, 48):
        pm = pixmap(name, color, size)
        ic.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(pm, QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(_faded(pm, 0.55), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def clear_cache() -> None:
    _cache.clear()


def color_swatch(color: str, size: int = 14) -> QIcon:
    """Icono cuadrado de color (para listas de capas)."""
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)
