# Fase 2 — Entrega

Documento vectorial completo: herramientas de dibujo, edición de nodos, capas con todos sus
parámetros de corte, organización (alinear, distribuir, orden, booleanas, offset, matrices),
importación SVG/DXF, exportación SVG/DXF, proyecto nativo `.olsp` con autoguardado y recuperación,
y deshacer/rehacer global. Todo lo de la Fase 1 sigue igual (conexión, movimiento, seguridad).

## 1. Árbol de archivos (nuevos ★ y modificados ✎)

```
openlaser-studio/
├── autoprueba.bat / autoprueba.sh            ✎ captura → docs/captura_fase2.png
├── pyproject.toml                            ✎ versión 0.2.0, sin dependencias nuevas
├── examples/
│   ├── proyecto_demo.olsp                    ★ proyecto nativo de ejemplo
│   └── piezas_demo.dxf                       ★ DXF R12 de ejemplo (capas por color)
├── docs/FASE2.md                             ★ este documento
├── src/openlaser/
│   ├── geometry/
│   │   ├── affine.py                         ★ transformaciones afines 2D (mover, escalar, girar, sesgar)
│   │   └── path.py                           ★ nodos Bézier, subtrayectorias, primitivas, arcos SVG,
│   │                                            aplanado, áreas, contención, RDP, unión de tramos
│   ├── document/
│   │   ├── model.py                          ✎ formato 2: Layer completa, Shape inmutable con curvas
│   │   ├── commands.py                       ✎ DocumentEdit + edit_document (deshacer por instantánea)
│   │   └── operations.py                     ★ todas las operaciones del documento (sin Qt)
│   ├── importers/  __init__.py svg.py dxf.py ★ lectores propios SVG 1.1 y DXF (R12–2018 ASCII)
│   ├── exporters/__init__.py                 ★ exportación SVG (mm) y DXF R12
│   ├── persistence/
│   │   ├── project.py                        ★ formato .olsp (ZIP+JSON), autoguardado
│   │   └── settings.py                       ✎ recientes, preajustes, ajuste magnético, Alt+flechas
│   ├── toolpath/order.py                     ★ orden interior→exterior + vecino más cercano
│   ├── gcode/generator.py                    ✎ curvas aplanadas, orden optimizado, M8/M9, Z, M0
│   ├── canvas/
│   │   ├── qt_geometry.py                    ★ puente QPainterPath: booleanas, offset, texto a curvas
│   │   └── canvas_view.py                    ✎ herramientas, tiradores, nodos, ajuste magnético, medir
│   ├── ui/
│   │   ├── document_actions.py               ★ acciones de documento de la ventana principal
│   │   ├── main_window.py                    ✎ menús Organizar/Trayectoria/Capas, barra de opciones
│   │   ├── actions.py                        ✎ acciones de Fase 2 habilitadas
│   │   ├── icons.py                          ✎ muestras de color, «ajustar a la selección»
│   │   ├── dialogs/edit_dialogs.py           ★ ajustes de capa, matrices, girar, offset, texto, documento
│   │   └── panels/ layers_panel.py info_panels.py ✎ capas con acciones; propiedades editables
│   └── i18n/en.py                            ✎ +237 cadenas
└── tests/
    ├── test_path.py                          ★ geometría
    ├── test_operations.py                    ★ operaciones y deshacer
    ├── test_import_export.py                 ★ SVG/DXF ida y vuelta
    ├── test_project.py                       ★ .olsp, migración, autoguardado
    ├── test_toolpath_order.py                ★ orden de trayectorias
    └── test_ui_smoke.py                      ✎ +10 pruebas de interfaz de Fase 2
```

## 2. Código

Completo en `src/openlaser/`. El núcleo (`geometry`, `document`, `importers`, `exporters`,
`persistence`, `toolpath`, `gcode`) no depende de Qt y se prueba sin interfaz. Qt sólo se usa en
`canvas/qt_geometry.py` para las booleanas, offsets y texto a curvas (motor de trayectorias de Qt).
Las acciones de fases posteriores (redondear esquinas, puentes, texto sobre trayectoria, imagen,
vectorizar) siguen deshabilitadas con el aviso «disponible en la fase N».

## 3. Dependencias

Ninguna nueva: PySide6, pyserial, websockets (y pytest para desarrollo). Los lectores SVG/DXF son
propios, sin ezdxf/svgpathtools/shapely. No hace falta reinstalar: `install.bat` vuelve a instalar el
paquete en modo editable y basta con sustituir los archivos.

## 4. Instalación

Igual que en la Fase 1: copie los archivos sobre la carpeta del proyecto y, si es una carpeta nueva,
ejecute `install.bat` (Windows) o `./install.sh`.

## 5. Ejecución

