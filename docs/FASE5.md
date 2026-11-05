# Fase 5 — Entrega: controladores grblHAL y FluidNC

El documento original define cuatro fases. Esta Fase 5, elegida por el usuario, convierte los
adaptadores **grblHAL** y **FluidNC** (antes «previstos») en adaptadores **disponibles**, con
detección automática del firmware y un simulador de cada uno.

## 1. Árbol de archivos (nuevos ★ y modificados ✎)

```
openlaser-studio/
├── autoprueba.bat / .sh, README.md             ✎ captura → docs/captura_fase5.png
├── pyproject.toml, src/openlaser/__init__.py   ✎ versión 0.5.0
├── docs/FASE5.md, ARQUITECTURA.md (§10)        ★/✎
├── src/openlaser/
│   ├── machine/
│   │   ├── dialects.py                         ★ GRBL / grblHAL / FluidNC: detección, búfer, alarmas
│   │   ├── controller.py                       ✎ detección, búfer de $I, H:, Pn:E, [MSG:ERR], sólo lectura
│   │   ├── grbl_codes.py                       ✎ alarmas y errores según el firmware
│   │   ├── states.py                           ✎ estado «Tool» (grblHAL)
│   │   └── events.py                           ✎ MachineStatus.dialect
│   ├── simulator/grbl_simulator.py             ✎ variantes grblhal y fluidnc
│   ├── devices/profile.py, transport.py        ✎ rx_buffer_auto; el simulador sigue al perfil
│   ├── plugins/controllers.py                  ✎ grblHAL y FluidNC disponibles
│   ├── app/config.py                           ✎ versión
│   ├── i18n/en.py                              ✎ traducciones
│   └── ui/
│       ├── dialogs/config_dialogs.py           ✎ elegir firmware y búfer automático; parámetros de sólo lectura
│       ├── dialogs/safety_dialogs.py           ✎ explicación de alarmas según el firmware
│       ├── main_window.py                      ✎ firmware en la barra de estado; simulador del perfil
│       ├── actions.py                          ✎ CURRENT_PHASE = 5
│       └── app.py                              ✎ captura con el simulador grblHAL y la consola
└── tests/
    ├── test_dialects.py                        ★ 14 pruebas (núcleo y simulador de los 3 firmwares)
    └── test_ui_smoke.py                        ✎ +3 pruebas de interfaz
```

## 2. Código

Completo en `src/openlaser/`. Las diferencias entre firmwares están en `machine/dialects.py` (sin
Qt); el controlador, la cola y las garantías de seguridad son los mismos para los tres.

## 3. Dependencias

Ninguna nueva.

## 4. Instalación

Copie los archivos y ejecute `autoprueba.bat`. No hace falta reinstalar.

## 5. Ejecución

* **Elegir el firmware**: *Máquina → Dispositivos…*, pestaña *Conexión*, campo **Firmware**
  (GRBL 1.1+, grblHAL o FluidNC). Con la conexión *Simulador*, el simulador imita ese firmware.
* **Detección automática**: al conectar, la consola muestra «Firmware detectado: …». Si el perfil
  dice otro, aparece un aviso y se usa el detectado. La barra de estado muestra el firmware
  («grblHAL · simulador»).
* **grblHAL**: el búfer de recepción se lee de `$I` (normalmente 1024 bytes) y se usa entero menos
  un byte, lo que evita pausas en grabados de imagen. La casilla *Usar el búfer de recepción que
  informa el controlador* lo desactiva. El homing se toma del campo `H:` del controlador. Si se
  pulsa la parada de emergencia física (`Pn:E`/`ALARM:10`), aparece un aviso de bloqueo y la
  posición deja de considerarse referenciada. Las alarmas 10–17 tienen explicación propia.
* **FluidNC**: los parámetros `$$` se leen, pero el diálogo *Parámetros GRBL* queda en sólo lectura
  (FluidNC guarda la configuración en un archivo YAML; `$CD` en la consola la muestra). Los
  mensajes `[MSG:ERR: …]` aparecen como aviso. Al conectar, un aviso recuerda revisar que el husillo
  sea de tipo láser en el YAML. Las alarmas 10–15 tienen explicación propia, y los errores no
  documentados indican cómo consultarlos (`$E=<número>`).
* **Envío a la SD** (de la Fase 4) sigue disponible para FluidNC.

## 6. Pruebas automatizadas

**287 pruebas** (247 sin interfaz + 40 de interfaz).

