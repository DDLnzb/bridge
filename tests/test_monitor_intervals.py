"""Warteschlaf im JDownloader-Monitoring (#477).

`_monitor_jdownloader()` wählt den Abstand zwischen zwei Polls über `matched`:
5 s bei laufendem Download, 300 s sonst. Bis zum Fix schlief der `except`-Zweig
**immer** 300 s — ein einzelner ausgesetzter JDownloader frozeerte damit ein
laufendes Paket für fünf Minuten und verbrauchte den Rest der 30-Minuten-Frist.

Zwei Punkte aus `../AGENTS.md` sind hier Vertrag:

- **Kein `fresh_import()`.** Der Handler greift über eine Modul-Referenz auf
  `download_queue` zu. Ein neu importiertes Modul hätte ein anderes Dict, der
  Auftrag fehlte, die Schleife kehrte sofort zurück und der Test wäre
  stillschweigend grün. Deshalb wird `app.sabnzbd_api.download_queue` direkt
  befüllt.
- **Verhalten, nicht Quelltext.** Beobachtet wird der gewählte Warteschlaf, nicht
  irgendeine Zeile im Code.

Die 30-Minuten-Frist wird über eine Attrappe für die Loop-Uhr ersetzt, sonst
würde der echte `sleep(300)` den Test unbrauchbar machen. Der Ablauf bleibt
echt: `_monitor_jdownloader()` läuft vollständig, nur die Zeit ist gestellt.
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
PAKET_NAME = "Mein.Release.2024.GRP"


def _paket(finished=False, name=PAKET_NAME):
    return {
        "name": name,
        "bytesLoaded": 500,
        "bytesTotal": 1000,
        "finished": finished,
        "saveTo": "/downloads",
    }


class _JDownloaderStub:
    """Liefert die Paketliste Durchlauf für Durchlauf aus.

    Ein `Exception`-Eintrag wird geworfen, nicht zurückgegeben — so lässt sich
    der Fehlerzweig des Monitorings gezielt auslösen.
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
    """Ersetzt die Zeitquelle so, dass genau `durchlaeufe` Iterationen laufen."""

    def __init__(self, durchlaeufe: int):
        self._durchlaeufe = durchlaeufe
        self._erster_aufruf = True
        self._geprueft = 0

    def time(self) -> float:
        if self._erster_aufruf:
            # Bildet die Frist: 0 + 1800 s
            self._erster_aufruf = False
            return 0.0
        if self._geprueft >= self._durchlaeufe:
            return 1801.0  # Frist überschritten → die Schleife endet
        self._geprueft += 1
        return 0.0


