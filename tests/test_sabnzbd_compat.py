"""SABnzbd compatibility of the bridge — Issues #657, #658, #659.

Three contracts that Sonarr/Radarr check against the bridge:

* **#657** — the client sends the nzo_id in the parameter `value` and removes
  entries via `mode=queue&name=delete` respectively `mode=history&name=delete`.
  The bridge only knew `nzo_id` and did not know `name=delete`: deleting from
  the history was impossible, a `mode=queue` with `name=delete` would have run
  as a plain listing in the bridge.
* **#658** — Sonarr/Radarr calculate the size from `mb`/`mbleft` (queue)
  respectively `bytes` (history). Without these fields they show every file
  as 0 MB.
* **#659** — Newtonsoft aborts the **entire** response as soon as a slot
  carries a status outside the `SabnzbdDownloadStatus` vocabulary. The free
  text ("Fetching links...") therefore belongs in a separate field.

What is checked is behaviour, not source text: every test goes through the
HTTP API that an *arr client uses.
"""

import pytest
from fastapi.testclient import TestClient

# `app.sabnzbd_api` is bound **once at module level** here. The other test
# modules call `fresh_import()`, which removes `app.sabnzbd_api` from
# `sys.modules` and re-imports it - from that point on an `import app.sabnzbd_api`
# *inside* a test function would be a different module than the one in which
# `app.main` mounts the router. The two sets would then be different objects
# and the tests would run into the void.
import app.sabnzbd_api as sab
from app.config import config
from app.main import app

KEY = "test-bridge-key"
GROSSE = 1500 * 1024 * 1024


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with API key and a state path in the tmp directory.

    The state path is set on `app.sabnzbd_api.STATE_PATH`, not via the env
    variable: `STATE_PATH` is built at module import time and is not read
    again afterwards.
    """
    monkeypatch.setattr(config, "DDLNZB_API_KEY", KEY)
    monkeypatch.setattr(sab, "STATE_PATH", tmp_path / "bridge_state.json")
    sab.download_queue.clear()
    sab.download_history.clear()
    yield TestClient(app)
    sab.download_queue.clear()
    sab.download_history.clear()


def _queue_eintrag(nzo_id="nzo-1", size_bytes=GROSSE):
    """Creates a queue entry (without HTTP - that is the initial state)."""
    item = sab.QueueItem(
        nzo_id=nzo_id,
        filename="Some.Release-GRP",
        category="movies",
        size_bytes=size_bytes,
        sizeleft_bytes=size_bytes,
    )
    sab.download_queue[nzo_id] = item
    return item


def _queue_slots(client):
    antwort = client.get("/api", params={"mode": "queue", "apikey": KEY})
    return antwort.json()["queue"]["slots"]


def _history_slots(client):
    antwort = client.get("/api", params={"mode": "history", "apikey": KEY})
    return antwort.json()["history"]["slots"]


# --- #657: value and name=delete ------------------------------------------


def test_queue_delete_per_value_entfernt_den_eintrag(client):
    """`mode=queue&name=delete&value=<nzo_id>` removes the entry from the queue."""
    _queue_eintrag("nzo-1")

    antwort = client.get(
        "/api",
        params={"mode": "queue", "name": "delete", "value": "nzo-1", "apikey": KEY},
    )

    assert antwort.json()["status"] is True
    assert "nzo-1" not in sab.download_queue


def test_queue_ohne_name_delete_bleibt_ein_listing(client):
    """`mode=queue` without `name=delete` only lists - the queue stays."""
    _queue_eintrag("nzo-1")

    slots = _queue_slots(client)

    assert len(slots) == 1
    assert "nzo-1" in sab.download_queue


def test_queue_delete_ohne_value_leert_die_queue_nicht(client):
    """A `name=delete` without `value` must not empty the whole queue."""
    _queue_eintrag("nzo-1")

    antwort = client.get("/api", params={"mode": "queue", "name": "delete", "apikey": KEY})

    assert antwort.json()["status"] is False
    assert "nzo-1" in sab.download_queue


def test_history_delete_per_value_entfernt_den_eintrag(client):
    """`mode=history&name=delete&value=<nzo_id>` removes the entry from the history."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")
    assert "nzo-1" in sab.download_history

    antwort = client.get(
        "/api",
        params={"mode": "history", "name": "delete", "value": "nzo-1", "apikey": KEY},
    )

    assert antwort.json()["status"] is True
    assert "nzo-1" not in sab.download_history


