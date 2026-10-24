"""Regression test for the state restore at bridge start.

Background: `_load_state()` in `sabnzbd_api.py` stood before the definitions of
`QueueItem` and `HistoryItem`. The resulting `NameError` lands in the
`except Exception` handler of `_load_state()` itself, so the import runs
cleanly - but queue and history stay empty on every start.

The test writes a state file, re-imports the module and checks that the
state actually arrives. No source-text assert.
"""

import json


def test_queue_und_historie_werden_beim_import_wiederhergestellt(state_file, fresh_import):
    state_file.write_text(
        json.dumps(
            {
                "queue": [
                    {
                        "nzo_id": "nzo-restored-1",
                        "filename": "Some.Release-GRP",
                        "category": "movies",
                        "status": "Downloading",
                        "percentage": 42,
                        "size": "1 GB",
                        "sizeleft": "580 MB",
                        "eta": "2026-10-19T20:00:00",
                        "timeleft": "0:05:00",
                        "added_at": "2026-10-19T19:00:00",
                        "release_id": "11111111-2222-3333-4444-555555555555",
                        "links": ["https://example.invalid/file.bin"],
                        "payload": "cGF5bG9hZA==",
                    }
                ],
                "history": [
                    {
                        "nzo_id": "nzo-history-1",
                        "name": "Old.Release-GRP",
                        "category": "tv",
                        "status": "Completed",
                        "size": "700 MB",
                        "completed_at": "2026-10-19T18:00:00",
                        "storage": "/downloads",
                        "release_id": "66666666-7777-8888-9999-000000000000",
                        "payload": None,
                        "links": ["https://example.invalid/old.bin"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    module = fresh_import()

    assert "nzo-restored-1" in module.download_queue, (
        "The queue was not restored - the state import probably runs "
        "before the dataclass definitions and fails silently"
    )
    restored_queue_item = module.download_queue["nzo-restored-1"]
    assert restored_queue_item.filename == "Some.Release-GRP"
    assert restored_queue_item.percentage == 42
    assert restored_queue_item.release_id == "11111111-2222-3333-4444-555555555555"
    assert restored_queue_item.links == ["https://example.invalid/file.bin"]

    assert "nzo-history-1" in module.download_history, "The history was not restored"
    restored_history_item = module.download_history["nzo-history-1"]
    assert restored_history_item.name == "Old.Release-GRP"
    assert restored_history_item.storage == "/downloads"


def test_fehlende_state_datei_bricht_den_import_nicht(state_file, fresh_import):
    assert not state_file.exists()

    module = fresh_import()

    assert module.download_queue == {}
    assert module.download_history == {}


def test_kaputte_state_datei_wird_gefangen(state_file, fresh_import):
    state_file.write_text("{not valid json", encoding="utf-8")

    module = fresh_import()

    assert module.download_queue == {}
    assert module.download_history == {}


def test_persist_roundtrip_erhaelt_die_felder(state_file, fresh_import):
    module = fresh_import()

    module.download_queue["nzo-1"] = module.QueueItem(
        nzo_id="nzo-1",
        filename="Round.Trip-GRP",
        category="movies",
        release_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        links=["https://example.invalid/a.bin", "https://example.invalid/b.bin"],
    )
    module.download_history["nzo-2"] = module.HistoryItem(
        nzo_id="nzo-2",
        name="Round.Trip.2-GRP",
        category="tv",
        storage="/downloads",
    )
    module._persist_state()

    reloaded = fresh_import()

    assert reloaded.download_queue["nzo-1"].links == [
        "https://example.invalid/a.bin",
        "https://example.invalid/b.bin",
    ]
    assert reloaded.download_queue["nzo-1"].release_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert reloaded.download_history["nzo-2"].storage == "/downloads"
