#!/usr/bin/env python3
"""
Interactive CLI to create (or promote) an admin user. No email/password is
ever hardcoded anywhere in this file or the codebase — you type them in
when you run this, and the password is hashed with the exact same
passlib/bcrypt setup auth.py already uses for normal signups (nothing new
introduced here).

Run this AFTER your app + database are deployed and reachable, with the
same environment (DATABASE_URL etc.) active — e.g. on Render, via the
Shell tab for your web service:

    python create_admin.py

Behavior:
  - If the email already exists -> sets is_admin = True on that existing
    user (password is left untouched, unless you explicitly choose to
    reset it when prompted).
  - If the email doesn't exist -> creates a new user with is_admin = True.

The password is entered with getpass (hidden input, never echoed or
logged) and is validated with a minimum length before hashing.
"""
import getpass
import re
import sys

from app.database import SessionLocal
from app import models
from app.auth import hash_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def prompt_email() -> str:
    while True:
        email = input("Admin email: ").strip().lower()
        if EMAIL_RE.match(email):
            return email
        print("That doesn't look like a valid email — try again.")


def prompt_password() -> str:
    while True:
        pw1 = getpass.getpass("Admin password (min 8 chars, hidden): ")
        if len(pw1) < 8:
            print("Password must be at least 8 characters.")
            continue
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2:
            print("Passwords didn't match — try again.")
            continue
        return pw1


def main():
    db = SessionLocal()
    try:
        email = prompt_email()
        existing = db.query(models.User).filter_by(email=email).first()

        if existing:
            print(f"\nUser already exists: {existing.full_name} <{existing.email}> "
                  f"(is_admin={existing.is_admin})")
            if existing.is_admin:
                print("Already an admin — nothing to do.")
                return

            confirm = input("Promote this existing user to admin? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return

            reset = input("Also reset their password now? [y/N] ").strip().lower()
            if reset == "y":
                existing.password_hash = hash_password(prompt_password())

            existing.is_admin = True
            db.commit()
            print(f"✅ {existing.email} is now an admin.")
            return

        print("\nNo user with that email yet — creating a new admin account.")
        full_name = input("Full name: ").strip() or "Admin"
        password = prompt_password()

        user = models.User(
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"✅ Created admin user: {user.email}")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
