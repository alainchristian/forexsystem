"""Shared PostgreSQL connection pool — import get_conn/put_conn everywhere."""

import logging
import psycopg2
import psycopg2.pool

from config.config import POSTGRESQL

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(minconn=2, maxconn=10, **POSTGRESQL)
        logger.info("PostgreSQL connection pool created (min=2, max=10)")
    return _pool


def get_conn() -> psycopg2.extensions.connection:
    return _get_pool().getconn()


def put_conn(conn: psycopg2.extensions.connection) -> None:
    _get_pool().putconn(conn)


def close_all() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
