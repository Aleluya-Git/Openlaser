# Fase 1 — Entrega

Producto mínimo completamente ejecutable: interfaz principal, control de movimiento, simulador GRBL,
consola, estado de máquina, conexión/Home/Frame/Inicio/Pausa/Stop, sistema de seguridad y pruebas.
La arquitectura, la máquina de estados, el modelo de coordenadas, la garantía de láser apagado, las
licencias y el esquema de pantalla están en [ARQUITECTURA.md](ARQUITECTURA.md).

## 1. Árbol de archivos

```
openlaser-studio/
├── docs/
│   ├── ARQUITECTURA.md
│   ├── FASE1.md
│   ├── LICENCIAS.md
│   └── captura_fase1.png (la genera autoprueba)
├── examples/
│   ├── circulo_arcos.gcode
│   ├── cuadrado_20mm.gcode
│   ├── demo_documento.gcode
│   ├── documento_demo.json
│   ├── estrella_demo.svg
│   ├── macros_demo.json
│   ├── materiales_DEMO.json
│   └── perfil_simulado.json
├── packaging/
│   ├── build_unix.sh
│   └── build_windows.bat
├── src/
│   └── openlaser/
│       ├── app/
│       │   ├── __init__.py
│       │   └── config.py
│       ├── camera/
│       │   └── __init__.py
│       ├── canvas/
│       │   ├── __init__.py
│       │   ├── canvas_view.py
│       │   └── rulers.py
│       ├── devices/
│       │   ├── __init__.py
│       │   ├── profile.py
│       │   └── transport.py
│       ├── diagnostics/
│       │   ├── __init__.py
│       │   └── logging_setup.py
│       ├── document/
│       │   ├── __init__.py
│       │   ├── commands.py
│       │   └── model.py
│       ├── exporters/
│       │   └── __init__.py
│       ├── gcode/
│       │   ├── __init__.py
│       │   ├── analyzer.py
│       │   └── generator.py
│       ├── geometry/
│       │   ├── __init__.py
│       │   └── coords.py
│       ├── i18n/
│       │   ├── __init__.py
│       │   └── en.py
│       ├── image_processing/
│       │   └── __init__.py
│       ├── importers/
│       │   └── __init__.py
│       ├── machine/
│       │   ├── __init__.py
│       │   ├── command_queue.py
│       │   ├── controller.py
│       │   ├── events.py
│       │   ├── grbl_codes.py
│       │   ├── grbl_protocol.py
│       │   ├── grbl_settings.py
│       │   └── states.py
│       ├── materials/
│       │   └── __init__.py
│       ├── persistence/
│       │   ├── __init__.py
│       │   ├── atomic.py
│       │   └── settings.py
│       ├── plugins/
│       │   ├── __init__.py
│       │   └── controllers.py
│       ├── safety/
│       │   ├── __init__.py
│       │   └── manager.py
│       ├── simulation/
│       │   └── __init__.py
│       ├── simulator/
│       │   ├── __init__.py
│       │   ├── grbl_simulator.py
│       │   └── tcp_server.py
│       ├── toolpath/
│       │   └── __init__.py
│       ├── ui/
│       │   ├── dialogs/
│       │   │   ├── __init__.py
│       │   │   ├── config_dialogs.py
│       │   │   ├── info_dialogs.py
│       │   │   └── safety_dialogs.py
│       │   ├── panels/
│       │   │   ├── __init__.py
│       │   │   ├── console_panel.py
│       │   │   ├── info_panels.py
│       │   │   ├── job_panel.py
│       │   │   ├── layers_panel.py
│       │   │   └── motion_panel.py
│       │   ├── widgets/
│       │   │   ├── __init__.py
│       │   │   ├── safety_banner.py
│       │   │   └── state_badge.py
│       │   ├── __init__.py
│       │   ├── actions.py
│       │   ├── app.py
│       │   ├── bridge.py
│       │   ├── icons.py
│       │   ├── main_window.py
│       │   └── theme.py
│       ├── __init__.py
│       └── __main__.py
├── tests/
│   ├── conftest.py
│   ├── test_command_queue.py
│   ├── test_controller.py
│   ├── test_coords.py
│   ├── test_gcode.py
│   ├── test_grbl_parser.py
│   ├── test_i18n.py
│   ├── test_persistence.py
│   ├── test_safety.py
│   ├── test_simulator.py
│   ├── test_states.py
│   ├── test_tcp_transport.py
│   ├── test_ui_smoke.py
│   └── test_undo.py
├── tools/
│   └── extract_strings.py
├── .gitignore
├── LICENSE
├── README.md
├── autoprueba.bat
├── autoprueba.sh
├── install.bat
├── install.sh
├── pyproject.toml
├── requirements.txt
├── run.bat
├── run.sh
└── simulador_tcp.bat
```

