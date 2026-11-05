# Licencias de las dependencias

OpenLaser Studio se distribuye bajo licencia **MIT** (archivo `LICENSE`). Todas las dependencias
son compatibles con esa licencia y con un uso comercial.

| Componente | Versión mínima | Licencia | Obligaciones principales |
|---|---|---|---|
| Python | 3.12 | PSF License 2.0 | Mantener el aviso de copyright si se redistribuye el intérprete |
| PySide6 (Qt for Python) y Qt 6 | 6.6 | LGPL-3.0 (opción GPL o comercial) | Enlace dinámico (lo habitual con pip/PyInstaller en modo carpeta); permitir sustituir las bibliotecas Qt; incluir el texto de la LGPL y el aviso de uso de Qt al distribuir binarios |
| pyserial | 3.5 | BSD-3-Clause | Conservar aviso de copyright y licencia |
| websockets | 12 | BSD-3-Clause | Conservar aviso de copyright y licencia |
| pytest (sólo desarrollo) | 8 | MIT | No se distribuye con la aplicación |
| PyInstaller (opcional, empaquetado) | 6 | GPL-2.0 con excepción de bootloader | La excepción permite distribuir ejecutables con cualquier licencia |
| NumPy (desde la Fase 3) | 1.26 | BSD-3-Clause | Conservar aviso |
| OpenCV / opencv-python (opcional, `pip install -e ".[opencv]"`) | 4.8 | Apache-2.0 | Conservar avisos y NOTICE; sólo para calibrar la lente con tablero |
| Qt Multimedia (incluido en PySide6) | 6.6 | LGPL-3.0 | Captura de cámara; mismas obligaciones que Qt |

## Formatos

* **SVG y DXF**: lectores propios (Fase 2); no se usan Shapely, ezdxf ni svgpathtools.
* **Imágenes ráster** (PNG, JPEG, BMP, GIF, TIFF, WebP): las decodifica Qt (incluido en PySide6);
  el procesamiento es NumPy y código propio. No se usa Pillow.
* **AI (Adobe Illustrator)**: no hay biblioteca libre y fiable para el formato propietario. Sólo se
  podrán abrir archivos AI guardados con compatibilidad PDF, a través de un importador PDF vectorial
  (Fase 4, propio) y se informa de lo que no se pueda importar.
* **PDF vectorial**: lector propio (Fase 4): objetos, flujos de objetos, compresión Flate, páginas,
  formularios y operadores de trazado; sin dependencias. No admite PDF cifrados.

## Recursos propios

Nombre, iconos (dibujados como SVG en `src/openlaser/ui/icons.py`), textos, distribución y estructura
son originales. No se ha copiado código, iconos, imágenes, textos, marcas ni recursos de LaserGRBL ni
de LightBurn. Los protocolos propietarios (Ruida, Trocen, controladores galvo) no se implementan por
ingeniería inversa; sólo se integrarán mediante plugins basados en documentación autorizada.
