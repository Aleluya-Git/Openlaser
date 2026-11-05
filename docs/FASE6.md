# Fase 6 — Entrega: Marlin y Smoothieware (experimental)

Marlin y Smoothieware pasan de «previstos» a **disponibles (experimental)**. No hablan el protocolo
de flujo de GRBL. La aplicación les envía una línea y espera su `ok`, deduce o pide el estado, y
traduce el G-code para que el láser se comporte igual que con GRBL.

## 1. Árbol de archivos (nuevos ★ y modificados ✎)

```
openlaser-studio/
├── autoprueba.bat / .sh, README.md             ✎ captura → docs/captura_fase6.png
├── pyproject.toml, src/openlaser/__init__.py   ✎ versión 0.6.0
├── docs/FASE6.md, ARQUITECTURA.md (§11)        ★/✎
├── src/openlaser/
│   ├── machine/
│   │   ├── host_protocols.py                   ★ respuestas de Marlin/Smoothieware y traducción del G-code
│   │   ├── host_controller.py                  ★ controlador envío-respuesta (hereda del de GRBL)
│   │   ├── factory.py                          ★ controlador según el firmware del perfil
│   │   ├── controller.py                       ✎ ganchos (_enqueue_jog, _estop_bytes) y detección cruzada
│   │   ├── command_queue.py                    ✎ max_in_flight y rebobinado del trabajo
│   │   └── dialects.py                         ✎ entradas Marlin y Smoothieware
│   ├── simulator/
│   │   ├── marlin_simulator.py                 ★ simulador de Marlin 2.x con LASER_FEATURE
│   │   └── grbl_simulator.py                   ✎ variante «smoothie»
│   ├── devices/profile.py, transport.py        ✎ fw_s_max; make_simulator
│   ├── plugins/controllers.py                  ✎ Marlin y Smoothieware disponibles
│   ├── i18n/en.py                              ✎ traducciones
│   └── ui/
│       ├── main_window.py                      ✎ controlador y simulador según el perfil al conectar
│       ├── dialogs/config_dialogs.py           ✎ «S máximo del firmware»
│       ├── actions.py, app.py                  ✎ CURRENT_PHASE = 6; captura con el simulador Marlin
└── tests/
    ├── test_host_firmwares.py                  ★ 20 pruebas (núcleo y simuladores de Marlin y Smoothieware)
    └── test_ui_smoke.py                        ✎ +2 pruebas de interfaz
```

## 2. Código

Completo en `src/openlaser/`. Las reglas de cada firmware (respuestas y traducción) están en
`machine/host_protocols.py`, sin Qt. El controlador `HostController` reutiliza el hilo, la cola,
el trabajo y todas las comprobaciones de seguridad del controlador GRBL.

## 3. Dependencias

Ninguna nueva.

## 4. Instalación

Copie los archivos y ejecute `autoprueba.bat`.

## 5. Ejecución

* *Máquina → Dispositivos…* → **Firmware**: Marlin o Smoothieware. Con la conexión *Simulador*, el
  simulador imita ese firmware. El campo **S máximo del firmware** sólo hace falta si el firmware
  no usa el valor por defecto (Marlin 255; Smoothieware 1; Marlin con `CUTTER_POWER_UNIT PERCENT`:
  100).
* Al conectar, un aviso explica las diferencias. La barra de estado muestra «Marlin · …» o
  «Smoothieware · …».
* **Marlin** (requiere `LASER_FEATURE`; recomendado `EMERGENCY_PARSER`):
  * Estado y posición: los deduce la aplicación con `M114` y `M400`.
  * Trabajo: cada `M3`/`M4` pasa a modo en línea (`M3 I`); cada `G0` pasa a `G1 … S0` al avance
    rápido del perfil, reponiendo después el avance y la potencia; la potencia se escala a 0–255.
  * *Stop*: `M410` + `M5`. *Parada de software*: `M112`; después hay que reiniciar la placa.
  * Si `M115` indica que falta `EMERGENCY_PARSER`, aparece un aviso.
  * Disparo de enfoque: `M3 S…` → `G4 P<ms>` → `M5`, con vigilante.
* **Smoothieware** (`grbl_mode true`):
  * Estado: con `?`. Home con `$H`; desbloqueo con `$X`.
  * Potencia: se escala a 0–1, y se añade `S0` antes del primer `G1`. Si no, Smoothie usaría su
    potencia por defecto.
  * Jog y frame: siempre `G0` (Smoothie nunca emite en G0).
  * *Stop*: Ctrl-X. Deja el controlador en alarma hasta *Desbloquear*.
  * El disparo de enfoque parado no está disponible: Smoothie sólo emite moviéndose.