@contextmanager
def _gestellte_zeit(durchlaeufe):
    """Stellt die Zeit nur für den Handler, nicht für den Event-Loop.

    Der Loop ruft `self.time()` ebenfalls auf (`_run_once`) — und zwar *vor* dem
    Handler-Callback. Eine Attrappe auf `loop.time` würde deshalb um einen
    Durchlauf verrutschen und der Monitor liefie nie in die Schleife. Stattdessen
    wird der Zugriff ersetzt, den ausschließlich `_monitor_jdownloader` hat.
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
    """Führt `coroutine` aus und räumt den Loop wieder ab.

    Die Tests sind bewusst synchron: `_monitor_jdownloader()` braucht keinen echten
    Warteschlaf, weil `_gestellte_zeit` und der `sleep`-Attrappe das Warten ersetzen.
    So ist sichergestellt, dass während des Durchlaufs kein fremder Loop-Zugriff
    auf die Zeitattrappe stolpert.
    """
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coroutine)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _monitoren(monkeypatch, abfolge, durchlaeufe=1):
    """Führt den echten `_monitor_jdownloader` aus und gibt die Wartezeiten zurück.

    `abfolge` beschreibt, was `get_packages()` je Durchlauf liefert — eine
    Liste von Paket-Dicts, oder ein `Exception`-Objekt zum Auslösen des
    Fehlerzweigs.
    """
    schlafzeiten = []

    async def schlafe(sekunden):
        # Kein echtes Warten: die Zeit ist gestellt, die Schleife läuft
        # synchron durch und wird von `_Uhr` beendet.
        schlafzeiten.append(sekunden)

    jdownloader = _JDownloaderStub(abfolge)
    loop = asyncio.new_event_loop()

    monkeypatch.setattr(download_handler_modul, "jdownloader", jdownloader)
    # Die drei Queue-Funktionen schreiben in den echten Zustand; hier geht es
    # nur um den Warteschlaf.
    monkeypatch.setattr(download_handler_modul, "update_progress", lambda *a, **kw: None)
    monkeypatch.setattr(download_handler_modul, "complete_download", lambda *a, **kw: None)
    monkeypatch.setattr(download_handler_modul, "fail_download", lambda *a, **kw: None)
    monkeypatch.setattr(asyncio, "sleep", schlafe)

    # `download_queue` ist die echte Referenz aus `app.sabnzbd_api` — genau
    # deshalb darf hier nicht neu importiert werden.
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
    """Der Normalfall: JDownloader antwortet."""

    def test_ohne_laufenden_download_der_lange_abstand(self, monkeypatch):
        schlafzeiten, _ = _monitoren(monkeypatch, [[]])

        assert schlafzeiten == [IDLE], (
            "Ohne sichtbares Paket sind 300 s korrekt — weicht der Test hier ab, "
            "ist die Attrappe kaputt und der Test sagt nichts mehr aus"
        )

    def test_bei_laufendem_download_der_kurze_abstand(self, monkeypatch):
        schlafzeiten, _ = _monitoren(monkeypatch, [[_paket()]])

        assert schlafzeiten == [ACTIVE]

    def test_ist_das_paket_verschwunden_waehrt_der_abstand_zurueck(self, monkeypatch):
        """JDownloader antwortet, das Paket ist aber nicht mehr dabei.

        `matched` muss dann wieder auf `False` stehen — sonst pollt der
        Monitor bis zum Ablauf der Frist alle 5 s ein totes Paket.
        """
        schlafzeiten, jdownloader = _monitoren(monkeypatch, [[_paket()], []], durchlaeufe=2)

        assert jdownloader.aufrufe == 2, "Beide Durchläufe müssen stattgefunden haben"
        assert schlafzeiten == [ACTIVE, IDLE]


class TestAbstandBeiFehler:
    """Der eigentliche Befund aus #477."""

    def test_fehler_bei_laufendem_download_schlaeft_kurz(self, monkeypatch):
        """Erster Durchlauf sieht das Paket, der zweite wirft.

        Der Fehlerzweig muss den kurzen Abstand wählen: der Download lief
        nachweislich, ein einzelner Poll-Fehler sagt nichts über seinen Zustand.
        """
        schlafzeiten, jdownloader = _monitoren(
            monkeypatch, [[_paket()], RuntimeError("JDownloader nicht erreichbar")], durchlaeufe=2
        )

        assert jdownloader.aufrufe == 2, "Beide Durchläufe müssen stattgefunden haben"
        assert schlafzeiten == [ACTIVE, ACTIVE], (
            f"Geschlafen wurde {schlafzeiten}, erwartet [5, 5]. "
            "Der Fehlerzweig friert den laufenden Download ein (#477)"
        )

    def test_fehler_ohne_bisherigen_download_schlaeft_lang(self, monkeypatch):
        """Gegenrichtung — ohne je ein Paket gesehen zu haben, ist nichts über
        einen laufenden Download bekannt, also bleibt der lange Abstand."""
        schlafzeiten, _ = _monitoren(monkeypatch, [RuntimeError("JDownloader nicht erreichbar")])

        assert schlafzeiten == [IDLE]

    def test_dauernde_fehler_bei_laufendem_download_bleiben_kurz(self, monkeypatch):
        """Auch nach mehreren Fehlern darf der Abstand nicht kippen."""
        fehler = [RuntimeError("nope")] * 3
        schlafzeiten, _ = _monitoren(monkeypatch, [[_paket()], *fehler], durchlaeufe=4)

        assert schlafzeiten == [ACTIVE] * 4


class TestAbbruch:
    """Der Monitor endet auch, wenn der Auftrag aus der Queue verschwindet."""

    def test_ohne_auftrag_in_der_queue_kein_warteschlaf(self, monkeypatch):
        jdownloader = _JDownloaderStub([[_paket()]])
        loop = asyncio.new_event_loop()

        async def schlafe(_sekunden):
            raise AssertionError("Ohne Auftrag darf kein Warteschlaf stattfinden")

        monkeypatch.setattr(download_handler_modul, "jdownloader", jdownloader)
        monkeypatch.setattr(download_handler_modul, "update_progress", lambda *a, **kw: None)
        monkeypatch.setattr(asyncio, "sleep", schlafe)

        # `NZO_ID` ist absichtlich nicht in der Queue.
        _kette_starten(loop, DownloadHandler()._monitor_jdownloader(NZO_ID, PAKET_NAME))

        assert jdownloader.aufrufe == 0, "Der Monitor hat trotzdem abgefragt"
