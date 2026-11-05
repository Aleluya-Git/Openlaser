# Fase 4 — Entrega

Cámara (calibración de lente y de área, superposición), rotary y corrección cilíndrica, biblioteca de
materiales, asistentes de prueba (13 tipos), Print and Cut, Center Finder, plugins, y lo que había
quedado pendiente: importación PDF/AI, texto sobre trayectoria, biblioteca de formas y panel de
historial.

## 1. Árbol de archivos (nuevos ★ y modificados ✎)

```
openlaser-studio/
├── autoprueba.bat / .sh, README.md             ✎ captura → docs/captura_fase4.png
├── pyproject.toml                              ✎ 0.4.0; extra opcional [opencv]
├── examples/plugins/ejemplo_marco/             ★ plugin de ejemplo (plugin.json + plugin.py)
├── docs/FASE4.md, ARQUITECTURA.md, LICENCIAS.md ★/✎
├── src/openlaser/
│   ├── camera/
│   │   ├── calibration.py                      ★ lente radial, homografía, precisión, perfiles, cámara simulada
│   │   └── lens.py                             ★ calibración con tablero (sólo si OpenCV está instalado)
│   ├── devices/
│   │   ├── rotary.py                           ★ rodillo/mandril, eje Y o A, corrección cilíndrica
│   │   ├── sd_upload.py                        ★ envío a la tarjeta SD por HTTP (FluidNC / ESP3D)
│   │   └── profile.py                          ✎ campos rotary, cylinder y datos del envío a la SD
│   ├── image_processing/raster.py, settings.py ✎ ángulo de barrido de las imágenes
│   ├── gcode/generator.py                      ✎ filas de imagen en la dirección del barrido
│   ├── materials/
│   │   ├── library.py                          ★ biblioteca de materiales (JSON, validación, fotos)
│   │   └── test_patterns.py                    ★ 13 generadores de pruebas con límites del perfil
│   ├── geometry/
│   │   ├── stroke_font.py                      ★ fuente de trazo para etiquetas (sin Qt)
│   │   ├── registration.py                     ★ Print and Cut (2 o 3 marcas) y Center Finder
│   │   └── shape_library.py                    ★ formas paramétricas y texto sobre trayectoria
│   ├── importers/pdf.py, __init__.py           ★/✎ PDF vectorial y AI compatible con PDF
│   ├── plugins/manager.py                      ★ descubrimiento, activación y API de plugins
│   ├── document/commands.py                    ✎ historial (lista de pasos e ir a un paso)
│   ├── persistence/settings.py                 ✎ cámara, plugins activos, últimos parámetros de prueba
│   ├── canvas/canvas_view.py                   ✎ superposición de cámara con perspectiva
│   └── ui/
│       ├── phase4_actions.py                   ★ acciones de la Fase 4 en la ventana principal
│       ├── camera_source.py                    ★ cámaras reales (Qt Multimedia) y simulada
│       ├── panels/camera_panel.py              ★ panel Cámara (sustituye al provisional)
│       ├── panels/materials_panel.py           ★ panel Biblioteca de materiales y editor de entradas
│       ├── panels/history_panel.py             ★ panel Historial
│       ├── dialogs/test_wizard.py              ★ asistente de pruebas con vista previa
│       ├── dialogs/camera_dialogs.py           ★ calibrar lente y área
│       ├── dialogs/machine_dialogs.py          ★ rotary, Print and Cut, Center Finder, plugins
│       ├── dialogs/shape_dialogs.py            ★ biblioteca de formas, texto sobre trayectoria
│       └── main_window.py, actions.py, app.py  ✎ menús, paneles, rotary en el trabajo
└── tests/
    ├── test_phase4_core.py                     ★ 28 pruebas del núcleo
    ├── test_sd_upload.py                       ★ envío a la SD contra un servidor HTTP local
    ├── test_fill_image_gcode.py                ✎ grabado de imágenes con ángulo
    └── test_ui_smoke.py                        ✎ +13 pruebas de interfaz
```

## 2. Código

