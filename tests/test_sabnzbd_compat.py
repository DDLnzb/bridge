"""SABnzbd-Kompatibilität der Bridge — Issues #657, #658, #659.

Drei Verträge, die Sonarr/Radarr gegen die Bridge prüfen:

* **#657** — der Client schickt die nzo_id im Parameter `value` und entfernt
  Einträge über `mode=queue&name=delete` bzw. `mode=history&name=delete`.
  Die Bridge kannte nur `nzo_id` und kannte kein `name=delete`: Löschen aus
  der History war unmöglich, ein `mode=queue` mit `name=delete` wäre in der
  Bridge als reines Listing gelaufen.
* **#658** — Sonarr/Radarr rechnen die Größe aus `mb`/`mbleft` (Queue) bzw.
  `bytes` (History). Ohne diese Felder zeigen sie jede Datei als 0 MB.
* **#659** — Newtonsoft bricht die **gesamte** Antwort ab, sobald ein Slot
  einen Status ausserhalb des `SabnzbdDownloadStatus`-Vokabulars trägt. Der
  freie Text ("Fetching links...") gehört deshalb in ein eigenes Feld.

Geprüft wird Verhalten, nicht Quelltext: jeder Test geht über die HTTP-API,
die ein *arr-Client benutzt.
"""

import pytest
from fastapi.testclient import TestClient

# `app.sabnzbd_api` wird hier **einmalig auf Modulebene** gebunden. Die anderen
# Testmodule rufen `fresh_import()` auf, das `app.sabnzbd_api` aus
# `sys.modules` entfernt und neu importiert - ab da waere ein `import app.sabnzbd_api`
# *innerhalb* einer Testfunktion ein anderes Modul als das, in dem `app.main`
# den Router haengt. Die beiden Mengen waeren dann verschiedene Objekte und die
# Tests liefeuern ins Leere.
import app.sabnzbd_api as sab
from app.config import config
from app.main import app

KEY = "test-bridge-key"
GROSSE = 1500 * 1024 * 1024


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient mit API-Key und einem State-Pfad im tmp-Verzeichnis.

    Der State-Pfad wird auf `app.sabnzbd_api.STATE_PATH` gesetzt, nicht über
    die Env-Variable: `STATE_PATH` wird beim Modulimport gebildet und danach
    nicht mehr neu gelesen.
    """
    monkeypatch.setattr(config, "DDLNZB_API_KEY", KEY)
    monkeypatch.setattr(sab, "STATE_PATH", tmp_path / "bridge_state.json")
    sab.download_queue.clear()
    sab.download_history.clear()
    yield TestClient(app)
    sab.download_queue.clear()
    sab.download_history.clear()


def _queue_eintrag(nzo_id="nzo-1", size_bytes=GROSSE):
    """Einen Queue-Eintrag anlegen (ohne HTTP - das ist der Ausgangszustand)."""
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


# --- #657: value und name=delete ----------------------------------------


def test_queue_delete_per_value_entfernt_den_eintrag(client):
    """`mode=queue&name=delete&value=<nzo_id>` nimmt den Eintrag aus der Queue."""
    _queue_eintrag("nzo-1")

    antwort = client.get(
        "/api",
        params={"mode": "queue", "name": "delete", "value": "nzo-1", "apikey": KEY},
    )

    assert antwort.json()["status"] is True
    assert "nzo-1" not in sab.download_queue


def test_queue_ohne_name_delete_bleibt_ein_listing(client):
    """`mode=queue` ohne `name=delete` listet nur - die Queue bleibt stehen."""
    _queue_eintrag("nzo-1")

    slots = _queue_slots(client)

    assert len(slots) == 1
    assert "nzo-1" in sab.download_queue


def test_queue_delete_ohne_value_leert_die_queue_nicht(client):
    """Ein `name=delete` ohne `value` darf nicht die ganze Queue leeren."""
    _queue_eintrag("nzo-1")

    antwort = client.get("/api", params={"mode": "queue", "name": "delete", "apikey": KEY})

    assert antwort.json()["status"] is False
    assert "nzo-1" in sab.download_queue


def test_history_delete_per_value_entfernt_den_eintrag(client):
    """`mode=history&name=delete&value=<nzo_id>` nimmt den Eintrag aus der History."""
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
    """`mode=delete&value=<nzo_id>` funktioniert wie bisher - nur mit `value`."""
    _queue_eintrag("nzo-1")

    antwort = client.get("/api", params={"mode": "delete", "value": "nzo-1", "apikey": KEY})

    assert antwort.json()["status"] is True
    assert "nzo-1" not in sab.download_queue


def test_retry_per_value_nimmt_den_eintrag_wieder_auf(client):
    """`mode=retry&value=<nzo_id>` holt einen fehlgeschlagenen Download zurück."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")

    antwort = client.get("/api", params={"mode": "retry", "value": "nzo-1", "apikey": KEY})

    assert antwort.json()["status"] is True
    assert "nzo-1" in sab.download_queue
    assert "nzo-1" not in sab.download_history


