# Fase 3 — Entrega

Procesamiento ráster, tramados, vectorización, generación de G-code para todos los modos de capa
(Línea, Relleno, Relleno + Línea, Imagen) y simulación visual con diagnósticos. Incluye también el
ajuste de la tabla de capas pedido al cerrar la Fase 2.

## 1. Árbol de archivos (nuevos ★ y modificados ✎)

```
openlaser-studio/
├── autoprueba.bat / autoprueba.sh              ✎ instala NumPy si falta; captura → docs/captura_fase3.png
├── pyproject.toml / requirements.txt           ✎ versión 0.3.0, dependencia nueva: numpy
├── examples/
│   ├── degradado_tramado.png                   ★ imagen de prueba para tramados
│   ├── engranaje_vectorizar.png                ★ silueta de prueba para vectorizar
│   └── proyecto_demo.olsp                      ✎ incluye una imagen
├── docs/FASE3.md                               ★ este documento (y ARQUITECTURA.md, README.md, LICENCIAS.md ✎)
├── src/openlaser/
│   ├── image_processing/
│   │   ├── settings.py                         ★ ImageSettings (ajustes no destructivos) y métodos de tramado
│   │   ├── raster.py                           ★ ajustes, remuestreo sobre el marco, 10 tramados, Otsu, npz
│   │   └── trace.py                            ★ vectorización (marching squares, limpieza, curvas)
│   ├── toolpath/
│   │   ├── fill.py                             ★ barrido de relleno (par-impar, unión, ángulo)
│   │   └── cutpath.py                          ★ entrada/salida (lead-in/out) y puentes (tabs)
│   ├── gcode/generator.py                      ✎ todos los modos, perforación, cachés, tramos por capa
│   ├── simulation/
│   │   ├── timeline.py                         ★ línea temporal del trabajo (posición en cada instante)
│   │   └── checks.py                           ★ diagnósticos: límites, fuera de área, duplicados, abiertos, cruces
│   ├── document/
│   │   ├── model.py                            ✎ formato 3: ImageRef (imagen colocada), Document.images, demo con imagen
│   │   └── operations.py                       ✎ redondear esquinas, ajustes de imagen, filtros vector/imagen
│   ├── geometry/path.py                        ✎ round_corners; containment_depth con índice espacial
│   ├── persistence/project.py                  ✎ imágenes como recursos .npz en el .olsp
│   ├── importers/__init__.py                   ✎ filtros de imagen (PNG, JPEG, BMP, GIF, TIFF, WebP)
│   ├── exporters/__init__.py                   ✎ las imágenes no se exportan como vectores
│   ├── canvas/
│   │   ├── raster_items.py                     ★ NumPy ↔ QImage, vista previa procesada en el lienzo
│   │   ├── sim_item.py                         ★ dibujo progresivo de la simulación (potencia/velocidad/capa)
│   │   └── canvas_view.py                      ✎ imágenes en el lienzo, doble clic → ajustes, capa de simulación
│   ├── ui/
│   │   ├── dialogs/image_dialogs.py            ★ Ajustes de imagen y Vectorizar (vista previa, antes/después)
│   │   ├── dialogs/edit_dialogs.py             ✎ puentes en ajustes de capa y diálogo de puentes
│   │   ├── panels/simulation_panel.py          ★ panel de simulación y diagnóstico
│   │   ├── panels/layers_panel.py              ✎ tabla compacta (modo abreviado, columnas con icono)
│   │   ├── panels/job_panel.py                 ✎ botón Simular, análisis en segundo plano, avisos del generador
│   │   ├── panels/info_panels.py               ✎ datos de imagen en Propiedades
│   │   ├── document_actions.py                 ✎ insertar/ajustar/vectorizar imagen, redondear, puentes, optimizar, simular
│   │   ├── main_window.py                      ✎ análisis en hilo de fondo, panel Simulación, menús
│   │   ├── actions.py / icons.py / app.py      ✎ acciones e iconos nuevos; la captura muestra la simulación
│   └── i18n/en.py                              ✎ +146 cadenas
└── tests/
    ├── test_raster.py                          ★ ajustes, marco girado/espejo, 10 tramados, persistencia
    ├── test_trace.py                           ★ contornos, agujeros, ruido, esquinas y curvas
    ├── test_fill_image_gcode.py                ★ relleno, imagen, lead-in/out, puentes, perforación, redondeo
    ├── test_simulation.py                      ★ línea temporal y diagnósticos
    └── test_ui_smoke.py / test_gcode.py / …    ✎ +7 pruebas de interfaz; demo completa en el simulador
```

## 2. Código

Completo en `src/openlaser/`. El núcleo nuevo (`image_processing`, `toolpath`, `simulation`,
generador) sólo depende de NumPy y se prueba sin interfaz. La interfaz decodifica las imágenes con
Qt (no hace falta Pillow) y las pasa al núcleo como matrices.

## 3. Dependencias

