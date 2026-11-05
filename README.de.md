# OpenLaser Studio

[English](README.md) · [Español](README.es.md) · [Français](README.fr.md) · **Deutsch**

Desktop-Anwendung (Windows, Linux, macOS) zum Entwerfen, Vorbereiten, Simulieren und Ausführen von
Lasergravur- und Schneidaufträgen mit **GRBL 1.1+, grblHAL und FluidNC** sowie experimentell mit
**Marlin** und **Smoothieware**. Geschrieben in Python 3.12 + PySide6. Eigener Code, eigene Symbole und
Texte — MIT-Lizenz.

> ⚠️ **Sicherheit zuerst.** OpenLaser Studio ersetzt keine physischen Verriegelungen, keinen fest
> verdrahteten Not-Aus, keine ständige Aufsicht, keine Rauchabsaugung und keinen für die Wellenlänge
> Ihres Lasers zertifizierten Augenschutz. Die Werte der Demo-Materialien sind fiktiv: Prüfen Sie jede
> Kombination an Ihrer Maschine und Ihrem Material.

## Download

Die neueste Version finden Sie unter [**Releases**](https://github.com/Aleluya-Git/Openlaser/releases/latest):

* `OpenLaserStudio-x.y.z-setup.exe` — Windows-Installer (kein Python nötig).
* `OpenLaserStudio-x.y.z-portable-win64.zip` — portable Version: entpacken und `OpenLaserStudio.exe` starten.

Unter Linux/macOS starten Sie es aus dem Quellcode (siehe unten).

## Funktionen

* **Maschinensteuerung** — USB/seriell, TCP/Telnet und WebSocket; Verfahren, Homing, Rahmen, Gehe zu,
  Nullpunkte, Echtzeit-Overrides, Konsole mit Verlauf, Makros und Editor für die GRBL-Parameter `$$`
  mit Sicherung und Wiederherstellung.
* **Controller** — GRBL 1.1+ (und Statusberichte von GRBL 0.9), grblHAL und FluidNC mit automatischer
  Firmware-Erkennung; Marlin und Smoothieware (experimentell); Aufträge auf die SD-Karte von
  FluidNC/ESP3D senden. Simulator für jede Firmware, um alles ohne Maschine auszuprobieren.
* **Design** — Vektordokument mit Ebenen (Linie, Füllung, Füllung + Linie, Bild), Zeichenwerkzeuge,
  Knotenbearbeitung, boolesche Operationen, Versätze, Anordnungen, Text und Text auf Pfad,
  Formenbibliothek; Import von SVG, DXF, PDF/AI und Bildern.
* **Bilder** — Helligkeit/Kontrast/Gamma, Rasterung (Floyd–Steinberg, Atkinson, Jarvis, Stucki, Bayer,
  Halbton…), Graustufen, Scanwinkel und Vektorisierung. Die Verarbeitung wird zwischengespeichert, damit
  das Verschieben von Bildern flüssig bleibt.
* **G-Code und Simulation** — Erzeugung von Linie, Füllung und Bild, Leerfahrtoptimierung, Stege,
  Ein-/Ausfahrten, Schnittfugenkompensation, Zeitschätzung und schrittweise Simulation mit
  Brennvorschau.
* **Kamera** — Kalibrierung von Objektiv und Arbeitsbereich, Überlagerung auf der Zeichenfläche, Print
  and Cut und Center Finder.
* **Rotary** und Zylinderkorrektur, Materialbibliothek und 13 Testassistenten (Fokus, Leistung, Abstand,
  Schnittfuge…).
* **Sicherheit von Grund auf** — Checkliste und Prüfungen vor dem Start, Laser bei Verfahren und Rahmen
  immer aus, Software-Stopp, Watchdog für den Fokusschuss, kein automatisches Entsperren oder Fortsetzen
  nach einem Alarm und Protokoll der Sicherheitsereignisse.
* **Sprachen** — Deutsch, Englisch, Spanisch und Französisch. Jeder kann eine Sprache hinzufügen, indem
  er eine einzige JSON-Datei übersetzt: siehe [docs/IDIOMAS.md](docs/IDIOMAS.md) (Einstellungen →
  *Neue Sprache erstellen…*).
* **Plugins** — Python-Erweiterungen für weitere Controller und Werkzeuge.

## Aus dem Quellcode starten

Erfordert Python 3.12 oder neuer.

```bash
# Windows
install.bat        # erstellt .venv und installiert die Abhängigkeiten
run.bat            # startet die Anwendung mit dem GRBL-Demosimulator
autoprueba.bat     # führt die Tests aus und erstellt einen Screenshot

# Linux / macOS
./install.sh && ./run.sh
```

Oder manuell: `python -m venv .venv`, `pip install -e .`, `python -m openlaser`.

Um den Windows-Installer selbst zu erstellen, führen Sie `build_installer.bat` aus (PyInstaller + Inno
Setup 6), siehe [docs/INSTALADOR.md](docs/INSTALADOR.md). Beim Hochladen eines Tags `vX.Y.Z` erstellt
und veröffentlicht GitHub Actions automatisch ein Release.

## Verbindungstipps

* Schließen Sie vor dem Verbinden jedes andere Programm, das den seriellen Port nutzt (LaserGRBL,
  LightBurn…).
* Die Standardgeschwindigkeit ist 115200 Baud. DTR/RTS sind standardmäßig aus; antwortet die Platine
  nicht, probiert OpenLaser die anderen Kombinationen und zeigt an, welche funktioniert hat, damit Sie
  sie im Geräteprofil speichern können.

## Dokumentation

Die technische Dokumentation (auf Spanisch) befindet sich in [`docs/`](docs): Architektur, jede
Entwicklungsphase, Installer, Sprachen und Lizenzen von Drittanbietern ([docs/LICENCIAS.md](docs/LICENCIAS.md)).

## Lizenz

MIT — siehe [LICENSE](LICENSE). Verwendet PySide6 (LGPL-3.0, dynamische Verlinkung), pyserial, websockets
und numpy. Steht in keiner Verbindung zu LaserGRBL oder LightBurn und enthält keinen Code und keine
Ressourcen davon.
