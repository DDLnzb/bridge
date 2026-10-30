"""Cloud link retrieval and progress calculation in the download handler.

Covers #374 (URL construction against the release ID) and #384
(ZeroDivisionError with `bytesTotal == 0`). For the HTTP part,
`httpx.AsyncClient` is replaced by a recorder — no request goes out.

Important: here we deliberately do **not** re-import. `download_handler` holds
a module reference to `download_queue`; a re-import of `app.sabnzbd_api`
would create a new dict, and the handler would not see the test entry.
That would silently turn the test green. Instead exactly the dict is filled
that the handler also sees — safeguarded by the check that `get_packages`
really was called.
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
    """Signal: the monitoring loop completed at least one iteration."""


@pytest.fixture
def aufrufe(monkeypatch):
    """Captures every cloud call and returns a link."""
    aufrufe = []

    def _client_factory(*args, **kwargs):
        return _MitschnittClient(aufrufe, {"links": [{"url": "https://example.invalid/a.bin"}]})

    monkeypatch.setattr("app.download_handler.httpx.AsyncClient", _client_factory)
    return aufrufe


@pytest.fixture
def queue_eintrag():
    """Creates a queue entry — in exactly the dict that the handler sees."""
    download_queue.clear()
    download_queue["nzo-jd"] = QueueItem(
        nzo_id="nzo-jd",
        filename=PAKETT_NAME,
        category="movies",
    )
    yield download_queue["nzo-jd"]
    download_queue.clear()


async def _einen_durchlauf(monkeypatch, paket):
    """Lets the monitoring loop run exactly once and aborts afterwards."""
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

    assert len(abrufe) == 1, "The monitoring loop did not see the entry - the test checks nothing"


# --- #374: URL construction ------------------------------------------------


async def test_cloud_url_nimmt_die_release_id(aufrufe):
    daten = await download_handler._fetch_release_data(RELEASE_ID)

    assert [link["url"] for link in daten["links"]] == ["https://example.invalid/a.bin"]
    assert aufrufe[0]["url"].endswith(f"/api/v1/links/{RELEASE_ID}")


async def test_cloud_url_sendet_keine_query_parameter(aufrufe):
    """The mirror filter belonged to a by-source path that does not exist."""
    await download_handler._fetch_release_data(RELEASE_ID)

    assert "params" not in aufrufe[0], (
        "Query parameters are sent to an endpoint that does not know them"
    )


async def test_release_id_wird_ins_pfadsegment_kodiert(aufrufe):
    """Unchecked payload content would otherwise land unencoded in the path."""
    await download_handler._fetch_release_data("../../admin?x=1")

    assert aufrufe[0]["url"].endswith("/api/v1/links/..%2F..%2Fadmin%3Fx%3D1")


async def test_leere_release_id_ruft_die_api_nicht(aufrufe):
    """Earlier this went into the by-source branch and ended in a 404."""
    daten = await download_handler._fetch_release_data("")

    assert daten is None
    assert aufrufe == [], "Without a release ID no request may go out"


# --- #384: progress calculation -------------------------------------------


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
    """Without bytesTotal the division by zero stalls the whole loop."""
    await _einen_durchlauf(monkeypatch, {"name": PAKETT_NAME, "bytesLoaded": 10})

    assert queue_eintrag.percentage == 20
