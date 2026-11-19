"""The queue persistence writes a file - and it writes the right fields.

The field names are the names of the dataclass: `size_bytes`,
`sizeleft_bytes` and `added_at` - not `size` and `created_at`.

Both item forms are covered here: the dataclass of the queue **and** a
plain dict.

The history of the bug this replaces is in `app/AGENTS.md` (#1048).
"""

import json

from app.persistence import QueuePersistence
from app.sabnzbd_api import QueueItem


def test_save_legt_die_datei_an(tmp_path):
    """A dataclass item must not break the save."""
    ziel = tmp_path / "queue.json"
    persistence = QueuePersistence(ziel)
    item = QueueItem(nzo_id="nzo-1", filename="Some.Release-GRP", category="movies")

    assert persistence.save({"nzo-1": item}) is True
    assert ziel.exists(), "The queue file was not written"


def test_save_uebernimmt_die_felder_der_dataclass(tmp_path):
    """The written fields are the ones the dataclass knows."""
    ziel = tmp_path / "queue.json"
    persistence = QueuePersistence(ziel)
    item = QueueItem(
        nzo_id="nzo-1",
        filename="Some.Release-GRP",
        category="movies",
        status="Queued",
        size_bytes=1234567,
        sizeleft_bytes=765432,
        release_id="11111111-2222-3333-4444-555555555555",
        links=["https://example.invalid/a.bin"],
    )

    persistence.save({"nzo-1": item})

    jobs = json.loads(ziel.read_text(encoding="utf-8"))["jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == "nzo-1"
    assert job["status"] == "Queued"
    assert job["filename"] == "Some.Release-GRP"
    assert job["category"] == "movies"
    assert job["size_bytes"] == 1234567
    assert job["sizeleft_bytes"] == 765432
    assert job["release_id"] == "11111111-2222-3333-4444-555555555555"
    assert job["links"] == ["https://example.invalid/a.bin"]
    assert job["added_at"], "The queue item has no timestamp"


def test_save_akzeptiert_auch_dicts(tmp_path):
    """A dict item is the second form - it must not break either."""
    ziel = tmp_path / "queue.json"
    persistence = QueuePersistence(ziel)

    assert (
        persistence.save(
            {
                "nzo-2": {
                    "status": "Queued",
                    "filename": "Dict.Release-GRP",
                    "category": "tv",
                    "size_bytes": 42,
                }
            }
        )
        is True
    )

    jobs = json.loads(ziel.read_text(encoding="utf-8"))["jobs"]
    assert jobs[0]["id"] == "nzo-2"
    assert jobs[0]["filename"] == "Dict.Release-GRP"
    assert jobs[0]["size_bytes"] == 42


def test_load_liefert_die_gespeicherten_jobs_zurueck(tmp_path):
    """The round trip: what was saved comes back."""
    ziel = tmp_path / "queue.json"
    persistence = QueuePersistence(ziel)
    item = QueueItem(nzo_id="nzo-3", filename="Round.Trip-GRP", category="movies")

    persistence.save({"nzo-3": item})

    jobs = persistence.load()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "nzo-3"
    assert jobs[0]["filename"] == "Round.Trip-GRP"


def test_save_schreibt_in_die_uebergebene_datei(tmp_path):
    """The file lands where it was told to - not next to the process."""
    ziel = tmp_path / "unterordner" / "queue.json"
    persistence = QueuePersistence(ziel)
    item = QueueItem(nzo_id="nzo-4", filename="Some.Release-GRP", category="movies")

    assert persistence.save({"nzo-4": item}) is True
    assert ziel.exists()


def test_der_restore_bekommt_die_felder_zurueck():
    """The restore reads the fields the persistence writes.

    A restored entry without the release ID is a hollow shell: the
    download handler needs it to resolve the links, and the *arr apps read
    the size from the queue.
    """
    from app.sabnzbd_api import queue_item_from_dict

    item = queue_item_from_dict(
        {
            "nzo_id": "nzo-5",
            "filename": "Restored.Release-GRP",
            "category": "movies",
            "status": "Queued",
            "release_id": "11111111-2222-3333-4444-555555555555",
            "size_bytes": 1234567,
            "sizeleft_bytes": 765432,
            "links": ["https://example.invalid/a.bin"],
        }
    )

    assert item.release_id == "11111111-2222-3333-4444-555555555555"
    assert item.size_bytes == 1234567
    assert item.sizeleft_bytes == 765432
    assert item.links == ["https://example.invalid/a.bin"]


def test_die_queue_datei_wird_bei_jeder_aenderung_geschrieben(
    state_file, fresh_import, queue_file_in_tmp
):
    """The queue file carries the same stand as the state file.

    A queue file that is only written at the shutdown brings back jobs
    that were completed or deleted in the meantime - once the process dies
    without a clean shutdown.
    """
    import app.persistence as persistence

    modul = fresh_import()

    modul.download_queue["nzo-6"] = modul.QueueItem(
        nzo_id="nzo-6", filename="Live.Release-GRP", category="movies"
    )
    modul._persist_state()

    assert queue_file_in_tmp.exists(), "The queue file was not written on the change"
    jobs = persistence.queue_persistence.load()
    assert [job["id"] for job in jobs] == ["nzo-6"]

    # The entry leaves the queue - the file has to follow.
    del modul.download_queue["nzo-6"]
    modul._persist_state()

    assert persistence.queue_persistence.load() == [], (
        "The queue file still carries the entry - a restart would bring back "
        "a job that is no longer queued (#1048)."
    )
