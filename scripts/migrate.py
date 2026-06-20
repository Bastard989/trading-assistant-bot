from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_bot.db import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply versioned Trading Assistant migrations")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    if not args.database.exists():
        args.database.parent.mkdir(parents=True, exist_ok=True)
    Database(args.database, auto_migrate=True)
    print("migrations_ok")


if __name__ == "__main__":
    main()
