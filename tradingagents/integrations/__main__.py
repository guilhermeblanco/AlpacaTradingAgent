"""Integration-vault setup helpers."""

from __future__ import annotations

import argparse

from cryptography.fernet import Fernet


def main() -> int:
    parser = argparse.ArgumentParser(description="Integration vault utilities")
    parser.add_argument("command", choices=("generate-key",))
    args = parser.parse_args()
    if args.command == "generate-key":
        print(Fernet.generate_key().decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
