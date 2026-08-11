"""Create and disable administrator-provisioned workbench accounts."""

import argparse
import getpass
from datetime import UTC, datetime

from pwdlib import PasswordHash

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.repositories.workbench.users import SqlAlchemyUserRepository


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m lucking.admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("display_name")
    disable = subcommands.add_parser("disable-user")
    disable.add_argument("username")
    args = parser.parse_args()

    engine = create_database_engine(Settings())
    repository = SqlAlchemyUserRepository(create_session_factory(engine))
    if args.command == "create-user":
        password = getpass.getpass("密码（12-128 字符）：")
        if not 12 <= len(password) <= 128:
            parser.error("密码长度必须为 12 至 128 个字符")
        repository.create(
            args.username.strip().lower(),
            args.display_name.strip(),
            PasswordHash.recommended().hash(password),
            datetime.now(UTC),
        )
        print("账号已创建")
    elif not repository.disable(args.username.strip().lower()):
        parser.error("账号不存在")
    else:
        print("账号已禁用")


if __name__ == "__main__":
    main()