Nueva: **NumPy ≥ 1.26** (BSD-3-Clause). `autoprueba.bat` la instala automáticamente si falta
(equivale a volver a ejecutar `install.bat`). Si `pip` no encuentra un paquete precompilado de NumPy
para su versión de Python, instale Python 3.13 y vuelva a ejecutar `install.bat`.

## 4. Instalación

Copie los archivos sobre la carpeta del proyecto y ejecute `autoprueba.bat` (o `install.bat`).

## 5. Ejecución

`run.bat`. Flujo típico:

1. **Imagen**: *Dibujar → Insertar imagen…* o *Archivo → Importar* (también arrastrando al lienzo).
   Se crea en la primera capa de modo Imagen (o en una nueva) y se ajusta al 80 % del área si no cabe.
   Mover, escalar, girar y voltear funcionan como con cualquier objeto.
2. **Ajustes de imagen** (doble clic sobre la imagen o Ctrl+Alt+I): tramado (Umbral, Atkinson,
   Floyd-Steinberg, Jarvis-Judice-Ninke, Stucki, Sierra, Burkes, Bayer ordenado, Halftone, Escala de
   grises con potencia variable), resolución en líneas/mm o DPI, brillo, contraste, gamma, exposición,
   nitidez, desenfoque, eliminación de fondo, invertir y recorte. La vista previa se recalcula al
   momento y se puede comparar **Antes / Después**. El lienzo muestra el resultado tramado.
3. **Vectorizar** (*Dibujar → Vectorizar imagen…*, con una imagen seleccionada o eligiendo un
   archivo): umbral manual u Otsu, invertir, supresión de ruido, área mínima, suavizado, tolerancia,
   ángulo de esquina, curvas Bézier, sólo contorno exterior, crear como Línea o Relleno y conservar o
   no la imagen. Vista previa **Antes / Después / Superpuesto** antes de aplicar.
4. **Capas**: Relleno (intervalo, ángulo, cruzado, bidireccional, overscan), Relleno + Línea, Línea con
   lead-in, lead-out (sobrecorte), perforación previa y **puentes** (*Trayectoria → Puentes / tabs…* o
   en Ajustes de capa). *Trayectoria → Redondear esquinas…* sustituye esquinas por arcos.
5. **Láser → Optimizar trayectorias** activa el orden interior→exterior y la optimización en todas las
   capas e informa del desplazamiento ahorrado.
6. **Láser → Simulación…** (F6, o *Simular…* en el panel Láser): reproducción con velocidad 1×–1000×,
   paso a paso, barra de posición, vista por capa, color por potencia, velocidad o capa, desplazamientos
   sin láser, flechas de dirección, tiempo/distancias/cambios de capa y la línea de G-code en curso. La
   lista de **Diagnóstico** señala movimientos fuera de límites, objetos fuera del área, imágenes sin
   píxeles, duplicados, trayectorias abiertas (y las que no se pueden rellenar) y cruces entre contornos
   de la misma capa; doble clic selecciona los objetos o lleva la simulación a esa línea. Cualquier
   cambio en el documento cierra la simulación (ya no correspondería).
7. El **análisis del trabajo** (panel Láser) se calcula ahora en segundo plano: el lienzo no se bloquea
   con imágenes grandes y el panel muestra «Calculando el trabajo…» mientras tanto.

## 6. Pruebas automatizadas

`autoprueba.bat` (instala NumPy si falta, pytest y captura `docs\captura_fase3.png`) o
`python -m pytest -q`: **220 pruebas** (196 sin interfaz + 24 de interfaz).

| Archivo | Qué comprueba |
|---|---|
| `test_raster.py` | ajustes (brillo, contraste, gamma, exposición, invertir, fondo, desenfoque, recorte), remuestreo con marco girado y en espejo, límite de resolución, los 10 tramados conservan el tono medio, Otsu, codificación npz y datos dañados, imagen sobre las transformaciones, proyecto con imágenes |
| `test_trace.py` | contornos cerrados y orientados, agujeros, sólo exterior, esquinas vivas y curvas, ruido y área mínima, invertir y umbral manual |
| `test_fill_image_gcode.py` | barrido par-impar y unión sin doble grabado, ángulo, overscan y sentido alterno, relleno cruzado, Relleno + Línea, imagen binaria y en grises (niveles de S), caché, lead-in/out, puentes, perforación, redondeo de esquinas; **ningún G0 con S≠0 activo** |
| `test_simulation.py` | línea temporal = análisis (distancia y tiempo), interpolación, arcos, comentarios de capa, diagnósticos de documento y de límites |
| `test_controller.py` ✎ | el documento de demostración completo (línea, relleno, corte e imagen: ~59 000 líneas) se ejecuta en el simulador GRBL sin errores ni emisión fuera de G1 |
| `test_ui_smoke.py` (+7) | insertar imagen (capa Imagen, vista previa, deshacer), ajustes con recorte que conserva la escala, vectorizar, redondear y puentes, simulación (reproducir, pasos, colores, vista por capa, se cierra al editar), análisis en segundo plano, tabla de capas compacta |

