# Instalador de Windows

## Generarlo

1. Tener el entorno preparado (`install.bat`) y las pruebas en verde (`autoprueba.bat`).
2. Ejecutar **`build_installer.bat`**. Pasos:
   1. Pruebas automáticas: si alguna falla, no se genera nada.
   2. Ejecutable con **PyInstaller**: `packaging/openlaser.spec`, carpeta `dist\OpenLaserStudio`, sin
      consola, con icono y metadatos de versión. Se excluyen los módulos de Qt que no se usan.
   3. **Prueba del ejecutable**: se arranca con el simulador y debe dejar `docs\captura_instalador.png`.
   4. Instalador con **Inno Setup 6** (`packaging/installer.iss`). Si no está instalado, se intenta con
      `winget install JRSoftware.InnoSetup`. Si tampoco se puede, se genera
      `dist\OpenLaserStudio-0.6.0-portable.zip`.
3. El registro completo queda en `instalador_resultado.txt`.

Resultado: **`dist\OpenLaserStudio-0.6.0-setup.exe`**.

## Qué hace el instalador

* Asistente en español o inglés, con la licencia (MIT) y un aviso de seguridad láser en la bienvenida.
* Instala sólo para el usuario actual (sin permisos de administrador) o para todos, a elección.
* Accesos en el menú Inicio (programa, ejemplos y desinstalar) y, opcionalmente, en el escritorio.
* Desinstalador en *Configuración → Aplicaciones*. Las preferencias, perfiles y la biblioteca de
  materiales (`%APPDATA%\OpenLaserStudio`) se conservan al desinstalar.

## Limitaciones

* No está firmado digitalmente: Windows SmartScreen puede mostrar «Windows protegió su PC».
  Pulse *Más información → Ejecutar de todas formas*. Firmarlo exige un certificado de firma de
  código.
* PyInstaller tiene que admitir la versión de Python del entorno. Si falla con Python 3.15, cree el
  entorno con Python 3.12 o 3.13 (`py -3.12 -m venv .venv`) y repita `install.bat`.
* El ejecutable incluye Qt (LGPL-3.0) como bibliotecas dinámicas separadas, lo que permite
  sustituirlas, como exige la LGPL; las licencias están en `LICENCIAS.md`.
