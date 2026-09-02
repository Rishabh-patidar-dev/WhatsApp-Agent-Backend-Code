"""Shared database connection pool.

The old code opened a fresh TCP+TLS connection for every single query — five or
six per incoming message. This opens a small pool once and hands connections
back out, which removes roughly a second of latency per reply.

`prepare_threshold=None` is required: Supabase's transaction-mode pooler
multiplexes connections, so server-side prepared statements break.
"""
from __future__ import annotations

import atexit
import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Opens the pool on first use so importing this module never touches the network."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=settings.database_url,
                    min_size=1,
                    max_size=settings.db_pool_max,
                    max_idle=300,
                    timeout=15,
                    kwargs={"row_factory": dict_row, "prepare_threshold": None},
                    open=True,
                )
                atexit.register(close_pool)
                log.info("Database pool opened (max_size=%s)", settings.db_pool_max)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Checked-out connection that commits on success and rolls back on error."""
    with get_pool().connection() as conn:
        yield conn


def query(sql: str, params: tuple | list | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | list | None = None) -> dict[str, Any] | None:
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple | list | None = None) -> None:
    with connection() as conn:
        conn.execute(sql, params)
