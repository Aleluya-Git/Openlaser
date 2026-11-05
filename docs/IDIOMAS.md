# Idiomas de OpenLaser Studio

OpenLaser Studio incluye **español** (idioma original del programa), **inglés**, **francés** y **alemán**.
El idioma se elige en **Preferencias → General → Idioma** y se aplica al reiniciar la aplicación.

## Cómo funciona

Todas las frases del programa están escritas en español en el código. Cada idioma es un archivo
JSON que asocia cada frase española con su traducción:

```json
{
 "language": "Italiano",
 "code": "it",
 "author": "Su nombre",
 "strings": {
  "Conectar": "Connetti",
  "Mover {a}": "Muovi {a}",
  "&Archivo": "&File"
 }
}
```

Los archivos se buscan en dos carpetas (la segunda tiene prioridad si se repite el código):

| Carpeta | Contenido |
|---|---|
| `src/openlaser/i18n/locales/` (dentro del programa instalado) | `en.json`, `fr.json`, `de.json` y `plantilla_idioma.json` |
| Carpeta de usuario `…/OpenLaserStudio/idiomas/` | Idiomas añadidos por usted, sin tocar el programa |

La carpeta de usuario se abre con **Preferencias → Abrir carpeta de idiomas**. Si copia allí un
`fr.json` modificado, sustituye al incluido (útil para corregir una traducción).

## Crear un idioma nuevo

1. En **Preferencias → General**, pulse **Crear idioma nuevo…** y escriba el código (p. ej. `it`,
   `pt`, `pt-br`, `ca`). Se crea `idiomas/it.json` a partir de la plantilla, con **todas** las frases
   del programa y el texto inglés como punto de partida.
2. Abra el archivo con un editor de texto que guarde en **UTF-8** (Bloc de notas, VS Code, Notepad++).
3. Cambie `"language"` por el nombre que verá el usuario (p. ej. `"Italiano"`) y rellene `"author"`.
4. Traduzca cada **valor** (a la derecha de los dos puntos). **No cambie la clave** (la frase española
   de la izquierda): es la que usa el programa para buscar la traducción.
5. Guarde, reinicie OpenLaser Studio y elija el idioma en Preferencias.

Sin la aplicación también vale copiar `src/openlaser/i18n/locales/plantilla_idioma.json` a la carpeta
de idiomas con el nombre `<código>.json` y cambiar `"code"` y `"language"`.

### Reglas al traducir

- Conserve exactamente las marcas entre llaves: `{n}`, `{p:.1f}`, `{x:.3f}`… (se sustituyen por números
  o nombres). Puede cambiarlas de sitio dentro de la frase.
- El `&` de los menús marca la letra del atajo con Alt (`"&Archivo"` → Alt+A). Deje un solo `&`.
- `\n` es un salto de línea: manténgalo.
- Los comandos (`$H`, `$X`, `M3`, `G0`…) y los nombres de firmware no se traducen.
- Las frases que deje vacías o borre se muestran en inglés y, si tampoco existen, en español.
- Si el archivo tiene un error de formato JSON, el idioma no aparece en la lista (el programa sigue
  funcionando); revise comas y comillas.

## Para desarrolladores

- En el código, las frases visibles se escriben con `tr("…")` (o `N_("…")` en tablas que se traducen al
  mostrarse). Las tablas de alarmas, errores y parámetros GRBL usan `pick({"es": …, "en": …})`.
- `python tools/extract_strings.py --missing` lista frases sin traducir en cada idioma.
- `python tools/extract_strings.py --update fr` añade a `fr.json` las frases nuevas (vacías).
- `python tools/extract_strings.py --template` regenera la plantilla tras añadir frases.
- `tests/test_i18n.py` comprueba que inglés, francés y alemán están completos, que las marcas `{…}`
  coinciden y que la plantilla está al día.