# --- #658: Groesse in Zahlen --------------------------------------------


def test_queue_slot_meldet_groesse_in_megabytes(client):
    """`mb` und `mbleft` sind Zahlen - Sonarr/Radarr rechnen daraus die Groesse."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)

    slot = _queue_slots(client)[0]

    assert slot["mb"] == pytest.approx(1500.0)
    assert slot["mbleft"] == pytest.approx(1500.0)
    assert slot["size"] == "1500.00 MB"


def test_sizeleft_schrumpft_mit_dem_fortschritt(client):
    """`sizeleft` darf nicht dauerhaft auf dem Startwert stehen."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)

    sab.update_progress("nzo-1", 40, "Downloading...")

    slot = _queue_slots(client)[0]
    assert slot["mbleft"] == pytest.approx(900.0)
    assert slot["mbleft"] < slot["mb"]


def test_sizeleft_nimmt_die_echten_restbytes(client):
    """Sind echte Byte-Zahlen bekannt, ist die Restgroesse der tatsaechliche Rest."""
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
    """Die History meldet die Groesse in Bytes unter `bytes`."""
    _queue_eintrag("nzo-1", size_bytes=GROSSE)
    sab.complete_download("nzo-1", "/downloads/Some.Release-GRP")

    slot = _history_slots(client)[0]
    assert slot["bytes"] == GROSSE
    assert slot["size"] == "1500.00 MB"


# --- #659: Status-Vokabular ---------------------------------------------


def test_jeder_queue_slot_trägt_einen_gültigen_status(client):
    """Ein Status ausserhalb des Vokabulars killt die *gesamte* Antwort."""
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
    """Der Klartext geht nicht verloren - er wandert in `message`."""
    _queue_eintrag("nzo-1")
    sab.update_progress("nzo-1", 50, "Downloading via JDownloader...")

    slot = _queue_slots(client)[0]
    assert slot["status"] == "Downloading"
    assert slot["message"] == "Downloading via JDownloader..."


def test_fehlschlag_meldet_failed_mit_fehlermeldung(client):
    """Ein Fehlschlag ist `Failed` - der Fehlertext steht in `fail_message`."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")

    slot = _history_slots(client)[0]
    assert slot["status"] == "Failed"
    assert slot["status"] in sab.SAB_STATUSES
    assert slot["fail_message"] == "Hoster down"


def test_links_ohne_jdownloader_werden_completed_gemeldet(client):
    """Der Erfolgsfall "Links bereit, kein JDownloader" ist `Completed`."""
    _queue_eintrag("nzo-1")
    sab.update_progress("nzo-1", 100, "Links ready")
    sab.complete_download("nzo-1", "", "Links available (3 URLs)")

    slot = _history_slots(client)[0]
    assert slot["status"] == "Completed"
    assert slot["fail_message"] == "Links available (3 URLs)"


def test_jeder_history_slot_trägt_einen_gültigen_status(client):
    """Auch die History muss ausschliesslich gueltige Status liefern."""
    _queue_eintrag("nzo-1")
    sab.fail_download("nzo-1", "Hoster down")
    _queue_eintrag("nzo-2")
    sab.complete_download("nzo-2", "/downloads/x")

    slots = _history_slots(client)

    assert len(slots) == 2
    for slot in slots:
        assert slot["status"] in sab.SAB_STATUSES
