# OpenLaser Studio

[English](README.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md)

Aplicación de escritorio (Windows, Linux, macOS) para diseñar, preparar, simular y ejecutar trabajos de
grabado y corte láser con controladores **GRBL 1.1+, grblHAL y FluidNC**, y de forma experimental
**Marlin** y **Smoothieware**. Escrita en Python 3.12 + PySide6. Código, iconos y textos originales —
licencia MIT.

<img width="1920" height="1034" alt="image" src="https://github.com/user-attachments/assets/99541cb7-b0a4-4dec-afeb-c02eefe9b34b" />


> ⚠️ **La seguridad, primero.** OpenLaser Studio no sustituye los interlocks físicos, la parada de
> emergencia cableada, la supervisión permanente, la extracción de humos ni la protección ocular
> certificada para la longitud de onda de su láser. Los valores de los materiales de demostración son
> ficticios: valide cada combinación en su máquina y material.

## Descarga

Descargue la última versión en [**Releases**](https://github.com/Aleluya-Git/Openlaser/releases/latest):

* `OpenLaserStudio-x.y.z-setup.exe` — instalador para Windows (no necesita Python).
* `OpenLaserStudio-x.y.z-portable-win64.zip` — versión portable: descomprima y ejecute `OpenLaserStudio.exe`.

En Linux/macOS, ejecútelo desde el código fuente (ver más abajo).

## Funciones

* **Control de la máquina** — USB/serie, TCP/Telnet y WebSocket; movimiento manual, homing, marco,
  ir a, orígenes, ajustes en tiempo real, consola con historial, macros y editor de parámetros `$$` de
  GRBL con copia de seguridad y restauración.
* **Controladores** — GRBL 1.1+ (y los informes de GRBL 0.9), grblHAL y FluidNC con detección automática
  del firmware; Marlin y Smoothieware (experimental); envío de trabajos a la tarjeta SD de FluidNC/ESP3D.
  Simulador de cada firmware para probarlo todo sin máquina.
* **Diseño** — documento vectorial con capas (Línea, Relleno, Relleno + Línea, Imagen), herramientas de
  dibujo, edición de nodos, operaciones booleanas, desplazamientos, matrices, texto y texto sobre
  trayectoria, biblioteca de formas; importación de SVG, DXF, PDF/AI e imágenes.
* **Imágenes** — brillo/contraste/gamma, tramados (Floyd–Steinberg, Atkinson, Jarvis, Stucki, Bayer,
  semitono…), escala de grises, ángulo de barrido y vectorización. El procesado se guarda en memoria
  para que mover imágenes sea fluido.
* **G-code y simulación** — generación de línea, relleno e imagen, optimización de desplazamientos,
  puentes, entradas/salidas, compensación de kerf, estimación de tiempo y simulación paso a paso con
  vista del quemado.
* **Cámara** — calibración de lente y del área de trabajo, superposición en el lienzo, Print and Cut y
  Center Finder.
* **Rotary** y corrección cilíndrica, biblioteca de materiales y 13 asistentes de prueba (enfoque,
  potencia, intervalo, kerf…).
* **Seguridad desde el diseño** — lista de comprobación y verificaciones antes de empezar, láser siempre
  apagado en movimientos y marcos, parada por software, vigilante del disparo de enfoque, sin
  desbloqueo ni reanudación automáticos tras una alarma y registro de eventos de seguridad.
* **Idiomas** — español, inglés, francés y alemán. Cualquiera puede añadir un idioma traduciendo un único
  archivo JSON: vea [docs/IDIOMAS.md](docs/IDIOMAS.md) (Preferencias → *Crear idioma nuevo…*).
* **Plugins** — complementos en Python para otros controladores y herramientas.

## Ejecutar desde el código fuente

Requiere Python 3.12 o superior.

```bash
# Windows
install.bat        # crea .venv e instala las dependencias
run.bat            # abre la aplicación con el simulador GRBL de demostración
autoprueba.bat     # ejecuta las pruebas y hace una captura

# Linux / macOS
./install.sh && ./run.sh
```

O a mano: `python -m venv .venv`, `pip install -e .`, `python -m openlaser`.

Para generar usted mismo el instalador de Windows ejecute `build_installer.bat` (PyInstaller + Inno
Setup 6); vea [docs/INSTALADOR.md](docs/INSTALADOR.md). Al subir una etiqueta `vX.Y.Z`, GitHub Actions
genera y publica la release automáticamente.

## Consejos de conexión

* Cierre cualquier otro programa que use el puerto serie (LaserGRBL, LightBurn…) antes de conectar.
* La velocidad por defecto es 115200 baudios. DTR/RTS están desactivadas por defecto; si la placa no
  responde, OpenLaser prueba las demás combinaciones y le indica cuál ha funcionado para que la guarde
  en el perfil del dispositivo.

## Documentación

La documentación técnica está en [`docs/`](docs): arquitectura, cada fase del desarrollo, instalador,
idiomas y licencias de terceros ([docs/LICENCIAS.md](docs/LICENCIAS.md)).

## Licencia

MIT — vea [LICENSE](LICENSE). Usa PySide6 (LGPL-3.0, enlace dinámico), pyserial, websockets y numpy.
No está relacionado con LaserGRBL ni LightBurn y no contiene código ni recursos suyos.
