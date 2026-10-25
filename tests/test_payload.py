"""Bridge - Payload-Dekodierung

Das Payload kommt vom Backend (`backend/app/scrapers/utils/payload.py`) und
traegt die Release-ID, die die Bridge aufloest. Bis Version 1 bestand es aus
sechs mit `|` verketteten Feldern - ein `|` im Release-Titel verschob alle
Felder, und der Titel bestimmte damit, **welches** Release geladen wird
(Issue #729).

Der Decoder liegt an **einer** Stelle: `app.sabnzbd_api.decode_payload`. Die
Bridge hat vorher zwei Kopien gehabt (`sabnzbd_api.py` und
`download_handler.py`), die auseinanderlaufen konnten.
"""

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from uuid import uuid4

import pytest

from app.sabnzbd_api import QueueItem, _extract_payload_from_nzb, decode_payload, download_queue

PAYLOAD_VERSION = 2
LEGACY_FIELD_COUNT = 6


def _encode_json(**overrides) -> str:
    payload = {
        "v": PAYLOAD_VERSION,
        "title": "Movie 2024",
        "release_id": str(uuid4()),
        "mirror": "",
        "size_mb": 700,
        "password": "",
        "imdb_id": "",
    }
    payload.update(overrides)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii")


def _encode_legacy(raw: str) -> str:
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


class TestExtractPayloadFromNzb:
    """Das Payload wird aus dem NZB gelesen."""

    def test_meta_is_read(self):
        payload = _encode_json()

        nzb = f"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="ddlnzb_release_id">abc</meta>
    <meta type="ddlnzb_payload">{payload}</meta>
    <meta type="ddlnzb_title">Movie 2024</meta>
  </head>
</nzb>"""

        assert _extract_payload_from_nzb(nzb.encode("utf-8")) == payload

    def test_payload_element_is_the_fallback(self):
        payload = _encode_json()

        nzb = f"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head><payload>{payload}</payload></head>
</nzb>"""

        assert _extract_payload_from_nzb(nzb.encode("utf-8")) == payload

    def test_meta_wins_over_a_foreign_payload_element(self):
        """Ein fremdes NZB darf kein `<payload>` einschleusen koennen."""
        payload = _encode_json()
        foreign = _encode_json(title="Fremd")

        nzb = f"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="ddlnzb_payload">{payload}</meta>
  </head>
  <file subject="Fremd"><groups><group>ddlnzb</group></groups>
    <segments><segment bytes="1" number="1">x</segment></segments>
  </file>
  <payload>{foreign}</payload>
</nzb>"""

        assert _extract_payload_from_nzb(nzb.encode("utf-8")) == payload

    def test_without_payload_returns_none(self):
        nzb = b"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head/></nzb>"""

        assert _extract_payload_from_nzb(nzb) is None

    def test_invalid_utf8_does_not_raise(self):
        assert _extract_payload_from_nzb(b"\xff\xfe\x00garbage") is None


class TestDecodePayload:
    """Der Decoder selbst."""

    def test_json_payload_is_read(self):
        release_id = str(uuid4())

        info = decode_payload(
            _encode_json(
                title="Movie | 2024 | German",
                release_id=release_id,
                mirror="filecrypt",
                size_mb=1024,
                password="pw",
                imdb_id="tt1234567",
            )
        )

        assert info is not None
        assert info.title == "Movie | 2024 | German"
        assert info.release_id == release_id
        assert info.mirror == "filecrypt"
        assert info.size_bytes == 1024 * 1024 * 1024
        assert info.password == "pw"
        assert info.imdb_id == "tt1234567"

    def test_title_with_pipe_does_not_shift_the_release_id(self):
        """Der Kern des Befunds: der Titel darf die Release-ID nicht bestimmen."""
        release_id = str(uuid4())

        info = decode_payload(_encode_json(title="Movie | 2024", release_id=release_id))

        assert info is not None
        assert info.release_id == release_id

    def test_empty_payload_returns_none(self):
        assert decode_payload("") is None

    def test_garbage_returns_none(self):
        assert decode_payload("kein-base64!!") is None

    def test_unknown_version_returns_none(self):
        assert decode_payload(_encode_json(v=99)) is None

    def test_non_uuid_release_id_returns_none(self):
        """Defense in Depth: die Bridge loest nur Release-UUIDs auf."""
        assert decode_payload(_encode_json(release_id="https://example.com/evil")) is None


