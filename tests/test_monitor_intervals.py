"""Sleep interval in the JDownloader monitoring (#477).

`_monitor_jdownloader()` chooses the interval between two polls via `matched`:
5 s while a download is running, 300 s otherwise. Before the fix the `except`
branch **always** slept 300 s — a single unresponsive JDownloader therefore
froze a running package for five minutes and burned the rest of the 30-minute
deadline.

Two points from `../AGENTS.md` are contract here:

- **No `fresh_import()`.** The handler accesses `download_queue` through a
  module reference. A newly imported module would have a different dict, the
  job would be missing, the loop would return immediately and the test would
  silently stay green. Therefore `app.sabnzbd_api.download_queue` is filled
  directly.
- **Behaviour, not source text.** What is observed is the chosen sleep
  interval, not some line in the code.

The 30-minute deadline is replaced by a decoy for the loop clock, otherwise
the real `sleep(300)` would make the test useless. The flow stays real:
`_monitor_jdownloader()` runs completely, only time is staged.
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import app.download_handler as download_handler_modul
import app.sabnzbd_api as sabnzbd_api
from app.download_handler import DownloadHandler

ACTIVE = 5
IDLE = 300

NZO_ID = "nzo-test-477"
PAKET_NAME = "My.Release.2024.GRP"


def _paket(finished=False, name=PAKET_NAME):
    return {
        "name": name,
        "bytesLoaded": 500,
        "bytesTotal": 1000,
        "finished": finished,
        "saveTo": "/downloads",
    }


class _JDownloaderStub:
    """Delivers the package list iteration after iteration.

    An `Exception` entry is raised, not returned — that way the error branch
    of the monitoring can be triggered on purpose.
    """

    def __init__(self, abfolge):
        self._abfolge = list(abfolge)
        self.aufrufe = 0

    async def get_packages(self):
        self.aufrufe += 1
        if not self._abfolge:
            return []
        ergebnis = self._abfolge.pop(0)
        if isinstance(ergebnis, Exception):
            raise ergebnis
        return ergebnis


class _Uhr:
    """Replaces the time source so that exactly `durchlaeufe` iterations run."""

    def __init__(self, durchlaeufe: int):
        self._durchlaeufe = durchlaeufe
        self._erster_aufruf = True
        self._geprueft = 0

    def time(self) -> float:
        if self._erster_aufruf:
            # Represents the deadline: 0 + 1800 s
            self._erster_aufruf = False
            return 0.0
        if self._geprueft >= self._durchlaeufe:
            return 1801.0  # Deadline exceeded → the loop ends
        self._geprueft += 1
        return 0.0


@contextmanager
def _gestellte_zeit(durchlaeufe):
    """Stages the time only for the handler, not for the event loop.

    The loop calls `self.time()` as well (`_run_once`) — and that *before* the
    handler callback. A decoy on `loop.time` would therefore shift by one
    iteration and the monitor would never enter the loop. Instead the access
    is replaced that only `_monitor_jdownloader` has.
    """
    uhr = _Uhr(durchlaeufe)

    class _GestellteSchleife:
        def time(self):
            return uhr.time()

    vorher = asyncio.get_event_loop
    asyncio.get_event_loop = _GestellteSchleife
    try:
        yield
    finally:
        asyncio.get_event_loop = vorher


def _kette_starten(loop, coroutine):
    """Runs `coroutine` and cleans the loop up again.

    The tests are deliberately synchronous: `_monitor_jdownloader()` needs no
    real sleep, because `_gestellte_zeit` and the `sleep` decoy replace the
    waiting. That way it is guaranteed that no foreign loop access stumbles
    over the time decoy during the run.
    """
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coroutine)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _monitoren(monkeypatch, abfolge, durchlaeufe=1):
    """Runs the real `_monitor_jdownloader` and returns the sleep times.

    `abfolge` describes what `get_packages()` returns per iteration — a list
    of package dicts, or an `Exception` object to trigger the error branch.
    """
    schlafzeiten = []

    async def schlafe(sekunden):
        # No real waiting: the time is staged, the loop runs through
        # synchronously and is ended by `_Uhr`.
        schlafzeiten.append(sekunden)

    jdownloader = _JDownloaderStub(abfolge)
    loop = asyncio.new_event_loop()

    monkeypatch.setattr(download_handler_modul, "jdownloader", jdownloader)
    # The three queue functions write to the real state; here only the sleep
    # interval matters.
    monkeypatch.setattr(download_handler_modul, "update_progress", lambda *a, **kw: None)
    monkeypatch.setattr(download_handler_modul, "complete_download", lambda *a, **kw: None)
    monkeypatch.setattr(download_handler_modul, "fail_download", lambda *a, **kw: None)
    monkeypatch.setattr(asyncio, "sleep", schlafe)

    # `download_queue` is the real reference from `app.sabnzbd_api` — that is
    # exactly why nothing may be re-imported here.
    sabnzbd_api.download_queue[NZO_ID] = SimpleNamespace(
        nzo_id=NZO_ID, filename=PAKET_NAME, status="Downloading"
    )
    try:
        with _gestellte_zeit(durchlaeufe):
            _kette_starten(loop, DownloadHandler()._monitor_jdownloader(NZO_ID, PAKET_NAME))
    finally:
        sabnzbd_api.download_queue.pop(NZO_ID, None)

    return schlafzeiten, jdownloader


class TestAbstandOhneFehler:
    """The normal case: JDownloader responds."""

    def test_ohne_laufenden_download_der_lange_abstand(self, monkeypatch):
        schlafzeiten, _ = _monitoren(monkeypatch, [[]])

        assert schlafzeiten == [IDLE], (
            "Without a visible package 300 s is correct - if the test deviates "
            "here, the decoy is broken and the test no longer says anything"
        )

    def test_bei_laufendem_download_der_kurze_abstand(self, monkeypatch):
        schlafzeiten, _ = _monitoren(monkeypatch, [[_paket()]])

        assert schlafzeiten == [ACTIVE]

    def test_ist_das_paket_verschwunden_waehrt_der_abstand_zurueck(self, monkeypatch):
        """JDownloader responds, but the package is no longer in the list.

        `matched` must then be `False` again — otherwise the monitor polls a
        dead package every 5 s until the deadline expires.
        """
        schlafzeiten, jdownloader = _monitoren(monkeypatch, [[_paket()], []], durchlaeufe=2)

        assert jdownloader.aufrufe == 2, "Both iterations must have happened"
        assert schlafzeiten == [ACTIVE, IDLE]


class TestAbstandBeiFehler:
    """The actual finding from #477."""

    def test_fehler_bei_laufendem_download_schlaeft_kurz(self, monkeypatch):
        """The first iteration sees the package, the second one raises.

        The error branch must choose the short interval: the download was
        demonstrably running, a single poll error says nothing about its state.
        """
        schlafzeiten, jdownloader = _monitoren(
            monkeypatch, [[_paket()], RuntimeError("JDownloader not reachable")], durchlaeufe=2
        )

        assert jdownloader.aufrufe == 2, "Both iterations must have happened"
        assert schlafzeiten == [ACTIVE, ACTIVE], (
            f"Slept for {schlafzeiten}, expected [5, 5]. "
            "The error branch freezes the running download (#477)"
        )

    def test_fehler_ohne_bisherigen_download_schlaeft_lang(self, monkeypatch):
        """The opposite direction — without ever having seen a package, nothing
        is known about a running download, so the long interval stays."""
        schlafzeiten, _ = _monitoren(monkeypatch, [RuntimeError("JDownloader not reachable")])

        assert schlafzeiten == [IDLE]

    def test_dauernde_fehler_bei_laufendem_download_bleiben_kurz(self, monkeypatch):
        """Even after several errors the interval must not flip."""
        fehler = [RuntimeError("nope")] * 3
        schlafzeiten, _ = _monitoren(monkeypatch, [[_paket()], *fehler], durchlaeufe=4)

        assert schlafzeiten == [ACTIVE] * 4


class TestAbbruch:
    """The monitor also ends when the job disappears from the queue."""

    def test_ohne_auftrag_in_der_queue_kein_warteschlaf(self, monkeypatch):
        jdownloader = _JDownloaderStub([[_paket()]])
        loop = asyncio.new_event_loop()

        async def schlafe(_sekunden):
            raise AssertionError("Without a job no sleep may take place")

        monkeypatch.setattr(download_handler_modul, "jdownloader", jdownloader)
        monkeypatch.setattr(download_handler_modul, "update_progress", lambda *a, **kw: None)
        monkeypatch.setattr(asyncio, "sleep", schlafe)

        # `NZO_ID` is deliberately not in the queue.
        _kette_starten(loop, DownloadHandler()._monitor_jdownloader(NZO_ID, PAKET_NAME))

        assert jdownloader.aufrufe == 0, "The monitor queried anyway"
