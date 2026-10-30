"""The bridge must not publish a URL for a host that does not resolve (#879).

The bridge used to carry **two** implementations and therefore **two**
defaults for `DDLNZB_API_URL`:

- `app/config.py` pointed at the live host,
- `src/config.py` — a stub tree that was never started — pointed at
  `api.ddlnzb.com`, a domain without a single DNS record. The stub was
  copied into the image and into the **public** repository `DDLnzb/bridge`,
  where it looked like the official API domain.

The contract pinned here:

1. There is **one** implementation and **one** default.
2. No file that ships with the bridge names a host that does not resolve.
3. The default host resolves.

The scan covers everything that ships (the image, the public mirror, the
README, the `.env.example`). The test directory is **not** part of it: a
test may name a dead host in order to prove that it does not occur in the
code.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BRIDGE_DIR / "app"

#: Hosts that must not occur in the shipped code. `api.ddlnzb.com` has no
#: DNS record at all - no A, no AAAA, and the parent zone does not exist
#: either. `api.petsground.de` is the same case (#871).
DEAD_HOSTS = ("api.ddlnzb.com", "api.petsground.de")

#: The host the bridge talks to by default.
LIVE_HOST = "ddlnzb.pages.dev"

#: The live API URL - the one default that exists.
LIVE_API_URL = f"https://{LIVE_HOST}/api/app"

#: Suffixes of the text files that are scanned. Binary files (the images in
#: `docs/`) are skipped - a PNG must not turn into a false positive.
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini", ".json", ".example"}
)

#: Files without a suffix that are text nevertheless.
TEXT_NAMES = frozenset({"Dockerfile", "Makefile", ".env", ".env.example"})


def _shipped_files() -> list[Path]:
    """Every file that ships with the bridge - without the test directory."""
    dateien: list[Path] = []
    for datei in BRIDGE_DIR.rglob("*"):
        if not datei.is_file():
            continue
        relativ = datei.relative_to(BRIDGE_DIR)
        if "tests" in relativ.parts or "__pycache__" in relativ.parts:
            continue
        if datei.suffix in TEXT_SUFFIXES or datei.name in TEXT_NAMES:
            dateien.append(datei)
    return sorted(dateien)


def _read(datei: Path) -> str:
    return datei.read_text(encoding="utf-8", errors="ignore")


def test_es_gibt_genau_eine_implentierung():
    """The stub tree is gone - one implementation, one default (#879)."""
    assert not (BRIDGE_DIR / "src").exists(), (
        "bridge/src/ is back - a second implementation means a second default "
        "for DDLNZB_API_URL, and the last one pointed at a dead domain (#879)"
    )


def test_kein_host_ohne_dns_im_ausgelieferten_code():
    """No shipped file names a host that does not resolve."""
    treffer = [
        f"{datei.relative_to(BRIDGE_DIR)}: {host}"
        for datei in _shipped_files()
        for host in DEAD_HOSTS
        if host in _read(datei)
    ]

    assert treffer == [], "These files name a host without a DNS record: " + ", ".join(treffer)


def test_der_api_default_steht_in_genau_einer_python_quelle():
    """The default is defined once - in `app/config.py`."""
    treffer = [
        str(datei.relative_to(BRIDGE_DIR))
        for datei in sorted(APP_DIR.rglob("*.py"))
        if LIVE_API_URL in _read(datei)
    ]

    assert treffer == ["app/config.py"], (
        "The default API URL stands in more than one place: " + ", ".join(treffer)
    )


def test_der_default_zeigt_auf_den_live_host():
    """`ENV_SOURCES` carries the live URL as the default for the API."""
    from app.config import ENV_SOURCES

    assert ENV_SOURCES["DDLNZB_API_URL"][2] == LIVE_API_URL


def test_der_live_host_loest_auf():
    """The default host resolves - without a network the test skips."""
    try:
        socket.getaddrinfo(LIVE_HOST, 443)
    except OSError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no DNS available in this environment: {exc}")