Completo en `src/openlaser/`. Todo el cálculo (rotary, calibración, pruebas, materiales, PDF,
registro, plugins) está en el núcleo, sin Qt, y se prueba aparte. La captura de cámaras usa Qt
Multimedia (incluido en PySide6); hay una **cámara simulada** para la demostración y las pruebas.

## 3. Dependencias

Ninguna obligatoria nueva. Opcional: `pip install -e ".[opencv]"` para calibrar la lente con un
tablero de ajedrez; sin OpenCV la lente se calibra marcando bordes rectos o a mano.

## 4. Instalación

Copie los archivos y ejecute `autoprueba.bat`. No hace falta reinstalar.

## 5. Ejecución

* **Biblioteca de materiales** (panel junto a Capas, o *Herramientas → Biblioteca de materiales*):
  buscar (sin distinguir acentos), filtrar por operación, «sólo validados» y «sólo esta máquina»;
  crear, editar, duplicar, eliminar, importar/exportar; cada entrada guarda material, variante,
  espesor, operación, velocidad, potencias, pasadas, intervalo, Air Assist, frecuencia, máquina,
  módulo, fecha de validación, resultado, notas y fotografía. *Aplicar a la capa* copia los valores a
  la capa actual (pide confirmación si no está validada); *Guardar capa…* crea una entrada. Las
  entradas de demostración se marcan «DEMO».
* **Pruebas** (*Herramientas → Pruebas*, y *Láser → Prueba de materiales/enfoque/intervalo*):
  matriz velocidad/potencia, matriz potencia/pasadas, intervalo, enfoque (eje Z o rampa), potencia
  mínima, grabado, corte, kerf, escala, precisión, círculos, backlash y rotary. El asistente muestra
  los parámetros, una vista previa con el tamaño y los ajustes aplicados por los límites, y exige
  marcar «He revisado los parámetros…». La prueba se añade como capas y formas **editables** (con
  etiquetas en una capa propia) y se graba con el flujo normal: Vista previa → Iniciar → comprobación
  previa.
* **Cámara** (panel Cámara): elegir dispositivo (o la cámara simulada), *Capturar*, actualización
  periódica, superposición sobre el lienzo con opacidad, *Marcadores de calibración* (cruces en
  posiciones conocidas que se graban a baja potencia), *Calibrar lente…* y *Calibrar área…* (clic en
  cada marcador; se ajusta solo a su centro). Se muestra la precisión estimada. El perfil de cámara
  se guarda asociado al dispositivo y a la máquina.
* **Rotary** (*Láser → Configuración rotativa…*): rodillos o mandril, eje Y o A, diámetros, distancia
  o pasos por vuelta, relación de transmisión, invertir, prueba de rotación (una vuelta con el láser
  apagado). Activar o desactivar el rotary en Y pide confirmación con las precauciones. La barra de
  estado muestra **ROTARY** mientras está activo. Pestaña *Corrección cilíndrica* para cilindros
  tumbados sin rotary.
* **Print and Cut** (*Láser*): seleccione 2 o 3 marcas del diseño, lleve el láser a cada marca impresa
  y pulse *Registrar*; *Aplicar* mueve, gira y escala el documento (deshacible).
* **Center Finder** (*Láser*): tres puntos del borde → centro y diámetro; *Ir al centro* (G0, láser
  apagado), *Fijar origen aquí*, *Centrar la selección*.
* **Plugins** (*Editar → Plugins…* o *Herramientas → Plugins*): lista, activar/desactivar, abrir la
  carpeta. Copie `examples/plugins/ejemplo_marco` a la carpeta de plugins para probarlo.
* **Importar PDF/AI** (*Archivo → Importar*), **Texto sobre trayectoria** (seleccione la guía),
  **Biblioteca de formas** y panel **Historial** (*Ventana*).
* **Ángulo de barrido de imágenes** (*Ajustes de imagen → Salida → Ángulo de barrido*, −90…90°): las
  líneas de grabado y el tramado siguen esa dirección (0° horizontal, 90° vertical, 45° diagonal).
  Útil para materiales con veta o para evitar marcas paralelas al eje más lento.