def test_delete_per_value_entfernt_aus_der_queue(client):
    """`mode=delete&value=<nzo_id>` works as before - only with `value`."""
    _queue_eintrag("nzo-1")

    antwort = client.get("/api", params={"mode": "delete", "value": "nzo-1", "apikey": KEY})

    assert antwort.json()["status"] is True
    assert "nzo-1" not in sab.download_queue


def test_retry_per_value_nimmt_den_eintrag_wieder_auf(client):
    """`mode=retry&value=<nzo_id>` retrieves a failed download."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")

    antwort = client.get("/api", params={"mode": "retry", "value": "nzo-1", "apikey": KEY})

    assert antwort.json()["status"] is True
    assert "nzo-1" in sab.download_queue
    assert "nzo-1" not in sab.download_history


# --- #658: size as numbers --------------------------------------------------


def test_queue_slot_meldet_groesse_in_megabytes(client):
    """`mb` and `mbleft` are numbers - Sonarr/Radarr calculate the size from them."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)

    slot = _queue_slots(client)[0]

    assert slot["mb"] == pytest.approx(1500.0)
    assert slot["mbleft"] == pytest.approx(1500.0)
    assert slot["size"] == "1500.00 MB"


def test_sizeleft_schrumpft_mit_dem_fortschritt(client):
    """`sizeleft` must not stay on the initial value permanently."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)

    sab.update_progress("nzo-1", 40, "Downloading...")

    slot = _queue_slots(client)[0]
    assert slot["mbleft"] == pytest.approx(900.0)
    assert slot["mbleft"] < slot["mb"]


def test_sizeleft_nimmt_die_echten_restbytes(client):
    """If real byte numbers are known, the remaining size is the actual remainder."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)

    sab.update_progress(
        "nzo-1",
        60,
        "Downloading...",
        bytes_loaded=500 * 1024 * 1024,
        bytes_total=GROSSE,
    )

    slot = _queue_slots(client)[0]
    assert slot["mbleft"] == pytest.approx(1000.0)


def test_history_slot_meldet_bytes(client):
    """The history reports the size in bytes under `bytes`."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)
    sab.complete_download("nzo-1", "/downloads/Some.Release-GRP")

    slot = _history_slots(client)[0]
    assert slot["bytes"] == GROSSE
    assert slot["size"] == "1500.00 MB"


# --- #659: status vocabulary -----------------------------------------------


def test_every_queue_slot_has_a_valid_status(client):
    """A status outside the vocabulary kills the *entire* response."""
    _queue_eintrag("nzo-1")
    sab.update_progress("nzo-1", 0, "Starting download...")
    sab.update_progress("nzo-1", 20, "Sending to JDownloader...")
    sab.update_progress("nzo-1", 50, "Downloading via JDownloader...")
    sab.update_progress("nzo-1", 100, "Links ready")

    slots = _queue_slots(client)

    assert slots
    for slot in slots:
        assert slot["status"] in sab.SAB_STATUSES


def test_der_freie_text_bleibt_im_message_feld(client):
    """The plain text does not get lost - it moves into `message`."""
    _queue_eintrag("nzo-1")
    sab.update_progress("nzo-1", 50, "Downloading via JDownloader...")

    slot = _queue_slots(client)[0]
    assert slot["status"] == "Downloading"
    assert slot["message"] == "Downloading via JDownloader..."


def test_fehlschlag_meldet_failed_mit_fehlermeldung(client):
    """A failure is `Failed` - the error text stands in `fail_message`."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")

    slot = _history_slots(client)[0]
    assert slot["status"] == "Failed"
    assert slot["status"] in sab.SAB_STATUSES
    assert slot["fail_message"] == "Hoster down"


def test_links_ohne_jdownloader_werden_completed_gemeldet(client):
    """The success case "links ready, no JDownloader" is `Completed`."""
    _queue_eintrag("nzo-1")
    sab.update_progress("nzo-1", 100, "Links ready")
    sab.complete_download("nzo-1", "", "Links available (3 URLs)")

    slot = _history_slots(client)[0]
    assert slot["status"] == "Completed"
    assert slot["fail_message"] == "Links available (3 URLs)"


def test_every_history_slot_has_a_valid_status(client):
    """The history must also deliver exclusively valid statuses."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")
    _queue_eintrag("nzo-2")
    sab.complete_download("nzo-2", "/downloads/x")

    slots = _history_slots(client)

    assert len(slots) == 2
    for slot in slots:
        assert slot["status"] in sab.SAB_STATUSES
