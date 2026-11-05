<img width="500" height="250" alt="Logo Openlaser" src="https://github.com/user-attachments/assets/889076ac-5cb7-4d92-a470-d0718cab5827" />


# OpenLaser Studio

**English** · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md)

Desktop application (Windows, Linux, macOS) to design, prepare, simulate and run laser engraving and
cutting jobs on **GRBL 1.1+, grblHAL and FluidNC** controllers, with experimental support for **Marlin**
and **Smoothieware**. Written in Python 3.12 + PySide6. Original code, icons and texts — MIT licence.

<img width="1920" height="1034" alt="image" src="https://github.com/user-attachments/assets/eac16612-3a48-496c-a2da-3529babf126e" />


[![Tests](https://github.com/Aleluya-Git/Openlaser/actions/workflows/tests.yml/badge.svg)](https://github.com/Aleluya-Git/Openlaser/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/Aleluya-Git/Openlaser)](https://github.com/Aleluya-Git/Openlaser/releases/latest)

> ⚠️ **Safety first.** OpenLaser Studio does not replace physical interlocks, a hard-wired emergency stop,
> constant supervision, fume extraction or eye protection certified for your laser's wavelength.
> Demo material values are fictitious: validate every combination on your machine and material.

## Download

Get the latest version from [**Releases**](https://github.com/Aleluya-Git/Openlaser/releases/latest):

* `OpenLaserStudio-x.y.z-setup.exe` — Windows installer (no Python needed).
* `OpenLaserStudio-x.y.z-portable-win64.zip` — portable version: unzip and run `OpenLaserStudio.exe`.

On Linux/macOS, run it from source (see below).

## Features

* **Machine control** — USB/serial, TCP/Telnet and WebSocket; jog, homing, frame, go-to, origins,
  real-time overrides, console with history, macros, GRBL `$$` settings editor with backup/restore.
* **Controllers** — GRBL 1.1+ (and 0.9 status reports), grblHAL and FluidNC with automatic firmware
  detection; Marlin and Smoothieware (experimental); upload jobs to the SD card of FluidNC/ESP3D.
  Built-in simulator of every firmware to try everything without a machine.
* **Design** — vector document with layers (Line, Fill, Fill + Line, Image), drawing tools, node editing,
  booleans, offsets, arrays, text and text on path, shape library; import SVG, DXF, PDF/AI and images.
* **Images** — brightness/contrast/gamma, dithering (Floyd–Steinberg, Atkinson, Jarvis, Stucki, Bayer,
  halftone…), grayscale, scan angle, image tracing. Processing is cached so moving images stays smooth.
* **G-code & simulation** — line, fill and raster generation, travel optimisation, tabs, lead-in/out,
  kerf compensation, time estimation and step-by-step simulation with burn preview.
* **Camera** — lens and work-area calibration, overlay on the canvas, Print and Cut, Center Finder.
* **Rotary** and cylinder correction, material library and 13 test wizards (focus, power, interval, kerf…).
* **Safety by design** — pre-start checklist and checks, laser never on during jog/frame, software stop,
  watchdog for focus fire, no automatic unlock or resume after alarms, safety event log.
* **Languages** — English, Spanish, French and German. Anyone can add a language by translating a
  single JSON file: see [docs/IDIOMAS.md](docs/IDIOMAS.md) (Preferences → *Create new language…*).
* **Plugins** — Python plugins for extra controllers and tools.

## Running from source

Requires Python 3.12+.

```bash
# Windows
install.bat        # creates .venv and installs dependencies
run.bat            # starts the app with the demo GRBL simulator
autoprueba.bat     # runs the tests and takes a screenshot

# Linux / macOS
./install.sh && ./run.sh
```

Or manually: `python -m venv .venv`, `pip install -e .`, `python -m openlaser`.

To build the Windows installer yourself run `build_installer.bat` (PyInstaller + Inno Setup 6), see
[docs/INSTALADOR.md](docs/INSTALADOR.md). Pushing a `vX.Y.Z` tag builds and publishes a release
automatically with GitHub Actions.

## Connection tips

* Close any other program that uses the serial port (LaserGRBL, LightBurn…) before connecting.
* Default speed is 115200 baud. DTR/RTS are off by default; if the board does not answer, OpenLaser
  tries the other combinations and tells you which one worked so you can save it in the device profile.

## Documentation

The technical documentation is in Spanish, in [`docs/`](docs): architecture, each development phase,
installer, languages and third-party licences ([docs/LICENCIAS.md](docs/LICENCIAS.md)).

## Licence

MIT — see [LICENSE](LICENSE). Uses PySide6 (LGPL-3.0, dynamic linking), pyserial, websockets and numpy.
Not affiliated with LaserGRBL or LightBurn and contains no code or resources from them.
