"""The history timestamp is the real UTC timestamp.

`datetime.utcnow()` returns a **naive** datetime, and
`datetime.timestamp()` interprets a naive datetime as **local** time. The
difference fell away: with `TZ=Europe/Berlin` a download that had just
finished appeared an hour in the past (#1051).

The timestamps are aware UTC now, and the timezone information survives the
round trip through the state file.

The test runs under two timezones and expects the same value - the
container's timezone is not the user's timezone, but the *arr apps compute
their times from the Unix timestamp.
"""

import json
import os
import time
from datetime import datetime, timezone

import pytest

import app.sabnzbd_api as sabnzbd_api


@pytest.fixture
def in_zeitzone(monkeypatch):
    """Runs the test body in a given timezone and restores the old one.

    `time.tzset()` reads the `TZ` variable - without it the change would
    not take effect in the running process.

    ⚠️ **`monkeypatch` restores `TZ` only *after* this teardown.** A
    `tzset()` in the teardown would therefore re-read the timezone of the
    test and leave the **process** in it - every test that follows would
    run in Berlin time without saying so. The old value is restored
    explicitly here, before `tzset()` runs.
    """
    original = os.environ.get("TZ")

    def _setzen(tz: str):
        monkeypatch.setenv("TZ", tz)
        time.tzset()

    yield _setzen

    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def _history_mit_zeitpunkt(stunde: int) -> sabnzbd_api.HistoryItem:
    return sabnzbd_api.HistoryItem(
        nzo_id="nzo-1",
        name="Some.Release-GRP",
        category="tv",
        completed_at=datetime(2026, 11, 18, stunde, 30, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("tz", ["UTC", "Europe/Berlin"])
def test_completed_ist_der_echte_unix_timestamp(in_zeitzone, tz):
    """The value is the UTC timestamp - in every timezone.

    The item carries a **naive** timestamp - the form an older state file
    holds. The conversion must read it as UTC: with the local timezone of
    the container the value would be off by the offset (an hour in Berlin).
    """
    in_zeitzone(tz)

    item = sabnzbd_api.HistoryItem(
        nzo_id="nzo-1",
        name="Some.Release-GRP",
        category="tv",
        completed_at=datetime(2026, 11, 18, 12, 30, 0),
    )

    slot = sabnzbd_api._history_slot(item)

    erwartet = int(datetime(2026, 11, 18, 12, 30, 0, tzinfo=timezone.utc).timestamp())
    assert slot["completed"] == erwartet


def test_der_zeitstempel_ist_zeitzonenunabhaengig(in_zeitzone):
    """The same moment yields the same value in two timezones."""
    item = _history_mit_zeitpunkt(12)

    in_zeitzone("UTC")
    in_utc = sabnzbd_api._history_slot(item)["completed"]

    in_zeitzone("Europe/Berlin")
    in_berlin = sabnzbd_api._history_slot(item)["completed"]

    assert in_utc == in_berlin, (
        f"The timestamp depends on the timezone of the container: {in_utc} in "
        f"UTC against {in_berlin} in Europe/Berlin. `datetime.utcnow()` is "
        "naive and `timestamp()` reads a naive datetime as local time (#1051)."
    )


def test_der_zeitstempel_ist_bewusst_utc():
    """A new item carries an aware UTC timestamp."""
    item = sabnzbd_api.HistoryItem(nzo_id="nzo-2", name="X-GRP", category="tv")

    assert item.completed_at.tzinfo is not None, (
        "The timestamp is naive - `timestamp()` would read it as local time (#1051)."
    )
    assert item.completed_at.utcoffset() == timezone.utc.utcoffset(None)


def test_der_zeitstempel_eines_neuen_eintrags_ist_zeitzonenunabhaengig(in_zeitzone):
    """The normal case: an item the bridge created itself.

    The timestamp comes from the default of the dataclass - not from a
    value someone set by hand. That is the path the operation takes on
    every finished download, and it is the one that was shifted.
    """
    in_zeitzone("UTC")
    in_utc = sabnzbd_api._history_slot(
        sabnzbd_api.HistoryItem(nzo_id="nzo-utc", name="X-GRP", category="tv")
    )["completed"]

    in_zeitzone("Europe/Berlin")
    in_berlin = sabnzbd_api._history_slot(
        sabnzbd_api.HistoryItem(nzo_id="nzo-berlin", name="X-GRP", category="tv")
    )["completed"]

    assert abs(in_utc - in_berlin) <= 1, (
        f"The timestamp of a new entry depends on the timezone of the "
        f"container: {in_utc} in UTC against {in_berlin} in Europe/Berlin. "
        "`datetime.utcnow()` is naive and `timestamp()` reads a naive "
        "datetime as local time (#1051)."
    )
    assert abs(in_utc - int(datetime.now(timezone.utc).timestamp())) <= 2, (
        "The timestamp is not the current UTC moment - it does not describe "
        "the download that just finished (#1051)."
    )


def test_die_zeitzone_ueberlebt_den_weg_durch_die_state_datei(state_file, fresh_import):
    """The state file keeps the timezone - a naive value is read as UTC."""
    modul = fresh_import()

    modul.download_history["nzo-3"] = modul.HistoryItem(
        nzo_id="nzo-3",
        name="Persisted.Release-GRP",
        category="tv",
        completed_at=datetime(2026, 11, 18, 12, 30, 0, tzinfo=timezone.utc),
    )
    modul._persist_state()

    gespeichert = json.loads(state_file.read_text(encoding="utf-8"))
    assert gespeichert["history"][0]["completed_at"].endswith("+00:00"), (
        "The timestamp was written without the timezone - the information is "
        "lost on the way through the state file (#1051)."
    )

    modul = fresh_import()

    item = modul.download_history["nzo-3"]
    assert item.completed_at.tzinfo is not None
    assert modul._history_slot(item)["completed"] == int(
        datetime(2026, 11, 18, 12, 30, 0, tzinfo=timezone.utc).timestamp()
    )


def test_ein_naiver_wert_aus_einer_alten_state_datei_wird_als_utc_gelesen(state_file, fresh_import):
    """An old state file has a naive timestamp - it was UTC back then."""
    state_file.write_text(
        json.dumps(
            {
                "queue": [],
                "history": [
                    {
                        "nzo_id": "nzo-old",
                        "name": "Old.Release-GRP",
                        "category": "tv",
                        "status": "Completed",
                        "completed_at": "2026-11-18T12:30:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    modul = fresh_import()

    item = modul.download_history["nzo-old"]
    assert item.completed_at.tzinfo is not None, (
        "The naive timestamp was read back as a naive datetime - it would be "
        "read as local time again (#1051)."
    )
    assert modul._history_slot(item)["completed"] == int(
        datetime(2026, 11, 18, 12, 30, 0, tzinfo=timezone.utc).timestamp()
    )
