#!/usr/bin/env python3
"""
One-time data migration: copies every row from an existing local SQLite
database into the PostgreSQL database currently configured via
DATABASE_URL.

This script does NOT define or change any model, schema, or business logic.
It reuses the exact same Base.metadata already built from app/models.py,
and copies table-by-table in FK-safe dependency order
(Base.metadata.sorted_tables — automatically correct even if models.py
changes later).

Usage (run from the backend/ folder, with your venv active):

    # 1) Set DATABASE_URL in your environment / .env to the PostgreSQL
    #    connection string you want to migrate data INTO. This is the
    #    destination.
    # 2) Run, pointing --sqlite at your existing sqlite file (the source):

    python migrate_sqlite_to_postgres.py --sqlite ./luckyspin.db

Safe to re-run: a row that already exists in the destination (same primary
key) is skipped, not duplicated or overwritten — so if a run fails partway
through (e.g. connection drop), you can just run it again.

NOT executed or tested against a real PostgreSQL instance in this session —
there is no live database available here. Review the printed per-table
counts carefully on your first real run, ideally against a throwaway
Postgres database before pointing it at production.
"""
import argparse
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

from app import models  # noqa: F401 — importing this populates Base.metadata with every table
from app.database import Base, engine as dest_engine


def migrate(sqlite_path: str):
    sqlite_url = sqlite_path if sqlite_path.startswith("sqlite") else f"sqlite:///{sqlite_path}"
    src_engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

    if dest_engine.url.get_backend_name() == "sqlite":
        print("❌ DATABASE_URL is currently pointing at SQLite too.")
        print("   Set DATABASE_URL to your PostgreSQL connection string first —")
        print("   that's the destination this script copies INTO.")
        sys.exit(1)

    print(f"Source (SQLite):      {sqlite_url}")
    print(f"Destination (target): {dest_engine.url.render_as_string(hide_password=True)}")
    confirm = input("Copy all data from source into destination? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted — nothing was copied.")
        return

    # Make sure destination tables exist yet (same models/schema — a no-op
    # if main.py already created them on startup).
    Base.metadata.create_all(bind=dest_engine)

    with src_engine.connect() as src_conn, dest_engine.connect() as dest_conn:
        for table in Base.metadata.sorted_tables:
            try:
                rows = src_conn.execute(select(table)).mappings().all()
            except Exception as e:
                # Table doesn't exist in the source sqlite file yet (e.g. a
                # newer table added after that sqlite snapshot) — nothing to copy.
                print(f"  {table.name}: not found in source, skipping ({e})")
                continue

            if not rows:
                print(f"  {table.name}: 0 rows, skipping")
                continue

            copied, skipped = 0, 0
            for row in rows:
                try:
                    dest_conn.execute(table.insert().values(**dict(row)))
                    dest_conn.commit()
                    copied += 1
                except IntegrityError:
                    dest_conn.rollback()
                    skipped += 1  # already exists (same primary key) — fine on a re-run
            print(f"  {table.name}: {copied} copied, {skipped} already existed")

    print("Done. Spot-check row counts in both databases before decommissioning the SQLite file.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", default="./luckyspin.db", help="Path to the existing SQLite file (default: ./luckyspin.db)")
    args = parser.parse_args()
    migrate(args.sqlite)
