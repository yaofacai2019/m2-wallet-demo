"""Operational reconciliation report for M2 Wallet."""

import json

from backend.store import M2WalletStore


def main() -> None:
    print(json.dumps(M2WalletStore().reconciliation_report(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