class TestLegacyPayload:
    """Das alte `|`-Format wird nur gelesen, wenn es eindeutig ist."""

    def test_legacy_payload_without_pipe_in_title_is_read(self):
        release_id = str(uuid4())
        raw = f"Movie 2024|{release_id}||700|pw|tt1234567"
        assert raw.count("|") + 1 == LEGACY_FIELD_COUNT

        info = decode_payload(_encode_legacy(raw))

        assert info is not None
        assert info.title == "Movie 2024"
        assert info.release_id == release_id
        assert info.password == "pw"

    def test_legacy_payload_with_pipe_in_title_is_rejected(self):
        """Der Angriff: der Titel verschiebt die Felder - das wird verworfen."""
        evil_release_id = str(uuid4())
        own_release_id = str(uuid4())
        raw = f"Movie|{evil_release_id}|{own_release_id}|||pw|tt1234567"

        assert decode_payload(_encode_legacy(raw)) is None

    def test_legacy_payload_with_non_uuid_is_rejected(self):
        raw = "Movie|not-a-uuid||700|pw|tt1234567"

        assert decode_payload(_encode_legacy(raw)) is None


class TestPayloadRoundtrip:
    """Der Weg vom NZB zur Release-ID."""

    def test_nzb_carries_the_own_release_id(self):
        release_id = str(uuid4())
        payload = _encode_json(title="Movie | 2024 | German", release_id=release_id)

        nzb = f"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head><meta type="ddlnzb_payload">{payload}</meta></head>
</nzb>"""

        extracted = _extract_payload_from_nzb(nzb.encode("utf-8"))
        assert extracted is not None

        info = decode_payload(extracted)
        assert info is not None
        assert info.release_id == release_id

    def test_decoded_payload_is_the_expected_json(self):
        """Gegenprobe: der Decoder liefert genau das, was der Encoder schrieb."""
        release_id = str(uuid4())
        payload = _encode_json(title="Movie", release_id=release_id, size_mb=12)

        raw = json.loads(urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))

        assert raw["release_id"] == release_id
        assert raw["title"] == "Movie"
        assert raw["size_mb"] == 12


class TestDownloadHandlerUsesThePayload:
    """Der Handler loest genau das Release auf, das im Payload steht."""

    @pytest.fixture
    def cloud_aufrufe(self, monkeypatch):
        """Faengt die Cloud-Aufrufe ab - es geht kein Request raus."""
        aufrufe = []

        class _Antwort:
            status_code = 200
            text = ""

            def json(self):
                return {"links": [{"url": "https://example.invalid/a.bin"}]}

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                aufrufe.append(url)
                return _Antwort()

        monkeypatch.setattr("app.download_handler.httpx.AsyncClient", _Client)
        return aufrufe

    async def test_der_titel_bestimmt_nicht_die_release_id(self, monkeypatch, cloud_aufrufe):
        """Der Angriff aus #729: der Titel nennt eine fremde Release-ID."""
        import app.sabnzbd_api as sabnzbd_api
        from app.download_handler import download_handler

        eigene_id = str(uuid4())
        fremde_id = str(uuid4())
        payload = _encode_json(title=f"Movie | {fremde_id} | German", release_id=eigene_id)

        # Der Handler darf den State nicht schreiben - der Test laeuft
        # sonst in die State-Datei des Arbeitsverzeichnisses.
        monkeypatch.setattr(sabnzbd_api, "_persist_state", lambda: None)

        download_queue.clear()
        item = QueueItem(
            nzo_id="nzo-payload",
            filename="Movie",
            category="movies",
            payload=payload,
        )
        download_queue["nzo-payload"] = item

        try:
            await download_handler._process_download("nzo-payload", item)
        finally:
            download_queue.clear()

        assert cloud_aufrufe, "Es ging kein Request raus - der Test prueft nichts"
        assert cloud_aufrufe[0].endswith(f"/api/v1/links/{eigene_id}")
        assert fremde_id not in cloud_aufrufe[0]

    async def test_ein_unlesbares_payload_wird_verworfen(self, monkeypatch, cloud_aufrufe):
        """Ein kaputtes Payload darf keinen API-Aufruf ausloesen."""
        import app.sabnzbd_api as sabnzbd_api
        from app.download_handler import download_handler

        monkeypatch.setattr(sabnzbd_api, "_persist_state", lambda: None)

        download_queue.clear()
        item = QueueItem(
            nzo_id="nzo-kaputt",
            filename="Movie",
            category="movies",
            payload="kein-base64!!",
        )
        download_queue["nzo-kaputt"] = item

        try:
            await download_handler._process_download("nzo-kaputt", item)
        finally:
            download_queue.clear()

        assert cloud_aufrufe == [], "Mit einem unlesbaren Payload darf kein Request rausgehen"