## 2. Código

Todo el código está en `src/openlaser/` (núcleo sin dependencias de interfaz + `ui/` y `canvas/` con
PySide6). No hay bloques omitidos ni marcadores de implementación pendiente: los paquetes de fases
posteriores (`importers/`, `camera/`…) sólo contienen su descripción y las acciones de esas fases
aparecen deshabilitadas en los menús con el aviso «disponible en la fase N».

## 3. Dependencias

`pyproject.toml` (instalable con pip) y `requirements.txt`. Fase 1: PySide6, pyserial, websockets;
desarrollo: pytest. Extras opcionales `phase2`, `phase3`, `phase4` para las fases siguientes.

## 4. Instalación

Windows:

```bat
install.bat          :: crea .venv con Python 3.12 e instala el paquete con extras de desarrollo
```

Linux / macOS:

```bash
./install.sh
```

Manual: `python3.12 -m venv .venv`, activar y `pip install -e ".[dev]"`.

## 5. Ejecución

```bat
run.bat                         :: o ./run.sh, o:  python -m openlaser
run.bat --lang en --theme light :: inglés, tema claro (también alto contraste: --theme contrast)
simulador_tcp.bat               :: simulador GRBL por TCP en 127.0.0.1:2323 (para probar la conexión de red)
```

Al abrir, el perfil activo es **Simulador GRBL (demo)**: pulse *Conectar*, después *Home* (el simulador
arranca en alarma, como una máquina real con homing) y ya puede mover, hacer frame, vista previa e
iniciar el trabajo del documento de demostración. Para una máquina real cree un perfil en
*Máquina → Configuración de dispositivo* (USB/serie, TCP/Telnet o WebSocket).

Ejecutable autónomo: `packaging\build_windows.bat` o `packaging/build_unix.sh` (PyInstaller).

## 6. Pruebas automatizadas

```bat
autoprueba.bat   :: pytest + captura de la interfaz con el simulador → autoprueba_resultado.txt y docs\captura_fase1.png
```

o `python -m pytest -q`. 116 pruebas:

| Archivo | Qué comprueba |
|---|---|
| `test_grbl_parser.py` | ok/error/ALARM, bienvenida, `$n=v`, `[MSG]`, informes `<…>` completos, tolerancia a campos dañados, limpieza de líneas |
| `test_command_queue.py` | conteo de caracteres, comandos exclusivos, orden de confirmación, vaciado por origen |
| `test_states.py` | prioridad del estado visible, acciones permitidas en cada combinación, Stop siempre disponible |
| `test_coords.py` | ida y vuelta documento↔máquina↔trabajo en las 4 esquinas y ejes invertidos |
| `test_simulator.py` | alarma de arranque, homing, jog, límites, G0 sin emisión, M3/M4, reinicio, retención, puerta, `$$`, desbordamiento |
| `test_controller.py` | **criterios de aceptación**: sin movimiento al conectar, jog cambia la posición, límites, jog continuo con latido, origen, frame/ir a sin láser, trabajo completo, 400 líneas sin desbordar, **Stop vacía la cola**, pausa/reanudar, parada de software, **desconexión segura** (voluntaria y cable), alarma sin desbloqueo automático, disparo (confirmación, límites, auto-apagado, vigilante), consola peligrosa, macros, error en trabajo, parámetros |
| `test_safety.py` | clasificación de comandos, límites duros del disparo, comprobación previa |
| `test_gcode.py` | cabecera/pie, G0 sin potencia, S limitado, pasadas, origen de usuario, analizador (límites, tiempo, arcos, pulgadas), formato numérico |
| `test_persistence.py` | escritura atómica, recuperación desde `.bak`, ida y vuelta de preferencias, campos desconocidos |
| `test_undo.py` | deshacer/rehacer, fusión, límite |
| `test_tcp_transport.py` | controlador por TCP contra el simulador, pérdida de servidor, Telnet |
| `test_i18n.py` | todas las cadenas tienen traducción inglesa, marcadores coherentes, sin textos sin traducir |
| `test_ui_smoke.py` | ventana, menús, iconos+tooltip+texto accesible, conectar/Home/jog con clic real, vista previa, parada de software, captura |

