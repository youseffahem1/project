"""
NEW: safe, idempotent, automatically re-runnable startup schema migration.

Base.metadata.create_all() (called right before this, in main.py) only
creates TABLES that don't exist yet — it does NOT add new COLUMNS to a
table that already exists in the database. So on an already-deployed
database (e.g. your Render PostgreSQL instance), columns added to
models.py's User model after that table was first created never show up
on their own, and every request touching them would fail.

This module closes that gap WITHOUT a separate script, WITHOUT Render
Shell access, and WITHOUT ever touching existing data:

- Runs automatically, every startup, called once from main.py right after
  Base.metadata.create_all(bind=engine).
- For each (table, column) this app currently expects: checks via
  SQLAlchemy's inspector whether the column is already there. If it is,
  does nothing at all for that column — no DDL, no log noise beyond one
  line. If it isn't, adds ONLY that column with ADD COLUMN (PostgreSQL
  additionally gets "IF NOT EXISTS" in the DDL itself as a second,
  belt-and-suspenders guard against two instances starting at the exact
  same time on the same database — e.g. a rolling Render deploy).
- Never drops, renames, or alters an existing column. Never touches a
  single existing row. Never touches any table this app doesn't already
  define.
- If it can't run for some reason (e.g. the DB user lacks ALTER
  privileges), it logs a clear warning and lets the app keep starting —
  a migration hiccup here must never take down features that don't
  depend on the new columns.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# (table, column, sqlite DDL type, postgres DDL type)
# Add a new tuple here whenever a NEW column is added to an EXISTING table
# in models.py — this is the one place that needs updating for that case.
# A brand-new TABLE never needs an entry here; create_all() already
# handles those correctly, columns and all.
_COLUMNS_TO_ENSURE = [
    ("users", "ngn_winnings_balance", "FLOAT DEFAULT 0.0", "DOUBLE PRECISION DEFAULT 0.0"),
    ("users", "usd_winnings_balance", "FLOAT DEFAULT 0.0", "DOUBLE PRECISION DEFAULT 0.0"),
    ("users", "referral_code", "VARCHAR", "VARCHAR"),
    ("users", "referred_by_user_id", "VARCHAR", "VARCHAR"),
    # NEW (Admin Grant Winnings): both nullable, both only ever populated for
    # type=ADMIN_GRANT rows — every pre-existing Transaction row/type is
    # completely unaffected either way.
    ("transactions", "admin_id", "VARCHAR", "VARCHAR"),
    ("transactions", "reason", "VARCHAR", "VARCHAR"),
]


def run_startup_column_migrations(engine: Engine) -> None:
    is_postgres = engine.url.get_backend_name().startswith("postgres")

    try:
        # Single connection/transaction for the whole thing — on SQLite in
        # particular, using a second engine-level connection concurrently
        # for inspection while another is mid-DDL can self-deadlock on the
        # file lock, so the inspector below is bound to THIS connection,
        # not to a fresh one from the pool.
        with engine.begin() as conn:
            inspector = inspect(conn)
            existing_tables = set(inspector.get_table_names())

            for table, column, sqlite_type, pg_type in _COLUMNS_TO_ENSURE:
                if table not in existing_tables:
                    # Table doesn't exist at all yet — create_all() (called
                    # right before this) already created it correctly, with
                    # this column already on it. Nothing to do here.
                    continue

                existing_columns = {c["name"] for c in inspector.get_columns(table)}
                if column in existing_columns:
                    continue  # already applied on a previous startup — no-op

                col_type = pg_type if is_postgres else sqlite_type
                if is_postgres:
                    ddl = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
                else:
                    # SQLite's ADD COLUMN doesn't support IF NOT EXISTS, but
                    # we already checked existence above, so this is safe.
                    ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"

                conn.execute(text(ddl))
                print(f"[startup-migration] added missing column: {table}.{column}")
                # Keep the inspector's view current for the next iteration
                # in this same loop/transaction.
                inspector = inspect(conn)

            # Unique index backing User.referral_code's unique=True — added
            # separately since ADD COLUMN alone doesn't create constraints.
            if "users" in existing_tables:
                existing_indexes = {ix["name"] for ix in inspector.get_indexes("users")}
                if "ix_users_referral_code" not in existing_indexes:
                    try:
                        conn.execute(text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code "
                            "ON users (referral_code)"
                        ))
                        print("[startup-migration] added missing unique index: ix_users_referral_code")
                    except Exception as e:
                        # Non-fatal — the app still works without this index,
                        # just without DB-level duplicate-code protection
                        # until it's added (e.g. manually) later.
                        print(f"[startup-migration] WARNING: could not add ix_users_referral_code: {e}")

    except Exception as e:
        # A migration failure must not take the whole app down — log loudly
        # and let startup continue. Any endpoint that genuinely needs a
        # still-missing column will fail on its own with a clear DB error,
        # rather than the entire site refusing to boot over one column.
        print(f"[startup-migration] WARNING: startup column migration failed, continuing anyway: {e}")