* **Pausa** (los dos firmwares): se deja de enviar. La máquina termina las pocas líneas ya enviadas
  y recibe `M5`. *Reanudar* repone el último `M3`/`M4` y continúa sin perder líneas.
* No hay ajustes en tiempo real (overrides) ni lectura de parámetros desde el diálogo. Use la
  consola: `M503` en Marlin.
* Si el controlador es de otro firmware, la conexión se corta con un mensaje que lo indica. Esto
  vale también al revés: un perfil GRBL conectado a Marlin o Smoothie.

## 6. Pruebas automatizadas

**309 pruebas** (267 sin interfaz + 42 de interfaz).

| Archivo | Qué comprueba |
|---|---|
| `test_host_firmwares.py` | traducción para Marlin (sin G0, modo en línea, F y S repuestos, G4 en ms) y para Smoothieware (S 0–1, `S0` antes del primer G1); analizadores de respuestas; fábrica y adaptadores; **con cada simulador**: conectar, Home, jog y frame sin emisión; trabajo con el láser sólo en avances (Marlin: una línea en la cola como máximo, sin desbordamientos); pausa (no se envía nada, láser apagado) y reanudación sin perder líneas; Stop (Marlin `M410`; Smoothie alarma y `$X`); disparo de enfoque (Marlin parado y con fin; Smoothie rechazado); `M112` en Marlin; aviso sin `EMERGENCY_PARSER`; **firmware equivocado** en los dos sentidos → desconexión con indicación |
| `test_ui_smoke.py` (+2) | conectar con Marlin y con Smoothieware desde la ventana (controlador y simulador correctos, barra de estado, Home, jog sin emisión) y volver a GRBL |

## 7. Resultado esperado

* `RESULTADO: TODAS LAS PRUEBAS SUPERADAS` con 309 pruebas y códigos de salida 0.
* `docs/captura_fase6.png`: conexión al simulador **Marlin**. La barra de estado muestra
  «Marlin · simulador» y la consola, las órdenes `G28XY`, `M400`, `G92X0Y0`, `M114` y el jog.

## 8. Limitaciones conocidas

* **Experimental y sin probar con hardware real.** Las reglas proceden de la documentación de
  Marlin y Smoothieware. Cada compilación de Marlin activa opciones distintas. Pruebe primero sin
  láser, o con la potencia al mínimo, y con la seta física a mano.
* **Marlin**:
  * Necesita `LASER_FEATURE` con potencia en línea; sin ella, la potencia de los `G1` no cambia (el
    láser quedaría a 0 y no grabaría).
  * El estado es deducido: la posición durante el trabajo es la planificada, no la real.
  * Sin `EMERGENCY_PARSER`, Stop y la parada de software tardan lo que tarde la línea en curso.
  * No se usan números de línea ni sumas de comprobación (`N…*cs`).
  * Tras `G28` se fija `G92 X0 Y0`: se supone que el homing está en el origen de máquina.
* **Smoothieware**:
  * `!`/`~` no se usan porque están «en desarrollo» en Smoothie.
  * Stop deja el controlador en alarma.
  * Con la compensación proporcional a la velocidad de Smoothie, la potencia real depende del
    avance.
* La pausa no es inmediata: se terminan los movimientos ya enviados (una línea más los que haya en
  el planificador).
* Sin overrides en tiempo real, sin diálogo de parámetros y sin envío a la SD.

## 9. Lista de comprobación de seguridad (Fase 6)

- [x] Nada enciende el láser al conectar, hacer Home, mover, hacer frame o ir a un punto (comprobado
      con los dos simuladores, incluido el comportamiento peligroso de Smoothie con G1 sin S).
- [x] Marlin: ningún `G0` llega al controlador dentro de un trabajo; los rápidos van con `S0`
      explícito. El simulador emitiría en un `G0` en modo en línea, y las pruebas lo excluyen.
- [x] Smoothieware: `S0` explícito antes del primer `G1`; jog y frame sólo con `G0`.
- [x] Pausa: no se envía nada más, `M5` al vaciarse y reanudación sin pérdida (comprobado).
- [x] Stop, desconexión con actividad y vigilante del disparo: `M410`+`M5` / Ctrl-X / `M112`.
- [x] Envío-respuesta estricto: nunca más de una línea sin `ok`. La conexión espera a que dejen de
      llegar respuestas de las sondas, para no desfasar los `ok`.
- [x] Firmware equivocado: desconexión con mensaje claro, sin enviar movimientos.
- [x] Avisos permanentes: «experimental», falta de `EMERGENCY_PARSER` y reinicio tras `M112`.

