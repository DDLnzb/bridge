"""Vertrag zwischen Bridge-Konfiguration, Compose-Service und README.

Zwei Befunde, die sich beide still verhalten (#555, #671):

- Der Bridge-Service in `docker-compose.prod.yml` startete mit `API_URL` statt
  `DDLNZB_API_URL` und bekam seine Zugangsdaten nicht. Der Prozess startete,
  lehnte aber jeden SABnzbd-Aufruf ab ("Bridge API key not configured") und
  erreichte die API nicht.
- `bridge/README.md` dokumentierte den Configvertrag des `src/`-Stubs
  (`JD_EMAIL`, `DDLNZB_BRIDGE_TOKEN`). Wer dem Quick Start folgte, startete
  eine Bridge ohne Zugang.

Der Vertrag ist deshalb an der Quelle festgemacht: **was `app/config.py`
liest, muss in `bridge/.env` stehen, im Compose-Service als Variable gesetzt
sein und in der README benannt werden.** Alles andere ist Dekoration, die
irgendwann gegen den Code verrottet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "bridge"
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"
CONFIG_FILE = BRIDGE_DIR / "app" / "config.py"
README_FILE = BRIDGE_DIR / "README.md"
ENV_FILE = BRIDGE_DIR / ".env"

#: Dieser Test prueft den **Monorepo**-Vertrag: die Compose-Datei des
#: Produktionsstacks und die versionierte `bridge/.env`. Beides existiert im
#: public Repo `DDLnzb/bridge` bewusst nicht - dort liegt der Code im
#: Repo-Root, es gibt keine Compose-Datei und keine versionierten
#: Zugangsdaten. Die Pruefungen laufen deshalb nur im Monorepo, wo ihr
#: Gegenstand liegt.
if not BRIDGE_DIR.is_dir():
    pytest.skip(
        "Monorepo-Vertrag (docker-compose.prod.yml, bridge/.env) - "
        "im public Repo nicht vorhanden",
        allow_module_level=True,
    )

#: Variablen des `src/`-Stubs. Die Bridge startet `app/` - diese Namen liest
#: kein laufender Prozess. Wer sie in der README findet, folgt dem falschen
#: Vertrag (#671).
SRC_VARIABLEN = ("DDLNZB_BRIDGE_TOKEN", "JD_EMAIL", "JD_PASSWORD", "JD_DEVICE")

#: Erfundene Download-Pfade. Es gibt nur `DOWNLOAD_DIR` plus das fest
#: verdrahtete `CATEGORY_MAPPING` (`bridge/app/config.py`).
ERFUNDENE_PFAD_VARIABLEN = ("DOWNLOAD_PATH_MOVIES", "DOWNLOAD_PATH_TV", "DOWNLOAD_PATH_DOCS")

#: `os.getenv("NAME", "")` - ein leerer Default macht die Variable zur Pflicht.
_GETENV_PFLICHT = re.compile(r'os\.getenv\(\s*"([A-Z0-9_]+)"\s*,\s*""\s*\)')
_GETENV_OHNE_DEFAULT = re.compile(r'os\.getenv\(\s*"([A-Z0-9_]+)"\s*\)')


def _service_block(text: str, name: str) -> str:
    """Der Block eines Compose-Service als Text.

    Gleiches Muster wie in `backend/tests/worker/test_vpn_config_mount.py`:
    ein Service beginnt mit zwei Leerzeichen und dem Namen, endet beim
    naechsten Service.
    """
    zeilen = text.splitlines()
    for index, zeile in enumerate(zeilen):
        if not re.match(rf"^  {re.escape(name)}:\s*$", zeile):
            continue

        block = [zeile]
        for folgezeile in zeilen[index + 1 :]:
            if re.match(r"^  [A-Za-z0-9_-]+:\s*$", folgezeile):
                break
            block.append(folgezeile)
        return "\n".join(block)

    raise AssertionError(f"Kein Service {name!r} in {COMPOSE_FILE.name} gefunden")


def _gelesene_variablen() -> set[str]:
    """Alle Variablen, die `app/config.py` aus der Umgebung liest."""
    text = CONFIG_FILE.read_text()
    return set(_GETENV_PFLICHT.findall(text)) | set(_GETENV_OHNE_DEFAULT.findall(text))


def _pflicht_variablen() -> set[str]:
    """Die gelesenen Variablen ohne brauchbaren Default - ohne sie startet
    die Bridge funktional leer."""
    return _gelesene_variablen()


def _env_werte() -> dict[str, str]:
    """Die gesetzten Variablen aus `bridge/.env`."""
    werte: dict[str, str] = {}
    for zeile in ENV_FILE.read_text().splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        werte[name.strip()] = wert.strip()
    return werte


def test_compose_setzt_die_api_url_mit_ddl_anzb_vorzeichen() -> None:
    """Der Code liest `DDLNZB_API_URL` - das Compose muss es auch setzen.

    Mit `API_URL` bleibt die Variable leer, der Bridge faellt auf
    `http://localhost:8000` zurueck und erreicht die API nicht (#555).
    """
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    assert "DDLNZB_API_URL=" in block, (
        "Der Bridge-Service setzt DDLNZB_API_URL nicht.\n"
        f"Block:\n{block}\n"
        "Ohne die Variable fällt der Bridge auf http://localhost:8000 zurück."
    )

    falsche = [
        zeile
        for zeile in block.splitlines()
        if re.match(r"^\s*-\s*API_URL=", zeile) or re.match(r"^\s*API_URL=", zeile)
    ]
    assert not falsche, (
        "Der Bridge-Service setzt die API-URL unter dem falschen Namen "
        f"`API_URL`: {falsche}.\n"
        "`app/config.py` liest ausschließlich `DDLNZB_API_URL`."
    )


def test_compose_zeigt_auf_den_api_service_im_compose_netz() -> None:
    """`localhost` zeigt aus dem Container auf den Container selbst."""
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    treffer = re.search(r"DDLNZB_API_URL=(\S+)", block)
    assert treffer, "Der Bridge-Service setzt DDLNZB_API_URL nicht."

    wert = treffer.group(1)
    assert "localhost" not in wert and "127.0.0.1" not in wert, (
        f"DDLNZB_API_URL zeigt auf {wert!r}. Aus dem Container ist das der "
        "Container selbst - der Bridge erreicht die API so nicht.\n"
        "Im Compose-Netz heißt der API-Service `api`."
    )


def test_compose_gibt_dem_bridge_seine_zugangsdaten() -> None:
    """Ohne Zugangsdaten startet der Bridge, lehnt aber jeden Aufruf ab."""
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    liest_env_file = bool(re.search(r"env_file:", block))
    environment = re.search(r"environment:\n((?:\s{6}-.*\n)+)", block)
    gesetzte = (
        set(re.findall(r"^\s*-\s*([A-Z0-9_]+)=", environment.group(1), re.M))
        if environment
        else set()
    )

    pflicht = {"DDLNZB_API_KEY", "MYJD_EMAIL", "MYJD_PASSWORD", "MYJD_DEVICE_NAME"}
    aus_environment = pflicht <= gesetzte

    assert aus_environment or liest_env_file, (
        "Der Bridge-Service bekommt seine Zugangsdaten nicht.\n"
        f"Block:\n{block}\n"
        "Erwartet wird entweder ein `env_file:` (bridge/.env pflegt genau die "
        "Variablen aus app/config.py) oder ein vollständiges `environment:`-"
        "Set mit DDLNZB_API_KEY, MYJD_EMAIL, MYJD_PASSWORD und "
        "MYJD_DEVICE_NAME."
    )


def test_bridge_env_datei_setzt_jede_pflichtvariable() -> None:
    """`bridge/.env` ist die Quelle für den lokalen Start - sie muss
    vollständig sein."""
    assert ENV_FILE.exists(), f"{ENV_FILE} fehlt. Die Datei ist versioniert."

    werte = _env_werte()
    fehlend = sorted(name for name in _pflicht_variablen() if not werte.get(name))

    assert not fehlend, (
        f"In bridge/.env fehlen nicht-leere Werte für: {fehlend}.\n"
        "Diese Variablen liest app/config.py ohne brauchbaren Default - ohne sie "
        "startet die Bridge funktional leer (API-Key fehlt, JDownloader "
        "verbindet sich nicht)."
    )


def test_readme_nennt_jede_pflichtvariable() -> None:
    """Die README muss den Vertrag nennen, den der Code liest."""
    readme = README_FILE.read_text()

    fehlend = sorted(name for name in _pflicht_variablen() if name not in readme)

    assert not fehlend, (
        f"bridge/README.md nennt diese von app/config.py gelesenen "
        f"Variablen nicht: {fehlend}.\n"
        "Wer dem Quick Start folgt, startet sonst eine Bridge ohne Zugang."
    )


def _enthaelt_variable(text: str, name: str) -> bool:
    """Ob `text` die Variable als eigenstaendiges Token nennt.

    Die Wortgrenze ist wichtig: `MYJD_EMAIL` enthaelt `JD_EMAIL` als
    Teilstring. Ohne die Grenze wuerde ein korrektes README als falsch
    gemeldet.
    """
    return re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", text) is not None


def test_readme_nennt_keine_src_variablen() -> None:
    """`src/` wird nicht gestartet - sein Configvertrag ist tot (#671)."""
    readme = README_FILE.read_text()

    treffer = [name for name in SRC_VARIABLEN if _enthaelt_variable(readme, name)]
    assert not treffer, (
        f"bridge/README.md nennt die src/-Variablen {treffer}.\n"
        "Der gestartete Pfad ist app/ und liest MYJD_* und DDLNZB_API_KEY."
    )

    erfunden = [name for name in ERFUNDENE_PFAD_VARIABLEN if _enthaelt_variable(readme, name)]
    assert not erfunden, (
        f"bridge/README.md nennt die nicht existierenden Pfadvariablen {erfunden}.\n"
        "Es gibt nur DOWNLOAD_DIR und das fest verdrahtete CATEGORY_MAPPING."
    )


def test_readme_nennt_den_sabnzbd_port_6789() -> None:
    """Der Container lauscht fest auf 6789 (Dockerfile-CMD)."""
    readme = README_FILE.read_text()

    assert "6789" in readme, (
        "bridge/README.md nennt den SABnzbd-Port 6789 nicht.\n"
        "Das Image startet uvicorn per CMD mit --host 0.0.0.0 --port 6789 - "
        "BRIDGE_PORT gilt nur für lokale Läufe."
    )
