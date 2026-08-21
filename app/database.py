from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import DATABASE_URL

# Render (and some other hosts) hand out DATABASE_URL starting with
# "postgres://", but SQLAlchemy 1.4+/2.x's default driver name requires
# "postgresql://" — same database, just the URL scheme SQLAlchemy expects.
# This does nothing for a sqlite:// URL (used for local dev only).
_resolved_url = DATABASE_URL
if _resolved_url.startswith("postgres://"):
    _resolved_url = _resolved_url.replace("postgres://", "postgresql://", 1)

_is_sqlite = _resolved_url.startswith("sqlite")

connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(
    _resolved_url,
    connect_args=connect_args,
    # Only relevant for Postgres: recycle/ping connections so a connection
    # that Render's proxy silently dropped isn't reused and doesn't error
    # out the next request. No effect on sqlite.
    **({} if _is_sqlite else {"pool_pre_ping": True, "pool_recycle": 300}),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