## 10. Descripción de la interfaz resultante

* **Gestor de dispositivos**: el campo **Firmware** ofrece GRBL 1.1+, grblHAL, FluidNC, Marlin y
  Smoothieware, y hay un campo nuevo, **S máximo del firmware** («predeterminado» = 0).
* **Barra de estado**: «Marlin · simulador» o «Smoothieware · …».
* **Avisos**:
  * Al conectar, en azul: «Marlin (experimental): una línea cada vez; Pausa deja de enviar…».
  * En ámbar: falta `EMERGENCY_PARSER`.
  * En rojo: controlador detenido (`M112`/Ctrl-X) con qué hacer.
* **Consola**:
  * Marlin: `M114` periódicos y respuestas `X:… Y:…`.
  * Smoothieware: `?` y su informe.
  * «?» escrito en la consola de Marlin envía `M114`.
* **Gestión de plugins**: Marlin y Smoothieware aparecen como disponibles, con la nota
  «Experimental».

## Mejoras posteriores (rendimiento de imágenes y panel Láser)

* **Imágenes grandes**. Ejemplo medido: una fotografía de 320 × 213 mm a 10 líneas/mm, tramada con
  Floyd–Steinberg, que genera 4,1 millones de líneas de G-code.
  * Antes: generar 29 s y analizar 37 s. Además, *Vista previa*, *Frame*, *Iniciar* y *Guardar
    G-code* repetían todo el cálculo en primer plano.
  * Ahora: generar ~4 s y analizar ~8 s, en segundo plano. Al editar, el trabajo se calcula una
    sola vez y esas acciones reutilizan el resultado (o esperan al cálculo en curso).
  * Cómo: las filas de la imagen se extraen con NumPy; si la imagen no está girada, cada línea
    `G1X…Y…S…` se monta con coordenadas ya formateadas (mismo resultado exacto, comprobado en
    pruebas); el análisis tiene una vía rápida para las líneas ráster y calcula la envolvente con
    NumPy.
* **Panel Láser**: se adapta al ancho del panel, sin barra de desplazamiento horizontal.
  * El texto largo del origen ya no fija el ancho («Origen de usuario»; la explicación está en la
    información emergente).
  * Los botones se reparten el espacio disponible.
* Pruebas nuevas: equivalencia de la emisión rápida y la general, equivalencia del análisis rápido y
  el general, tiempo de una fotografía grande, caché del trabajo y ancho del panel.

### Imagen calculada una vez (mover sin recalcular)

* La imagen tramada se guarda en memoria **independientemente de su posición**. La rejilla de salida se
  ancla al propio marco, así que al moverla se reutilizan los píxeles y sus tramos, y sólo cambia el origen.
  Girar, escalar o cambiar los ajustes sí vuelve a tramar.
* El trabajo ya no crea el texto G-code de las imágenes para analizarlo. Cada imagen es un bloque
  (`RasterBlock`) que el análisis mide con NumPy; el texto se crea sólo al enviar, guardar o ver la
  vista previa, y queda guardado.
* Medido con la fotografía de 320 × 213 mm (4,1 millones de líneas):

  | Momento | Antes | Ahora |
  |---|---|---|
  | Primera vez (análisis) | ~12 s | ~3 s |
  | Tras mover la imagen | ~12 s | ~0,5 s |
  | Crear el texto (vista previa, enviar…) | — | ~1,5 s, una sola vez por posición |

* Pruebas: el análisis por bloques coincide con el del texto (líneas, distancias, tiempos por línea,
  límites, contorno); mover no vuelve a tramar y el G-code es idéntico al recalculado (también con
  ángulo de barrido).


### Simulación de imágenes sin «cuadros»

* Antes cada tramo de una imagen era una línea de 1,6 px en la simulación. Con miles de filas a 0,1 mm
  las líneas se solapaban y la imagen aparecía como bloques de color macizos. Además, a partir de
  2 millones de movimientos la simulación se cortaba, y sólo se veía la parte de arriba.
* Ahora cada fila de una imagen es un único movimiento en la línea temporal: 3–4 movimientos por fila
  en lugar de uno por tramo. Lo grabado se dibuja como **imagen quemada píxel a píxel**, con los
  colores del modo elegido (potencia, velocidad o capa), y se revela fila a fila al reproducir.
* Tiempo, distancia de corte y desplazamiento son los mismos que recorriendo todas las líneas
  (comprobado en pruebas).
* Fotografía de 320 × 213 mm: la línea temporal pasa de 2 millones de movimientos (cortada) a 8 520,
  en 0,2 s.
