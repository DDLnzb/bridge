"""DDLnzb Bridge - API key comparison

The bridge compares the presented API key with the configured one in
constant time. The comparison lives in **one** place - both call sites
(`app/sabnzbd_api.py` for the SABnzbd dispatch and `app/main.py` for the
web UI dependency) import it from here. Two copies of the comparison are
the trap from #405: they drift apart, and the one that drifts compares the
key in a way that can be timed or that crashes.

The comparison runs over **bytes**, not over `str`:
`secrets.compare_digest` raises a `TypeError` when it compares two `str`
arguments and one of them contains a non-ASCII character. The key is
delivered as an unauthenticated query parameter, so a client sending
`?apikey=<non-ascii>` got an HTTP 500 instead of a 401 - the crash happened
before any decision could be made (#1050).

The backend solves the same problem the same way: see
`backend/app/metrics_token.py`.
"""

import secrets


def key_matches(presented: str | None, configured: str) -> bool:
    """Compare the presented key with the configured one in constant time.

    An empty configuration closes everything: without a configured key no
    call is valid, otherwise a bridge without a key could be switched off
    through the back door. A missing presented key is a mismatch, never a
    match.

    The comparison runs over UTF-8 bytes - `compare_digest` refuses `str`
    arguments with non-ASCII characters (#1050).
    """
    if not presented or not configured:
        return False

    return secrets.compare_digest(presented.encode("utf-8"), configured.encode("utf-8"))
