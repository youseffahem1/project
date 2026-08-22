"""
NEW (additive only): auto-migration for native PostgreSQL ENUM types.

Base.metadata.create_all() creates a Postgres ENUM type (e.g.
`transactiontype`) the FIRST time a table using it is created. If a NEW
value is later added to the Python enum in models.py (e.g.
TransactionType.ADMIN_GRANT), an already-existing production database's
enum type is never updated on its own — create_all() does not touch a
type that already exists. Any INSERT using that new value then fails with:

    psycopg2.errors.InvalidTextRepresentation:
    invalid input value for enum transactiontype: "ADMIN_GRANT"

This module closes that gap automatically, every startup:

- Scans every column in Base.metadata that uses a SQLAlchemy
  Enum(SomePythonEnum) type, and diffs that Python enum's members against
  the labels the live PostgreSQL enum type actually has (via pg_enum).
- For every missing label, runs:
      ALTER TYPE "<pg_enum_name>" ADD VALUE '<value>'
  guarded by an explicit pg_enum existence check instead of "IF NOT
  EXISTS" (that clause on this statement needs Postgres 12+; the manual
  check works on every version Render offers).
- PostgreSQL only — a no-op on SQLite/local dev, matching
  db_migrations.py / startup_migrations.py.
- Never removes a value, never drops/renames a type, never touches a row
  of existing data, and never touches the Ledger, Deposit, Withdrawal,
  Spin, or Auth/JWT code paths — purely additive.
- Safe to run on every restart: already-present values are skipped, so a
  second (or hundredth) run is a no-op.
- ALTER TYPE ... ADD VALUE cannot run inside an ordinary transaction block
  on PostgreSQL < 12 (and is only safely usable-same-transaction on 12+ if
  you don't also read it back before commit). To be correct on every
  version, each ADD VALUE runs on its own AUTOCOMMIT connection, isolated
  from create_all()'s transaction and from every other statement here.
- Because this is generic (keyed off whatever Enum-typed columns actually
  exist in Base.metadata), it also covers any future new enum member
  someone adds later — not just ADMIN_GRANT — with zero further changes
  needed to this file.
"""
import logging

from sqlalchemy import text
from sqlalchemy.types import Enum as SAEnum

logger = logging.getLogger("enum_migrations")


def _pg_enum_existing_labels(conn, pg_type_name: str) -> set:
    rows = conn.execute(
        text(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON t.oid = e.enumtypid "
            "WHERE t.typname = :type_name"
        ),
        {"type_name": pg_type_name},
    )
    return {r[0] for r in rows}


def _add_enum_value(engine, pg_type_name: str, value: str) -> None:
    """Own AUTOCOMMIT connection — required so ALTER TYPE ... ADD VALUE
    never ends up inside an explicit transaction block, which PostgreSQL
    versions older than 12 reject outright with a hard error."""
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        # Re-check right before adding: closes the race where two app
        # instances both see the value missing at the same instant (e.g. a
        # rolling Render deploy starting two containers back to back).
        if value in _pg_enum_existing_labels(conn, pg_type_name):
            return
        ddl = f"ALTER TYPE \"{pg_type_name}\" ADD VALUE '{value}'"
        logger.info("enum_migrations: %s", ddl)
        print(f"🛠️  enum_migrations: {ddl}")
        conn.execute(text(ddl))
    finally:
        conn.close()


def run_enum_value_migrations(engine, base) -> None:
    """Call once at startup, after Base.metadata.create_all(bind=engine)."""
    if engine.dialect.name != "postgresql":
        return  # SQLite/local dev — untouched

    try:
        # pg_type_name -> set of Python enum values this codebase currently
        # expects, collected from every Enum-typed column in Base.metadata.
        expected = {}
        for table in base.metadata.tables.values():
            for column in table.columns:
                col_type = column.type
                if isinstance(col_type, SAEnum) and col_type.enum_class is not None:
                    pg_name = col_type.name
                    if not pg_name:
                        continue
                    values = {member.value for member in col_type.enum_class}
                    expected.setdefault(pg_name, set()).update(values)

        with engine.connect() as inspect_conn:
            for pg_type_name, expected_values in expected.items():
                existing_labels = _pg_enum_existing_labels(inspect_conn, pg_type_name)
                if not existing_labels:
                    # Type isn't in the DB at all yet — a brand-new
                    # enum/table that create_all() just created correctly,
                    # with every current value already on it. Nothing to do.
                    continue
                missing = expected_values - existing_labels
                for value in sorted(missing):
                    _add_enum_value(engine, pg_type_name, value)

    except Exception as exc:
        # Same philosophy as db_migrations.py / startup_migrations.py: a
        # migration hiccup here must never take down the whole app.
        logger.exception("enum_migrations: startup enum migration failed")
        print(f"⚠️  enum_migrations: startup enum migration failed: {exc}")
