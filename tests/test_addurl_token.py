"""`mode=addurl` must resolve the NZB link from the Newznab feed (#789).

The *arr apps hand their download client the NZB link from the search
result. Since #694 that link carries a short-lived signed token instead of
the API key. The bridge could not decode it: the queue entry got no release
ID and the download ended with "Invalid payload".

What is checked is behaviour - the queue entry after the call and the
release the handler resolves - not the wording of a docstring.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

# `app.sabnzbd_api` is bound **once at module level** here: `fresh_import()`
# in `conftest.py` removes the module from `sys.modules` and imports it
# again - the router in `app.main` would then hold a different module than
# the test, and the queue would run into the void.
import app.sabnzbd_api as sab
from app.config import config
from app.download_handler import download_handler
from app.jdownloader import jdownloader
from app.main import app

KEY = "test-bridge-key"
RELEASE_ID = "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b"
USER_ID = "9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"
TITEL = "Some.Release-GRP"
GROSSE = 1500 * 1024 * 1024

#: The name the *arr app hands over. It is **not** the title of the release -
#: otherwise the fallback chain could not be told apart from the answer of the
#: link endpoint: the file name would answer the same way.
NZB_NAME = "Some.Release-GRP.nzb"


def _token(release_id: str = RELEASE_ID) -> str:
    """Builds a download token in the format of the backend.

    The signature is irrelevant for the bridge - it does not verify it. The
    format `<base64url(nutzlast)>.<base64url(signatur)>` is what matters.
    """
    nutzlast = {"v": 1, "rid": release_id, "uid": USER_ID, "exp": 9999999999}
    body = base64.urlsafe_b64encode(json.dumps(nutzlast).encode()).decode().rstrip("=")
    signatur = base64.urlsafe_b64encode(b"signatur").decode().rstrip("=")
    return f"{body}.{signatur}"


def _nzb_link(release_id: str = RELEASE_ID) -> str:
    """The NZB link as it stands in the Newznab feed."""
    return f"https://ddlnzb.pages.dev/api/download?payload={_token(release_id)}"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client with API key and a state path in the tmp directory."""
    monkeypatch.setattr(config, "DDLNZB_API_KEY", KEY)
    monkeypatch.setattr(sab, "STATE_PATH", tmp_path / "bridge_state.json")
    sab.download_queue.clear()
    sab.download_history.clear()
    yield TestClient(app)
    sab.download_queue.clear()
    sab.download_history.clear()


def test_addurl_setzt_die_release_id_aus_dem_token(client):
    """The queue entry carries the release ID of the token."""
    antwort = client.get(
        "/api",
        params={
            "mode": "addurl",
            "name": _nzb_link(),
            "nzbname": NZB_NAME,
            "apikey": KEY,
        },
    )

    assert antwort.status_code == 200
    assert antwort.json()["status"] is True

    nzo_id = antwort.json()["nzo_ids"][0]
    assert sab.download_queue[nzo_id].release_id == RELEASE_ID


def test_addurl_meldet_einen_unbekannten_token(client):
    """A token without a release ID is refused - nothing is queued."""
    antwort = client.get(
        "/api",
        params={
            "mode": "addurl",
            "name": "https://ddlnzb.pages.dev/api/download?payload=kein-token",
            "apikey": KEY,
        },
    )

    assert antwort.json()["status"] is False
    assert sab.download_queue == {}


async def test_der_handler_nimmt_titel_und_passwort_aus_der_api(monkeypatch, tmp_path):
    """The token carries the release ID only.

    Title, password and size belong to the release - they stand in the
    answer of the link endpoint and are used when the payload does not
    carry them.
    """
    monkeypatch.setattr(sab, "STATE_PATH", tmp_path / "bridge_state.json")
    aufrufe: list = []

    class _Antwort:
        status_code = 200
        text = ""

        def json(self):
            return {
                "release_id": RELEASE_ID,
                "title": TITEL,
                "links": [{"hoster": "example", "url": "https://example.invalid/a.bin"}],
                "password": "geheim",
                "size_bytes": GROSSE,
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            aufrufe.append({"url": url, **kwargs})
            return _Antwort()

    monkeypatch.setattr("app.download_handler.httpx.AsyncClient", lambda *a, **k: _Client())

    async def _add_links(**kwargs):
        aufrufe.append(kwargs)
        return {}

    async def _kein_monitoring(*args, **kwargs):
        return None

    monkeypatch.setattr(jdownloader, "device_id", "device-1")
    monkeypatch.setattr(jdownloader, "add_links", _add_links)
    monkeypatch.setattr(download_handler, "_monitor_jdownloader", _kein_monitoring)

    item = sab._enqueue_payload(_token(), NZB_NAME, "movies")
    await download_handler._process_download(item.nzo_id, item)

    assert aufrufe[0]["url"].endswith(f"/api/v1/links/{RELEASE_ID}")
    # The title of the release - **not** the file name the *arr app handed
    # over. The two are different on purpose: otherwise the fallback could
    # not be told apart from the answer of the endpoint.
    assert aufrufe[1]["package_name"] == TITEL
    assert aufrufe[1]["package_name"] != item.filename
    assert aufrufe[1]["extract_password"] == "geheim"
    assert item.size_bytes == GROSSE


async def test_der_dateiname_dient_nur_als_letzter_ausweg(monkeypatch, tmp_path):
    """Without a title in the answer the file name of the entry is used.

    The chain is: what the payload carries, then the answer of the link
    endpoint, then the file name of the queue entry.
    """
    monkeypatch.setattr(sab, "STATE_PATH", tmp_path / "bridge_state.json")
    aufrufe: list = []

    class _Antwort:
        status_code = 200
        text = ""

        def json(self):
            return {
                "release_id": RELEASE_ID,
                "links": [{"hoster": "example", "url": "https://example.invalid/a.bin"}],
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            aufrufe.append({"url": url, **kwargs})
            return _Antwort()

    monkeypatch.setattr("app.download_handler.httpx.AsyncClient", lambda *a, **k: _Client())

    async def _add_links(**kwargs):
        aufrufe.append(kwargs)
        return {}

    async def _kein_monitoring(*args, **kwargs):
        return None

    monkeypatch.setattr(jdownloader, "device_id", "device-1")
    monkeypatch.setattr(jdownloader, "add_links", _add_links)
    monkeypatch.setattr(download_handler, "_monitor_jdownloader", _kein_monitoring)

    item = sab._enqueue_payload(_token(), NZB_NAME, "movies")
    await download_handler._process_download(item.nzo_id, item)

    assert aufrufe[1]["package_name"] == NZB_NAME
    assert aufrufe[1]["extract_password"] is None
    assert item.size_bytes == 0
