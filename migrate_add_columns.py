#!/usr/bin/env python3
"""
One-time schema migration: adds columns that were added to existing models
AFTER your database was first created.

Base.metadata.create_all() (called on every app startup) only creates
TABLES that don't exist yet — it does NOT add new COLUMNS to a table that
already exists. So a brand new/empty database is fine without this script,
but an already-deployed database needs these columns added manually before
the new features will work (the app will error on missing columns
otherwise).

New tables (referral_rewards, admin_settings, nigerian_withdrawals, etc.)
do NOT need this script — create_all() already creates those correctly on
next startup, since they're entirely new tables.

Columns this script adds (skips any that already exist — safe to re-run):
  users.ngn_winnings_balance   FLOAT DEFAULT 0.0
  users.usd_winnings_balance   FLOAT DEFAULT 0.0
  users.referral_code          VARCHAR, UNIQUE, NULL
  users.referred_by_user_id    VARCHAR, NULL

Usage (with DATABASE_URL already set to your target DB in the environment):

    python migrate_add_columns.py

NOT executed or tested against a real database in this session — there is
no live database available here. Review carefully, ideally against a
throwaway copy of your database first.
"""
import sys

from sqlalchemy import inspect, text

from app.database import engine

# (table, column, DDL type + default, is_sqlite_ddl_different)
COLUMNS_TO_ADD = [
    ("users", "ngn_winnings_balance", "FLOAT DEFAULT 0.0"),
    ("users", "usd_winnings_balance", "FLOAT DEFAULT 0.0"),
    ("users", "referral_code", "VARCHAR"),
    ("users", "referred_by_user_id", "VARCHAR"),
]


def main():
    inspector = inspect(engine)
    is_sqlite = engine.url.get_backend_name() == "sqlite"

    with engine.connect() as conn:
        for table, column, ddl_type in COLUMNS_TO_ADD:
            if table not in inspector.get_table_names():
                print(f"  ⚠️  Table '{table}' doesn't exist yet — skipping "
                      f"'{column}' (it'll be created correctly with the "
                      f"column already on it next time the app starts).")
                continue

            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if column in existing_columns:
                print(f"  {table}.{column}: already exists, skipping")
                continue

            ddl = f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'
            print(f"  Adding {table}.{column} ...")
            conn.execute(text(ddl))
            conn.commit()
            print(f"  {table}.{column}: added")

    # referral_code needs a unique index too (SQLAlchemy's model declares
    # unique=True, index=True — this creates it explicitly since ADD COLUMN
    # above doesn't add constraints on its own).
    inspector = inspect(engine)  # refresh after the ALTERs above
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("users")} if "users" in inspector.get_table_names() else set()
    if "ix_users_referral_code" not in existing_indexes:
        with engine.connect() as conn:
            try:
                conn.execute(text("CREATE UNIQUE INDEX ix_users_referral_code ON users (referral_code)"))
                conn.commit()
                print("  Added unique index on users.referral_code")
            except Exception as e:
                print(f"  ⚠️  Could not add unique index on users.referral_code: {e}")
                print("     (if every existing user still has referral_code = NULL, this is expected —")
                print("     NULLs don't count as duplicates for a unique index in Postgres/SQLite, so this")
                print("     should still succeed; codes get backfilled lazily the first time each user")
                print("     opens the Refer a Friend section.)")

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