`run.bat` (o `python -m openlaser`). Flujo típico:

1. **Dibujar**: `R` rectángulo, `E` elipse, `P` polígono/estrella, `L` línea/polilínea (clic a clic;
   doble clic o Intro termina; Ctrl+Intro cierra; Retroceso quita el último punto), `T` texto,
   `M` medir. Ctrl restringe a cuadrado/círculo y Mayús dibuja desde el centro; el polígono se dibuja
   desde el centro y el arrastre fija radio y giro.
   La barra **Opciones de herramienta** fija radio de esquina, lados, estrella y radio interior.
2. **Seleccionar y transformar** (`V`): clic, Mayús+clic, marco; tiradores para escalar (Mayús =
   proporcional) y tirador superior para girar (Mayús = pasos de 15°). Flechas: mover 1 mm
   (Mayús 10 mm). Panel **Propiedades**: posición, tamaño (con proporción), giro, capa y nombre.
3. **Nodos** (`N`): arrastrar nodos y manejadores Bézier, doble clic en un tramo inserta un nodo,
   Supr elimina el nodo activo.
4. **Capas**: panel «Capas y operaciones» (doble clic en el color, salida, visible, bloqueo, orden de
   ejecución) y diálogo **Ajustes de capa** con velocidad, potencia mín./máx., pasadas, modo,
   relleno, intervalo, ángulo, overscan, kerf, lead-in/out, Z, Air Assist, M3/M4, pausa, orden
   interior→exterior… Preajustes guardables y copia de ajustes entre capas.
5. **Organizar**: agrupar (Ctrl+G), alinear y distribuir, orden de apilado, booleanas (unir, restar,
   intersección, exclusión), combinar/separar, offset y compensación de kerf, matrices rectangular y
   circular, copias sobre trayectoria, voltear, girar, mover al origen o a la posición del láser.
6. **Trayectoria**: cerrar, unir y reparar, simplificar, eliminar nodos/objetos duplicados, invertir.
7. **Archivo**: Nuevo, Abrir (`.olsp` o `.json` de la Fase 1), Guardar/Guardar como, Abrir reciente,
   Importar SVG/DXF (también arrastrando al lienzo), Exportar SVG/DXF, Propiedades del documento,
   Resumen imprimible, autoguardado (cada 3 min) y Recuperar.
8. **Deshacer/Rehacer** (Ctrl+Z / Ctrl+Y) cubre todo lo anterior, incluidos los cambios de capa.

> Cambio de atajos: el jog por teclado pasa a **Alt+flechas** (Alt+RePág/AvPág para Z) porque las
> flechas solas mueven la selección. Las preferencias de la Fase 1 se migran automáticamente.

## 6. Pruebas automatizadas

`autoprueba.bat` (pytest + captura `docs\captura_fase2.png`) o `python -m pytest -q`:
**174 pruebas** (157 sin interfaz + 17 de interfaz, que se omiten si PySide6 no está instalado).

| Archivo | Qué comprueba |
|---|---|
| `test_path.py` | primitivas, arcos SVG→Bézier, aplanado, áreas con signo, contención, RDP, unión de tramos, serialización |
| `test_operations.py` | mover/girar/voltear/escalar, alinear, distribuir, igualar, orden, grupos, matrices, copias sobre trayectoria, combinar/separar, nodos, capas (crear, borrar moviendo objetos, copiar ajustes), bloqueo, deshacer de cada operación |
| `test_import_export.py` | SVG (unidades, viewBox, transform, path completo, arcos, use/defs, estilos, colores→capas, elementos no admitidos), DXF (LINE, LWPOLYLINE con bulge, CIRCLE, ARC, ELLIPSE, SPLINE, INSERT, unidades), ida y vuelta SVG y DXF |
| `test_project.py` | guardar/abrir `.olsp`, migración del `.json` de la Fase 1, archivos dañados o de versión futura, autoguardado |
| `test_toolpath_order.py` | interiores antes que exteriores, vecino más cercano, distancia de desplazamiento reducida, 3000 contornos ordenados en segundos (índice espacial) |
| `test_ui_smoke.py` (+10) | herramientas exclusivas, dibujar+deshacer+rehacer y título «•», redimensionar desde Propiedades, unión booleana y grupo, copiar/pegar/duplicar/borrar, añadir/borrar capa, guardar y abrir proyecto, importar SVG y deshacer, texto a curvas, offset |

## 7. Resultado esperado

* `autoprueba_resultado.txt` termina con `RESULTADO: TODAS LAS PRUEBAS SUPERADAS` (174 pasadas).
* La captura muestra la segunda fila de barras con **Opciones de herramienta**, los menús *Organizar*,
  *Trayectoria* y *Capas*, y el primer objeto del documento de demostración seleccionado con sus
  tiradores y los datos en *Propiedades*.