## 7. Resultado esperado

* `autoprueba_resultado.txt` termina con `RESULTADO: TODAS LAS PRUEBAS SUPERADAS` (116 pasadas).
* Se abre la ventana unos 5 s, se conecta al simulador, hace Home, mueve a X120 Y160, dibuja la vista
  previa y guarda `docs\captura_fase1.png`.
* En uso manual: el estado pasa de *Desconectado* a *Conectando* y *Alarma*; tras *Home*, *Reposo*.
  Las flechas mueven la cruz roja del cabezal en el lienzo y cambian X/Y en la franja, el panel de
  movimiento y la barra de estado. *Iniciar* abre la comprobación previa; con todas las casillas
  marcadas el trabajo se ejecuta con barra de progreso y tiempo restante. *Detener* o *PARADA SOFTWARE*
  cortan el trabajo, vacían la cola y el indicador de láser vuelve a «Láser apagado».

## 8. Limitaciones conocidas

* Sólo se genera G-code para capas en **modo Línea**; Relleno e Imagen aparecen en el documento demo
  pero se generan en la Fase 3. Las formas del documento son polilíneas (Bézier y texto en la Fase 2).
* Importación SVG/DXF, proyecto nativo, herramientas de dibujo y edición: Fase 2 (acciones visibles
  pero deshabilitadas). Cámara, rotary, biblioteca de materiales y asistentes de prueba: Fase 4.
* El teclado mueve un paso por pulsación y sólo con el lienzo enfocado (para no mover la máquina al
  navegar por tablas o escribir). El jog continuo es con el ratón (mantener pulsado).
* El simulador usa un espacio de máquina positivo (0…$130) y movimiento sin aceleración; los arcos
  se recorren como cuerda. Los tiempos estimados ignoran la aceleración (factor 1,15).
* El disparo de enfoque usa `G1 F… M3 S…` + `G4`: en GRBL con `$32=1` el láser emite en reposo con M3 y
  modo G1. Verifique primero con la potencia mínima; algunos firmware pueden no emitir durante `G4`
  (en ese caso simplemente no dispara: fallo seguro).
* Con conexiones de red, si el enlace se pierde durante un trabajo, el controlador puede ejecutar
  las líneas que ya tenía en su búfer (≤ 127 bytes) antes de detenerse.
* Las unidades en pulgadas sólo afectan a la preferencia; la interfaz muestra mm en la Fase 1.
* Cambiar idioma requiere reiniciar; el cambio de tema recolorea paleta, iconos de barras y lienzo,
  pero algunos iconos de paneles se actualizan al reiniciar.
* Las reglas, cuadrícula y flechas de jog siguen los ejes de máquina; con orígenes distintos de
  «delante izquierda» la flecha Y+ puede apuntar hacia abajo en pantalla.

## 9. Lista de comprobación de seguridad (Fase 1)

