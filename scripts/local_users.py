#!/usr/bin/env python
"""Manage local (non-Clerk) Land Registry accounts.

Talks directly to land_registry.local_auth's storage (the same shared
`users` table Clerk accounts sync into — see land_registry/local_auth.py)
rather than shelling out to aecs4u-auth's own `aecs4u_auth.cli.users`
command: that CLI builds its own async engine with no
`statement_cache_size=0`, which trips "cached statement plan is invalid"
errors against Neon's pooled endpoint (PgBouncer transaction pooling)
almost immediately. This wrapper reuses the already-pooler-safe engine
land_registry itself uses.

There is no self-registration page — local accounts (a handful of
admin/service logins alongside the public Clerk sign-in) are meant to be
created here, out of band.

Usage:
    uv run python scripts/local_users.py create --email dev@example.com --password secret123
    uv run python scripts/local_users.py list
    uv run python scripts/local_users.py set-password --email dev@example.com --password newpass
    uv run python scripts/local_users.py delete --email dev@example.com
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from land_registry.config import db_settings  # noqa: E402  (loads .env as a side effect)
from land_registry import local_auth  # noqa: E402


async def _run(args: argparse.Namespace) -> None:
    if not db_settings.database_url:
        sys.exit(
            "DATABASE_URL is not set (check .env) — local accounts are stored in this "
            "app's Postgres database, so there's nowhere to create them without it."
        )

    from aecs4u_auth import get_password_hash
    from aecs4u_auth.users.models import User

    local_auth.init_engine(local_auth.to_asyncpg_url(db_settings.database_url))
    await local_auth.ensure_user_table()
    storage = local_auth.get_storage()

    if args.command == "create":
        existing = await storage.get_user_by_email(args.email)
        if existing:
            sys.exit(f"A user with email '{args.email}' already exists (id={existing.id}).")
        user = User(
            id=str(uuid.uuid4()),
            clerk_id=f"local_{uuid.uuid4().hex[:8]}",
            email=args.email,
            username=args.username,
            first_name=args.first_name,
            last_name=args.last_name,
            hashed_password=get_password_hash(args.password),
            email_verified=True,
        )
        await storage.upsert_user(user)
        print(f"Created local user {user.email} (id={user.id})")

    elif args.command == "list":
        users = await storage.list_users(limit=args.limit)
        if not users:
            print("No users found")
        for u in users:
            kind = "local" if u.clerk_id.startswith("local_") else "clerk"
            print(f"  [{kind}] {u.email}  id={u.id}  username={u.username or '-'}")

    elif args.command == "set-password":
        user = await storage.get_user_by_email(args.email)
        if not user:
            sys.exit(f"No user with email '{args.email}'")
        user.hashed_password = get_password_hash(args.password)
        await storage.upsert_user(user)
        print(f"Password updated for {user.email}")

    elif args.command == "delete":
        user = await storage.get_user_by_email(args.email)
        if not user:
            sys.exit(f"No user with email '{args.email}'")
        await storage.delete_user(user.id)
        print(f"Deleted {user.email}")

    await local_auth.dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local (non-Clerk) Land Registry accounts.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new local account")
    p_create.add_argument("--email", required=True)
    p_create.add_argument("--password", required=True)
    p_create.add_argument("--username", default=None)
    p_create.add_argument("--first-name", dest="first_name", default=None)
    p_create.add_argument("--last-name", dest="last_name", default=None)

    p_list = sub.add_parser("list", help="List accounts (local and Clerk-synced)")
    p_list.add_argument("--limit", type=int, default=50)

    p_setpw = sub.add_parser("set-password", help="Change a local account's password")
    p_setpw.add_argument("--email", required=True)
    p_setpw.add_argument("--password", required=True)

    p_delete = sub.add_parser("delete", help="Delete an account")
    p_delete.add_argument("--email", required=True)

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