* Uso manual: al dibujar, el título muestra «•» (cambios sin guardar); al cerrar pregunta si guardar.
  Importar `examples\estrella_demo.svg` o `examples\piezas_demo.dxf` muestra un resumen con objetos,
  capas creadas por color y elementos no admitidos. La vista previa de corte respeta el orden
  interior→exterior y las pasadas de cada capa.

## 8. Limitaciones conocidas

* G-code sólo para capas en modo **Línea**; relleno, imagen, lead-in/out, perforación y PPI se guardan
  en la capa pero se generan en la Fase 3 (la interfaz lo indica).
* Las curvas se aplanan a 0,02 mm al generar G-code (no se emiten G2/G3).
* SVG: no se importan texto (conviértalo a trayectorias en el editor de origen), imágenes, máscaras,
  recortes ni CSS externo; se informa de cada elemento omitido. DXF: sólo ASCII; HATCH, TEXT/MTEXT,
  DIMENSION y sólidos 3D se informan como no admitidos.
* Las booleanas y el offset convierten las curvas en polilíneas finas (motor de Qt).
* Redondear esquinas, puentes/tabs y texto sobre trayectoria llegan en la Fase 3.
* La impresión del resumen usa el diálogo de vista previa de Qt (requiere un controlador de impresora
  o «Microsoft Print to PDF»).

## 9. Lista de comprobación de seguridad (Fase 2)

- [x] Ninguna operación de diseño envía nada a la máquina; sólo *Mover el láser a la selección* usa
      `G0` (sin láser) y respeta los límites y el estado (deshabilitada si no está en reposo).
- [x] G-code generado: `M5` antes y después de cada trayectoria, `G0` para desplazamientos, S limitado
      a `$30`/perfil y F al avance máximo; capas sin salida o ocultas no generan nada.
- [x] Air Assist (`M8`/`M9`) por capa y `M9` siempre al final; `M0` de pausa entre capas opcional.
- [x] Desplazamiento Z sólo si el perfil declara eje Z.
- [x] El análisis de límites se recalcula tras cada cambio del documento; *Iniciar* sigue pasando por la
      comprobación previa de la Fase 1.
- [x] Archivos importados: tamaño de miembros ZIP limitado, sin ejecución de scripts ni recursos
      externos en SVG, SVG con declaraciones `<!ENTITY>` rechazados.
- [x] Guardado atómico con copia `.bak`; autoguardado separado del proyecto.
- [x] Los valores de potencia y velocidad de las capas son de demostración: validar en la máquina.

## 10. Descripción de la interfaz resultante

* **Barra principal**: Nuevo, Abrir, Guardar, Importar, Exportar · Deshacer, Rehacer, Cortar,
  Copiar, Pegar, Eliminar · Vista previa, Frame, Iniciar, Pausa, Reanudar, Detener · Home,
  Conectar, Cámara, Rotary, Prueba de material, Configuración.
* **Barra de modificación**: zoom, ajustar a pantalla/selección, centrar · Alinear ▾ y Distribuir ▾
  (botones con menú desplegable) · Agrupar, Desagrupar · Unir, Restar, Intersección, Offset ·
  Matriz, Voltear, Girar.
* **Opciones de herramienta** (segunda fila): radio de esquina, lados/puntas, estrella, radio
  interior y lectura de la herramienta Medir (distancia, ΔX, ΔY, ángulo).
* **Barra vertical izquierda**: Seleccionar, Nodos, Línea, Rectángulo, Elipse, Polígono, Texto,
  (Imagen, Vectorizar: Fase 3), Medir — exclusivas, la activa resaltada.
* **Menús**: Archivo (con Abrir reciente), Editar, Dibujar, Organizar (Alinear, Distribuir, Orden,
  Booleanas, Transformar, Matrices y copias, Posición, Ajuste magnético), Trayectoria, Capas, Láser,
  Máquina, Cámara, Herramientas, Ventana, Ayuda.
* **Panel Capas y operaciones**: tabla con color, nombre, modo, velocidad, potencia, pasadas, salida,
  visible y bloqueo; botones Nueva, Eliminar, Subir, Bajar, Ajustes, Asignar selección, Copiar
  ajustes, Preajustes.
* **Panel Propiedades**: nombre, capa, X, Y, ancho, alto, mantener proporción, giro y resumen
  (trayectorias, nodos, abiertas, agrupados); campos deshabilitados sin selección.
* **Lienzo**: objetos con el color de su capa (punteado si la capa no tiene salida, relleno
  translúcido en capas de relleno), marco de selección con 8 tiradores y tirador de giro, nodos
  cuadrados y manejadores redondos en modo Nodos, guía de ajuste magnético.
