"""TLS handling for the remaining psycopg2 connections.

``psycopg2-binary`` bundles its own libpq and OpenSSL. Negotiating TLS with it
on a worker thread while another thread is JIT-compiling with numba/LLVM (the
datashader warm-up) segfaults the whole API worker; one request to
``/api/v1/enrichment/bulletin`` was enough to take the server down. The map
layer source avoids this by using asyncpg (see ``map_layers``). Connections to
the loopback interface gain nothing from TLS, so skip the negotiation there and
leave every remote connection (Cloud SQL and similar) untouched.
"""

from __future__ import annotations

from typing import Optional

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_loopback_host(host: Optional[str]) -> bool:
    return bool(host) and (host in _LOOPBACK_HOSTS or str(host).startswith("/"))


def plain_loopback_dsn(dsn: str) -> str:
    """Return ``dsn`` with ``sslmode=disable`` when it targets loopback.

    An explicit ``sslmode`` is always respected, as are non-loopback hosts and
    DSNs that psycopg2 cannot parse (they are returned unchanged).
    """
    try:
        from psycopg2.extensions import make_dsn, parse_dsn

        parts = parse_dsn(dsn)
    except Exception:
        return dsn
    if "sslmode" in parts or not is_loopback_host(parts.get("host")):
        return dsn
    return make_dsn(dsn, sslmode="disable")