| Archivo | Qué comprueba |
|---|---|
| `test_dialects.py` | reglas de detección (bienvenida, `$I`, `FW:`), tamaño del búfer de `[OPT:]` y límites, campos `H:`/`Pn:E`/`SD:` y estado `Tool` (pausa: sólo reanudar), textos de alarmas/errores por firmware, adaptadores disponibles, simulador según el perfil; con simulador: grblHAL detectado con búfer de 1022 bytes, homing por `H:`, trabajo de 400 líneas sin desbordamiento y láser sólo al avanzar; parada de emergencia física (`ALARM:10`); grblHAL en modo compatibilidad detectado por `$I`; FluidNC detectado, búfer sin ampliar, escritura de parámetros rechazada, `[MSG:ERR]` como aviso, `$$` legible; avisos de firmware distinto al del perfil; **en los tres firmwares**: conectar/Home sin emisión, jog, trabajo con el láser sólo en G1 y parada de software |
| `test_ui_smoke.py` (+3) | conectar con grblHAL y con FluidNC (firmware en la barra de estado, parámetros de sólo lectura en FluidNC, Home sin emisión), elegir firmware y búfer automático en el gestor de dispositivos |

## 7. Resultado esperado

* `RESULTADO: TODAS LAS PRUEBAS SUPERADAS` con 287 pruebas y códigos de salida 0.
* La captura `docs/captura_fase5.png` muestra la conexión al simulador **grblHAL**: barra de estado
  «grblHAL · simulador» y la consola con `GrblHAL 1.1f …`, `[FIRMWARE:grblHAL]`, «Firmware detectado:
  grblHAL» y «Búfer de recepción del controlador: 1023 bytes (se usan 1022)».

## 8. Limitaciones conocidas

* **No se ha probado con controladores reales**: sólo con el simulador, que reproduce lo documentado
  por grblHAL (wiki «Report extensions») y FluidNC. Haga la primera conexión con el láser
  desconectado o la potencia al mínimo y revise la consola.
* La numeración de alarmas 16–17 de grblHAL y 10–15 de FluidNC se ha tomado de sus fuentes y
  puede cambiar entre versiones; los códigos desconocidos se muestran como tales, sin suponer nada.
* FluidNC: no se edita el YAML desde la aplicación; el envío de archivos es por HTTP (Fase 4). El
  búfer no se amplía automáticamente porque FluidNC no informa su tamaño en todos los canales.
* grblHAL: no se usan sus comandos de tiempo real ampliados (p. ej. `0x87` informe completo), ni el
  cambio de herramienta, sondas, MPG, ni la lectura de la SD del controlador.
* Smoothieware y Marlin siguen previstos (necesitan otro protocolo de flujo); Ruida, Trocen y galvo,
  sólo mediante plugins con documentación autorizada.

## 9. Lista de comprobación de seguridad (Fase 5)

- [x] Los tres firmwares usan el mismo controlador: `S0` antes de G0, `M5` antes de jog/frame,
      disparo `M3→G4→M5`, reinicio (`0x18`) al parar o desconectar, nunca `$X`/`$H` automáticos
      (comprobado con el simulador de cada firmware).
- [x] El firmware detectado sustituye al del perfil y **avisa** de la diferencia; nunca se supone
      grblHAL por el perfil sin confirmarlo en el controlador.
- [x] Búfer: sólo se amplía con grblHAL y con el valor que informa el propio controlador (menos un
      byte); en los demás firmwares sólo puede reducirse. Sin desbordamientos en las pruebas.
- [x] grblHAL: la parada de emergencia física se muestra como bloqueo y obliga a repetir Home; las
      alarmas que invalidan la posición dependen del firmware.
- [x] FluidNC: no se escriben parámetros numerados (evita cambios que el firmware ignoraría o
      interpretaría de otra forma); aviso para revisar la configuración del láser en el YAML.
- [x] El estado `Tool` de grblHAL se trata como pausa: sólo se permite reanudar.

## 10. Descripción de la interfaz resultante

* **Gestor de dispositivos** (*Máquina → Dispositivos…*, pestaña *Conexión*): el campo **Firmware**
  ofrece GRBL 1.1+, grblHAL y FluidNC (con la descripción como información emergente) y una casilla
  nueva, *Usar el búfer de recepción que informa el controlador ($I)*.
* **Barra de estado**: con la máquina conectada, la etiqueta de conexión empieza por el firmware
  detectado («grblHAL · simulador», «FluidNC · tcp://…»).
* **Consola**: líneas informativas «Firmware detectado: …» y «Búfer de recepción del controlador: …».
* **Avisos** (banda superior): firmware distinto al del perfil (ámbar), parada de emergencia física
  (rojo), mensajes de error de FluidNC (ámbar), recordatorio de la configuración del láser en FluidNC.
* **Parámetros GRBL** con FluidNC: el campo de valor y *Escribir valor* desactivados y una nota que
  explica que la configuración está en YAML.
* **Gestión de plugins**: grblHAL y FluidNC aparecen como **disponibles** con sus notas.
