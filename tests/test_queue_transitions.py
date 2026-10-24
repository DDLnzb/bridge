"""State transitions of the queue.

Covers #383 (persistence on `fail_download` / `mark_manual_download`) and
#385 (`mode=retry` for completed downloads and lost links).

What is checked is behaviour, not source text: every test moves an entry,
reads the state again from the state file and checks the result.

With #659 `status` only carries one value from the SABnzbd vocabulary; the
verbose text stands in `message`. The size is tracked in bytes
(`size_bytes`), no longer as text.
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


# --- #383: the state must survive a restart --------------------------------


def test_fail_download_ueberlebt_den_neustart(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)

    module.fail_download("nzo-1", "Hoster down")

    assert "nzo-1" not in module.download_queue, "Entry must leave the queue"
    reloaded = fresh_import()
    assert "nzo-1" in reloaded.download_history, (
        "The failed download has vanished without a trace after the restart - "
        "fail_download() does not persist the state"
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
    """Regression: the shared transition must not break complete_download."""
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


# --- #385: retry -----------------------------------------------------------


def test_retry_lehnt_abgeschlossene_downloads_ab(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.complete_download("nzo-1", "/downloads/complete")

    payload = _retry_payload(module._retry_item("nzo-1", "json"))

    assert payload["status"] is False
    assert payload["error"] == "Item already completed: nzo-1"
    assert "nzo-1" not in module.download_queue, (
        "A completed download must not go back into the queue"
    )
    assert "nzo-1" in module.download_history, "the history entry must survive"


def test_retry_nimmt_fehlgeschlagene_downloads_wieder_auf(fresh_import):
    module = fresh_import()
    _queue_item(module, links=LINKS)
    module.fail_download("nzo-1", "Hoster down")

    payload = _retry_payload(module._retry_item("nzo-1", "json"))

    assert payload["status"] is True
    assert "nzo-1" in module.download_queue
    assert "nzo-1" not in module.download_history


def test_retry_behaelt_links_und_groesse(fresh_import):
    """Without carried-over links the retry starts without a source."""
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
