import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_strings import all_strings, extract  # noqa: E402

from openlaser import i18n  # noqa: E402
from openlaser.i18n import LOCALES_DIR, TEMPLATE_NAME, pick, set_language, tr  # noqa: E402
from openlaser.machine.grbl_codes import ALARMS, ERRORS  # noqa: E402
from openlaser.machine.grbl_settings import SETTINGS  # noqa: E402


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


BUNDLED = ("en", "fr", "de")


def _catalog(code: str) -> dict[str, str]:
    return i18n.load_language_file(LOCALES_DIR / f"{code}.json")["strings"]


def test_bundled_languages_available():
    langs = i18n.available_languages()
    assert langs["es"] == "Español"
    assert langs["en"] == "English" and langs["fr"] == "Français" and langs["de"] == "Deutsch"


def test_every_source_string_has_english_translation():
    strings, _ = extract()
    catalog = _catalog("en")
    missing = sorted(s for s in strings if s not in catalog)
    assert not missing, f"Faltan traducciones: {missing[:10]}"


def test_french_and_german_are_complete():
    needed = set(all_strings())
    for code in ("fr", "de"):
        missing = sorted(needed - set(_catalog(code)))
        assert not missing, f"{code}: faltan {missing[:10]}"


def test_placeholders_match():
    for code in BUNDLED:
        for es, text in _catalog(code).items():
            assert _fields(es) == _fields(text), (code, es)
            assert es.count("\n") == text.count("\n"), (code, es)
            if "&" in es and "&&" not in es:
                assert text.count("&") == 1, (code, es)


def test_template_lists_every_phrase():
    data = i18n.json.loads((LOCALES_DIR / TEMPLATE_NAME).read_text(encoding="utf-8"))
    assert set(data["strings"]) == set(all_strings()), "Ejecute tools/extract_strings.py --template"
    assert "plantilla" not in i18n.available_languages()


def test_french_german_switch():
    try:
        assert set_language("fr") == "fr"
        assert tr("Conectar") == "Connecter"
        assert tr("Mover {a}", a="X+") == "Déplacer X+"
        assert pick({"es": "Puerta abierta", "en": "Door open"}) == "Porte ouverte"
        assert pick({"es": "frase sin traducir", "en": "untranslated"}) == "untranslated"
        assert set_language("de") == "de"
        assert tr("Conectar") == "Verbinden"
        assert pick(next(iter(ALARMS.values())).description) == _catalog("de")[next(iter(ALARMS.values())).description["es"]]
    finally:
        set_language("es")


def test_user_language_file_overrides_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLASER_HOME", str(tmp_path))
    try:
        path = i18n.create_language_file("it")
        assert path.parent == i18n.user_locales_dir()
        data = i18n.json.loads(path.read_text(encoding="utf-8"))
        data["language"] = "Italiano"
        data["strings"] = {"Conectar": "Connetti"}  # el resto se mostrará en inglés
        path.write_text(i18n.json.dumps(data), encoding="utf-8")
        assert i18n.available_languages()["it"] == "Italiano"
        assert set_language("it") == "it"
        assert tr("Conectar") == "Connetti"
        assert tr("Desconectar") == "Disconnect"
        try:
            i18n.create_language_file("it")
        except ValueError:
            pass
        else:
            raise AssertionError("debe rechazar un idioma que ya existe")
        for bad in ("es", "", "../x", "italiano largo"):
            try:
                i18n.create_language_file(bad)
            except ValueError:
                continue
            raise AssertionError(bad)
        (path.parent / "roto.json").write_text("{no es json", encoding="utf-8")
        assert "roto" not in i18n.available_languages()
    finally:
        set_language("es")


def test_switch_language():
    try:
        assert set_language("en") == "en"
        assert tr("Conectar") == "Connect"
        assert tr("Mover {a}", a="X+") == "Move X+"
        assert pick({"es": "hola", "en": "hello"}) == "hello"
        assert set_language("xx") == "es"
        assert tr("Conectar") == "Conectar"
    finally:
        set_language("es")


def test_code_tables_are_bilingual():
    for table in (ALARMS, ERRORS):
        for info in table.values():
            for d in (info.description, info.cause, info.action):
                assert d["es"] and d["en"]
    for s in SETTINGS.values():
        assert s.name["es"] and s.name["en"] and s.help["es"] and s.help["en"]


def test_no_hardcoded_spanish_setText_without_tr():
    """Detecta textos visibles en español escritos sin tr() en la interfaz."""
    offenders = []
    pattern = re.compile(r'(setText|setToolTip|QLabel|QPushButton|QCheckBox)\(\s*"([^"]*[áéíóúñ¿¡][^"]*)"')
    for path in (ROOT / "src" / "openlaser" / "ui").rglob("*.py"):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {m.group(2)}")
    assert not offenders, offenders
