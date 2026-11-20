"""Administrative CLI.

    python -m app.cli migrate
    python -m app.cli create-company --name "Coffee LLP" [--external-id mp-123]
    python -m app.cli create-key --company-id <id> [--role admin] [--user-external-id u-1] [--name backend]
    python -m app.cli revoke-key --key-prefix aie_xxxxxx
    python -m app.cli seed-demo        # two fictional companies with keys, for local testing
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.database.models import ApiKey, Company, User
from app.database.session import build_engine, build_sessionmaker, run_migrations
from app.security.auth import create_api_key


def _session():
    settings = get_settings()
    run_migrations(settings.database_url)
    return build_sessionmaker(build_engine(settings))()


def cmd_migrate(_: argparse.Namespace) -> None:
    run_migrations(get_settings().database_url)
    print("Database is at the latest revision.")


def cmd_create_company(args: argparse.Namespace) -> None:
    with _session() as db:
        company = Company(name=args.name, external_id=args.external_id)
        db.add(company)
        db.commit()
        print(f"company_id={company.id}")


def cmd_create_key(args: argparse.Namespace) -> None:
    with _session() as db:
        company = db.get(Company, args.company_id)
        if company is None:
            sys.exit("Company not found")
        user = None
        if args.user_external_id:
            user = db.scalar(select(User).where(User.company_id == company.id,
                                                User.external_id == args.user_external_id))
        if user is None:
            user = User(company_id=company.id, external_id=args.user_external_id, role=args.role,
                        display_name=args.name)
            db.add(user)
            db.flush()
        _, raw = create_api_key(db, company_id=company.id, user_id=user.id, name=args.name)
        db.commit()
        print(f"user_id={user.id}\nrole={user.role}\napi_key={raw}\n(Store it now - it is not shown again.)")


def cmd_revoke_key(args: argparse.Namespace) -> None:
    with _session() as db:
        keys = list(db.scalars(select(ApiKey).where(ApiKey.key_prefix == args.key_prefix)))
        for key in keys:
            key.is_active = False
        db.commit()
        print(f"revoked={len(keys)}")


def cmd_seed_demo(_: argparse.Namespace) -> None:
    with _session() as db:
        for name in ("Demo Coffee Almaty (fictional)", "Other Company (fictional)"):
            company = Company(name=name)
            db.add(company)
            db.flush()
            for role in ("admin", "member"):
                user = User(company_id=company.id, role=role, display_name=f"{role} of {name}")
                db.add(user)
                db.flush()
                _, raw = create_api_key(db, company_id=company.id, user_id=user.id, name=f"demo-{role}")
                print(f"{name:<34} {role:<7} {raw}")
        db.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate").set_defaults(func=cmd_migrate)
    p = sub.add_parser("create-company")
    p.add_argument("--name", required=True)
    p.add_argument("--external-id")
    p.set_defaults(func=cmd_create_company)
    p = sub.add_parser("create-key")
    p.add_argument("--company-id", required=True)
    p.add_argument("--role", choices=["member", "admin"], default="member")
    p.add_argument("--user-external-id")
    p.add_argument("--name", default="default")
    p.set_defaults(func=cmd_create_key)
    p = sub.add_parser("revoke-key")
    p.add_argument("--key-prefix", required=True)
    p.set_defaults(func=cmd_revoke_key)
    sub.add_parser("seed-demo").set_defaults(func=cmd_seed_demo)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
