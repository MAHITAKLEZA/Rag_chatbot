import psycopg
from pgvector.psycopg import register_vector

from .config import DB_DSN


def connect() -> psycopg.Connection:
    conn = psycopg.connect(DB_DSN)
    register_vector(conn)
    return conn
