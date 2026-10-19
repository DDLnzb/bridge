"""Cloud-Link-Abruf und Fortschrittsberechnung im Download-Handler.

Deckt #374 ab (URL-Bildung gegen die Release-ID) und #384 ab
(ZeroDivisionError bei `bytesTotal == 0`). Für den HTTP-Teil wird
`httpx.AsyncClient` durch einen Mitschnitt ersetzt — es geht kein Request
raus.

Wichtig: hier wird bewusst **nicht** neu importiert. `download_handler` hält
eine Modul-Referenz auf `download_queue`; ein Re-Import von `app.sabnzbd_api`
würde ein neues Dict erzeugen, und der Handler sähe den Testeintrag nicht.
Das würde den Test stillschweigend grün färben. Stattdessen wird genau das Dict
befüllt, das auch der Handler sieht — abgesichert über die Prüfung, dass
`get_packages` wirklich aufgerufen wurde.
"""

import pytest

from app.download_handler import download_handler
from app.sabnzbd_api import QueueItem, download_queue

RELEASE_ID = "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b"
PAKETT_NAME = "Some.Release-GRP"


class _Antwort:
    def __init__(self, nutzlast, status_code=200):
        self.status_code = status_code
        self._nutzlast = nutzlast
        self.text = str(nutzlast)

    def json(self):
        return self._nutzlast


class _MitschnittClient:
    def __init__(self, aufrufe, nutzlast, status_code=200):
        self._aufrufe = aufrufe
        self._nutzlast = nutzlast
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self._aufrufe.append({"url": url, **kwargs})
        return _Antwort(self._nutzlast, self._status_code)


class _LoopVerlassen(Exception):
    """Signal: der Monitoring-Loop hat mindestens einen Durchlauf geschafft."""


@pytest.fixture
def aufrufe(monkeypatch):
    """Fängt jeden Cloud-Aufruf ab und liefert einen Link zurück."""
    aufrufe = []

    def _client_factory(*args, **kwargs):
        return _MitschnittClient(aufrufe, {"links": [{"url": "https://example.invalid/a.bin"}]})

    monkeypatch.setattr("app.download_handler.httpx.AsyncClient", _client_factory)
    return aufrufe


@pytest.fixture
def queue_eintrag():
    """Legt einen Queue-Eintrag an - in genau dem Dict, das der Handler sieht."""
    download_queue.clear()
    download_queue["nzo-jd"] = QueueItem(
        nzo_id="nzo-jd",
        filename=PAKETT_NAME,
        category="movies",
    )
    yield download_queue["nzo-jd"]
    download_queue.clear()


async def _einen_durchlauf(monkeypatch, paket):
    """Lässt den Monitoring-Loop genau einmal laufen und bricht danach ab."""
    abrufe = []

    async def _packages():
        abrufe.append(1)
        return [paket]

    async def _sleep(_dauer):
        raise _LoopVerlassen

    monkeypatch.setattr("app.jdownloader.jdownloader.get_packages", _packages)
    monkeypatch.setattr("app.download_handler.asyncio.sleep", _sleep)

    with pytest.raises(_LoopVerlassen):
        await download_handler._monitor_jdownloader("nzo-jd", PAKETT_NAME)

    assert len(abrufe) == 1, (
        "Der Monitoring-Loop hat den Eintrag nicht gesehen - der Test prueft nichts"
    )


# --- #374: URL-Bildung ---------------------------------------------------


async def test_cloud_url_nimmt_die_release_id(aufrufe):
    links = await download_handler._fetch_links_from_cloud(RELEASE_ID)

    assert links == ["https://example.invalid/a.bin"]
    assert aufrufe[0]["url"].endswith(f"/api/v1/links/{RELEASE_ID}")


async def test_cloud_url_sendet_keine_query_parameter(aufrufe):
    """Der Mirror-Filter gehörte zu einem by-source-Pfad, den es nicht gibt."""
    await download_handler._fetch_links_from_cloud(RELEASE_ID)

    assert "params" not in aufrufe[0], (
        "Es werden Query-Parameter an einen Endpunkt geschickt, der sie nicht kennt"
    )


async def test_release_id_wird_ins_pfadsegment_kodiert(aufrufe):
    """Ungeprüfter Payload-Inhalt landet sonst unkodiert im Pfad."""
    await download_handler._fetch_links_from_cloud("../../admin?x=1")

    assert aufrufe[0]["url"].endswith("/api/v1/links/..%2F..%2Fadmin%3Fx%3D1")


async def test_leere_release_id_ruft_die_api_nicht(aufrufe):
    """Früher ging das in den by-source-Zweig und endete in einem 404."""
    links = await download_handler._fetch_links_from_cloud("")

    assert links == []
    assert aufrufe == [], "Ohne Release-ID darf kein Request rausgehen"


# --- #384: Fortschrittsberechnung ---------------------------------------


@pytest.mark.parametrize(
    ("bytes_loaded", "bytes_total", "erwartet"),
    [
        (0, 0, 20),
        (500, 0, 20),
        (0, 100, 20),
        (50, 100, 60),
        (100, 100, 100),
        (250, 100, 100),
    ],
)
async def test_fortschritt_ueberlebt_ungueltige_gesamtgroesse(
    monkeypatch, queue_eintrag, bytes_loaded, bytes_total, erwartet
):
    await _einen_durchlauf(
        monkeypatch,
        {
            "name": PAKETT_NAME,
            "bytesLoaded": bytes_loaded,
            "bytesTotal": bytes_total,
            "finished": False,
        },
    )

    assert queue_eintrag.percentage == erwartet


async def test_fehlende_gesamtgroesse_bricht_den_handler_nicht_ab(monkeypatch, queue_eintrag):
    """Ohne bytesTotal hängt die Division durch Null den ganzen Loop auf."""
    await _einen_durchlauf(monkeypatch, {"name": PAKETT_NAME, "bytesLoaded": 10})

    assert queue_eintrag.percentage == 20