* **Enviar a la tarjeta SD** (*Archivo → Enviar a la tarjeta SD…*): prepara el trabajo igual que
  *Guardar G-code* y lo sube por HTTP a un controlador FluidNC (`/upload`) o ESP3D 3.x
  (`/sdfiles`). Dirección, puerto, ruta, carpeta y nombre (se limpian caracteres no válidos);
  *Probar conexión* pide el listado sin enviar nada. Muestra tamaño, tiempo, potencia máxima y los
  avisos (fuera del área con el origen actual, rotary, líneas no reconocidas). Exige marcar
  «Entiendo que el trabajo se ejecutará fuera de la supervisión de OpenLaser». Los datos se
  recuerdan en el perfil de la máquina.

## 6. Pruebas automatizadas

**270 pruebas** (233 sin interfaz + 37 de interfaz).

| Archivo | Qué comprueba |
|---|---|
| `test_phase4_core.py` | factor del rotary (rodillo, mandril, transmisión, pasos), reescritura Y/A y rechazo de arcos/relativas, corrección cilíndrica con subdivisión, transformación sobre un trabajo real sin potencia en G0; Print and Cut con 2 y 3 marcas, Center Finder; biblioteca de materiales (búsqueda, validación, duplicar, guardar/cargar, capas, formato de la Fase 1); **las 13 pruebas** generan geometría dentro de los límites, con colores únicos y G-code válido; límites y rechazo fuera del área; fuente de trazo, formas, texto sobre trayectoria; historial; lente (ida y vuelta, ajuste de k1 con líneas), homografía y precisión con la cámara simulada, refinado de marcadores, perfiles; OpenCV opcional; plugins (sin activar no se ejecutan, API incompatible, ruta fuera de la carpeta, error al cargar, plugin de ejemplo); PDF (Flate y sin comprimir, `cm`, colores, texto informado, AI no compatible) |
| `test_sd_upload.py` | nombres seguros, validación de dirección/puerto/carpeta (sin `..`), apagado final `M5` garantizado, subida y listado contra un servidor local que imita `/upload` (campos `path`, `<archivo>S`, `myfile[]`), errores HTTP, estado de error, sin conexión, trabajo vacío; nunca se envía una orden de ejecución |
| `test_fill_image_gcode.py` (+4) | barrido a 90°, 45° y −30°: todas las líneas que graban siguen el ángulo, misma longitud grabada, dentro de la imagen y sin potencia en G0; el ajuste se guarda y se limita |
| `test_ui_smoke.py` (+13) | tabla de materiales compacta con parámetros en el detalle; ángulo de barrido desde el diálogo; envío a la SD de principio a fin (botón bloqueado sin confirmación, probar conexión, archivo recibido, datos guardados en el perfil); | aplicar material y guardar capa, asistente de pruebas (añadir y deshacer, bloqueo fuera del área), cámara simulada: marcadores + calibración + superposición y opacidad, rotary aplicado al trabajo con indicador, Print and Cut, Center Finder, plugin de ejemplo deshacible, biblioteca de formas + texto sobre trayectoria + historial, importar PDF |

## 7. Resultado esperado

* `RESULTADO: TODAS LAS PRUEBAS SUPERADAS` con 270 pruebas y códigos de salida 0.
* La captura muestra el panel **Biblioteca de materiales** con las entradas DEMO y el aviso de
  validación, además de la simulación de la Fase 3. La tabla tiene cuatro columnas
  (Material, mm, Operación, ✔) y los nombres ya no se recortan; velocidad, potencia y pasadas
  aparecen en el detalle y en la información emergente.

## 8. Limitaciones conocidas

* Calibración de lente sin OpenCV: modelo radial k1/k2 centrado (suficiente para lentes de cámaras
  web); sin tangencial.
* La precisión de la cámara depende de la altura del material: la calibración vale para la altura a
  la que se grabaron los marcadores.
* Rotary: el G-code con arcos (G2/G3) o en pulgadas no se transforma (el generador propio no los usa);
  los archivos G-code cargados se envían tal cual.