| Requisito | Estado | Dónde |
|---|---|---|
| Estado visible de conexión y de láser | ✅ | franja de seguridad, barra de estado |
| Confirmación previa al inicio con lista configurable | ✅ | `PreflightDialog`, Preferencias → Seguridad |
| Confirmación para encender el láser manualmente | ✅ | `FireDialog` (dos casillas) + consola/macros |
| Límite temporal y de potencia del disparo de prueba | ✅ | 1 %/3 s por defecto, máx. 5 %/10 s, bloque M3→G4→M5 + vigilante |
| Disparo desactivado con movimiento o trabajo | ✅ | sólo en Idle; bloquea jog/frame/inicio |
| Botón Stop siempre visible + parada de software diferenciada | ✅ | franja (rojo / rojo con borde amarillo), Esc, Ctrl+Mayús+Espacio |
| Door / Hold / Alarm | ✅ | estados, reanudar sólo con subestado 0, simulador de puerta |
| Comprobación de área y límites | ✅ | jog limitado, frame/ir a/trabajo rechazados fuera del área |
| Aviso sin homing | ✅ | aviso al conectar, en jog y en la comprobación previa (opcionalmente bloqueante) |
| Aviso $32, $30 y $13 | ✅ | lectura `$$` al conectar + comprobación previa |
| Sin movimiento inesperado al conectar | ✅ | sólo `$I`, `$$`, `$G` |
| Láser apagado ante desconexión o excepción | ✅ | `0x18` + cola vaciada + trabajo abortado |
| Sin desbloqueo ni reanudación automáticos tras alarma | ✅ | probado |
| Registro de eventos de seguridad | ✅ | `openlaser.jsonl` en la carpeta de usuario, diagnóstico, exportación |
| Aviso de que el software no sustituye medidas físicas | ✅ | franja, comprobación previa, disparo, Ayuda → Seguridad |

## 10. Descripción de la interfaz resultante

Ventana de 1500×920 (tema oscuro por defecto; claro y alto contraste disponibles):

* **Arriba**: barra de menús (Archivo, Editar, Dibujar, Organizar, Capas, Láser, Máquina, Cámara,
  Herramientas, Ventana, Ayuda); barra principal con iconos de trazo propios (archivo, edición,
  trabajo, máquina) y barra de modificación (zoom, ajustar, centrar, alinear, distribuir, agrupar,
  booleanas, offset, matriz, espejo, rotar). Las acciones no implementadas aún aparecen atenuadas.
* **Izquierda**: barra vertical de dibujo y el panel **Movimiento**: posición de trabajo grande y de
  máquina pequeña para X/Y/Z/A; cruceta 3×3 con diagonales e «ir al origen» en el centro; Z±/A±
  (atenuados si el perfil no los tiene); pasos rápidos 0,1·1·10·50·100; paso y velocidad; modo
  continuo; Home, Desbloquear, Fijar/Borrar origen, Frame y Contorno; «Ir a X/Y»; posiciones guardadas;
  disparo de enfoque en rojo.
* **Centro**: la **franja de seguridad** (estado con color+símbolo+texto, láser, homing, puerta,
  coordenadas, último aviso y botones Pausa/Reanudar/Detener/PARADA SOFTWARE) y el **lienzo** con
  reglas en mm de máquina, cuadrícula adaptativa, área de trabajo, formas del documento en el color de
  su capa (relleno translúcido en capas de relleno/imagen, punteado si la capa no tiene salida), cruz
  roja del cabezal, marca azul del origen de trabajo, vista previa (rojo corte, gris discontinuo
  desplazamiento) y frame discontinuo naranja.
* **Derecha**: pestañas **Capas y operaciones** (color, nombre, modo, velocidad, potencia, pasadas,
  salida, visible; editable con Deshacer) / Propiedades / Cámara, y debajo el panel **Láser** (origen
  del trabajo, archivo G-code, copias, análisis, Vista previa, Frame, Iniciar, Pausa, Reanudar,
  Detener, progreso, tiempos, línea actual y overrides de avance, rápidos y potencia).
* **Abajo**: **Consola GRBL** contraíble con filtros, marcas de tiempo, colores TX/RX/aviso/error,
  macros de demostración, historial y envío; y la barra de estado con cursor, conexión, X/Y/Z/A,
  F y S actuales, progreso, tiempo transcurrido/restante y estado.
