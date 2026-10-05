"""Engine construction: sync SQLAlchemy 2 on psycopg 3 (spec D7)."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url


def psycopg_url(database_url: str) -> str:
    """Force the psycopg 3 driver on a plain postgresql:// URL."""
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def make_engine(database_url: str) -> Engine:
    """Pooled engine; every connection uses UTC so timestamptz values come back in UTC."""
    return create_engine(
        psycopg_url(database_url),
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
