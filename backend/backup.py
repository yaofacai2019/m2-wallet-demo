"""Consistent SQLite backup command for M2 Wallet."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.store import DEFAULT_DB


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent M2 Wallet database backup")
    parser.add_argument("--output-dir", default="backups")
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"m2-wallet-{timestamp}.db"
    with sqlite3.connect(DEFAULT_DB) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    print(target)


if __name__ == "__main__":
    main()