## 7. Resultado esperado

* `autoprueba_resultado.txt`: versiones de Python, PySide6 y NumPy, `220 passed`, código de salida 0 y
  `RESULTADO: TODAS LAS PRUEBAS SUPERADAS`.
* La captura muestra el panel **Simulación** (abajo, junto a la consola) con el trabajo completo de la
  demostración coloreado por potencia sobre el lienzo, la imagen tramada en su marco y la lista de
  diagnóstico.
* En la tabla de capas caben todas las columnas con el ancho por defecto: el modo aparece abreviado
  («Línea», «Relleno», «R+Lín.», «Imagen»; nombre completo en el desplegable y el tooltip) y las
  columnas de salida, visible y bloqueo tienen iconos.

## 8. Limitaciones conocidas

* Las imágenes se graban en filas horizontales (sin ángulo de barrido para imágenes) con píxeles
  cuadrados; el ángulo y el cruzado se aplican sólo a los rellenos vectoriales.
* La vista previa del lienzo y del diálogo se limita a 1200 / 520 píxeles; el G-code se genera a la
  resolución completa.
* El tiempo estimado ignora la aceleración; en grabados ráster con overscan corto puede quedarse
  corto. La simulación usa el mismo modelo.
* Puentes, entrada/salida y perforación sólo en contornos de modo Línea; la perforación usa M3 durante
  la pausa (en M4 la potencia a velocidad cero es nula) y se avisa.
* GRBL no admite frecuencia/PPI: el valor se guarda pero no se emite.
* Al copiar una imagen a otro proyecto no se copian sus píxeles (sólo dentro del mismo proyecto).
* Texto sobre trayectoria pasa a la Fase 4. Las imágenes no se exportan a SVG/DXF.
* La vectorización trabaja como máximo a 1600 píxeles de lado (las imágenes mayores se reducen).

## 9. Lista de comprobación de seguridad (Fase 3)

- [x] Todo G0 va precedido de un `S0` modal: aunque el controlador no esté en modo láser ($32=0) no
      hay emisión en desplazamientos rápidos (comprobado en pruebas para línea, relleno e imagen).
- [x] Cada fila de relleno o imagen termina con un tramo `S0`; las filas en blanco no se recorren.
- [x] S limitado a `$30`/perfil y F al avance máximo en todos los modos; la potencia de imagen va de
      la mínima a la máxima de la capa y los píxeles blancos son `S0`.
- [x] La perforación previa (único caso con láser encendido sin movimiento) sólo ocurre si la capa lo
      configura explícitamente, y el generador lo avisa.
- [x] El análisis de límites y la comprobación previa de la Fase 1 cubren también los recorridos de
      relleno, imagen y overscan; la simulación marca los movimientos fuera de límites.
- [x] Resolución de imagen limitada (12 Mpx de salida, 4000 px de lado al importar) para no agotar la
      memoria; recursos de proyecto con tamaño máximo y sin `pickle`.
- [x] El trabajo completo de la demostración se ejecuta en el simulador sin errores ni láser activo
      al terminar.
- [x] Los valores de potencia y velocidad de la demostración son ficticios: validar en la máquina.

## 10. Descripción de la interfaz resultante

* **Barra principal**: se añade **Simular** (F6) junto a Vista previa.
* **Menú Dibujar**: Insertar imagen…, Ajustes de imagen… (Ctrl+Alt+I), Vectorizar imagen….
* **Menú Trayectoria**: Redondear esquinas…, Puentes / tabs… (habilitados).
* **Menú Láser**: Simulación…, Optimizar trayectorias.
* **Panel Simulación** (pestaña junto a Consola GRBL): fila de controles (Generar, ⏮ ◀ Reproducir ▶ ⏭,
  velocidad), barra de posición con tiempo transcurrido/total, Capa (todas o una), Color por
  (Potencia / Velocidad / Capa) con leyenda de color, casillas Desplazamientos y Dirección, resumen
  (tiempo, corte, desplazamiento, cambios de capa, S y avance máximos), línea de G-code actual y, a la
  derecha, la lista de Diagnóstico con iconos ⛔ ⚠ ℹ.
* **Lienzo**: la imagen se dibuja tramada dentro de su marco discontinuo (más tenue si la capa no tiene
  salida); durante la simulación el recorrido aparece progresivamente con el cabezal (círculo rojo con
  cruz), flechas de dirección y desplazamientos discontinuos grises.
* **Ajustes de imagen**: columna de controles (Salida, Ajustes con deslizadores, Recorte) y vista
  previa a la derecha con Antes / Después y la resolución de salida.
* **Vectorizar**: parámetros a la izquierda y vista previa Antes / Después / Superpuesto con el
  número de contornos, nodos y el umbral usado.
* **Panel Láser**: botón Simular… bajo el análisis; el análisis incluye los avisos del generador.
* **Propiedades**: para una imagen, tamaño en píxeles, DPI y método de tramado.