* Print and Cut aplica la transformación a todo el documento desbloqueado.
* Plugins: desactivarlos requiere reiniciar; la API 1 no da acceso al controlador de la máquina.
* PDF: sólo la primera página; texto, imágenes, sombreados y recortes no se importan (se informa).
* **Envío a la SD**: los manejadores web de FluidNC/ESP3D no tienen una especificación formal; los
  nombres de los campos se han tomado de la documentación de ESP3D y pueden variar entre versiones.
  Sólo se ha probado contra un servidor que los imita, **no contra un controlador real**: compruebe
  el primer envío desde la WebUI. Sin HTTPS ni contraseña (FluidNC sin autenticación). GRBL por
  USB no tiene almacenamiento: con él la opción falla al conectar.
* **Ángulo de barrido**: la vista previa de la imagen en el lienzo muestra el tramado sin girar; la
  vista previa del trabajo (Vista previa / simulación) sí muestra las líneas reales. Con ángulos
  distintos de 0/90° el trabajo es más largo (más filas cortas y más overscan).

## 9. Lista de comprobación de seguridad (Fase 4)

- [x] Ninguna prueba se ejecuta sola: se añade al documento y pasa por vista previa y comprobación
      previa. El asistente exige confirmar que los parámetros se validarán en la máquina.
- [x] Potencia 0–100 %, avance ≤ máximo del perfil y Z dentro de su rango en todas las pruebas; se
      informa de cada ajuste. Pruebas fuera del área: no se pueden añadir.
- [x] Biblioteca: avisos permanentes de que ningún valor es universal; las entradas DEMO y no
      validadas piden confirmación al aplicarse.
- [x] Cámara: avisos de que no es un sistema de seguridad; la calibración sólo cambia la vista.
- [x] Rotary: confirmación con precauciones al cambiar entre Y y rotary, indicador permanente,
      prueba de rotación con el láser apagado y confirmación, rotary y cilindro no simultáneos.
- [x] Print and Cut y Center Finder sólo mueven con G0 (láser apagado) y por petición del usuario.
- [x] Plugins: nunca se activan solos; la API no permite mover ni encender el láser; un plugin
      defectuoso no impide arrancar.
- [x] Las transformaciones del rotary conservan `S0` antes de cada G0 (comprobado en pruebas).
- [x] Imágenes con ángulo: mismo emisor de filas que los rellenos (`S0` en G0, aceleración y salida
      con `S0`), comprobado a 90°, 45° y −30°.
- [x] Envío a la SD: nunca pone en marcha el trabajo (sin órdenes de ejecución, comprobado); exige
      confirmar que se ejecutará sin supervisión; avisa si sale del área o es para el rotary; el
      archivo termina siempre con `M5` y lleva en la cabecera la máquina para la que se generó.

## 10. Descripción de la interfaz resultante

* **Paneles nuevos** (pestañas junto a Capas y Propiedades): *Biblioteca de materiales* (búsqueda,
  filtros, tabla Material/Espesor/Operación/mm/min/%/×/Resultado, botones, detalle y aviso), *Cámara*
  (dispositivo, miniatura, capturar, actualización, superposición y opacidad, calibrar lente/área,
  marcadores, estado y aviso), *Historial* (oculto por defecto).
* **Menú Herramientas**: Pruebas ▸ (13 pruebas en 4 grupos), Prueba de materiales/enfoque/intervalo,
  Biblioteca de materiales, Biblioteca de formas, Texto sobre trayectoria, Plugins ▸, Gestión de
  plugins.
* **Menú Láser**: Print and Cut, Center Finder, Configuración rotativa, Corrección cilíndrica (todos
  activos). **Menú Cámara**: Detectar, Calibrar lente, Calibrar área, Capturar.
* **Menú Archivo**: *Enviar a la tarjeta SD…* abre un diálogo con dirección, puerto, ruta de
  subida (FluidNC/ESP3D), carpeta, nombre, resumen del trabajo, avisos en ámbar, la casilla de
  confirmación, *Probar conexión* y *Enviar* (desactivado hasta confirmar).
* **Ajustes de imagen**: campo *Ángulo de barrido* bajo *Ángulo halftone*.
* **Barra de estado**: etiqueta ámbar **ROTARY …** o **CILINDRO …** cuando están activos.
* **Lienzo**: la imagen de la cámara corregida en perspectiva aparece bajo los objetos.
