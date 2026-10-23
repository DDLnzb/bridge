"""Zustandsübergänge der Queue.

Deckt #383 ab (Persistenz bei `fail_download` / `mark_manual_download`) und
#385 ab (`mode=retry` bei abgeschlossenen Downloads und Verlust der Links).

Geprüft wird Verhalten, nicht Quelltext: jeder Test bewegt einen Eintrag, liest
den Zustand neu aus der State-Datei und prüft das Ergebnis.

Mit #659 trägt `status` nur noch einen Wert aus dem SABnzbd-Vokabular; der
ausführliche Text steht in `message`. Die Größe wird in Bytes geführt
(`size_bytes`), nicht mehr als Text.
"""

import json

LINKS = ["https://example.invalid/a.bin", "https://example.invalid/b.bin"]
GROSSE = 700 * 1024 * 1024


def _queue_item(module, nzo_id="nzo-1", links=None):
    item = module.QueueItem(
        nzo_id=nzo_id,
        filename="Some.Release-GRP",
        category="movies",
        size_bytes=GROSSE,
        sizeleft_bytes=GROSSE,
        links=list(links or []),
    )
    module.download_queue[nzo_id] = item
    return item


def _retry_payload(response):
    return json.loads(response.body)


# --- #383: der Zustand muss einen Neustart überleben -------------------


def test_fail_download_ueberlebt_den_neustart(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)

    module.fail_download("nzo-1", "Hoster down")

    assert "nzo-1" not in module.download_queue, "Eintrag muss aus der Queue raus"
    reloaded = fresh_import()
    assert "nzo-1" in reloaded.download_history, (
        "Fehlgeschlagener Download ist nach dem Neustart spurlos verschwunden - "
        "fail_download() persistiert den Zustand nicht"
    )
    assert reloaded.download_history["nzo-1"].status == "Failed"
    assert reloaded.download_history["nzo-1"].message == "Hoster down"


def test_mark_manual_download_ueberlebt_den_neustart(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)

    module.mark_manual_download("nzo-1", "Manual download required")

    reloaded = fresh_import()
    assert "nzo-1" in reloaded.download_history
    assert reloaded.download_history["nzo-1"].status == "Completed"
    assert reloaded.download_history["nzo-1"].message == "Manual download required"


def test_complete_download_ueberlebt_den_neustart(fresh_import):
    """Regression: der gemeinsame Übergang darf complete_download nicht brechen."""
    module = fresh_import()
    _queue_item(module)

    module.complete_download("nzo-1", "/downloads/complete")

    reloaded = fresh_import()
    assert "nzo-1" in reloaded.download_history
    entry = reloaded.download_history["nzo-1"]
    assert entry.status == "Completed"
    assert entry.storage == "/downloads/complete"


def test_historie_behaelt_die_links_aus_der_queue(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)

    module.fail_download("nzo-1", "timeout")

    assert module.download_history["nzo-1"].links == LINKS


# --- #385: retry ---------------------------------------------------------


def test_retry_lehnt_abgeschlossene_downloads_ab(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.complete_download("nzo-1", "/downloads/complete")

    payload = _retry_payload(module._retry_item("nzo-1", "json"))

    assert payload["status"] is False
    assert payload["error"] == "Item already completed: nzo-1"
    assert "nzo-1" not in module.download_queue, (
        "Ein abgeschlossener Download darf nicht erneut in die Queue"
    )
    assert "nzo-1" in module.download_history, "der Historieneintrag muss bestehen bleiben"


def test_retry_nimmt_fehlgeschlagene_downloads_wieder_auf(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.fail_download("nzo-1", "Hoster down")

    payload = _retry_payload(module._retry_item("nzo-1", "json"))

    assert payload["status"] is True
    assert "nzo-1" in module.download_queue
    assert "nzo-1" not in module.download_history


def test_retry_behaelt_links_und_groesse(fresh_import):
    """Ohne übernommene Links startet der Wiederholungsversuch ohne Quelle."""
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.fail_download("nzo-1", "Hoster down")

    module._retry_item("nzo-1", "json")

    requeued = module.download_queue["nzo-1"]
    assert requeued.links == LINKS
    assert requeued.size_bytes == GROSSE
    assert requeued.sizeleft_bytes == GROSSE
    assert requeued.filename == "Some.Release-GRP"


def test_retry_ueberlebt_den_neustart(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.fail_download("nzo-1", "Hoster down")
    module._retry_item("nzo-1", "json")

    reloaded = fresh_import()
    assert "nzo-1" in reloaded.download_queue
    assert reloaded.download_queue["nzo-1"].links == LINKS
