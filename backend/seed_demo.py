"""Idempotently seed safe, simulated data for an empty M2 Wallet demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.addresses import SimulatedAddressProvider
from backend.payout import PayoutService
from backend.store import M2WalletStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed safe M2 Wallet demo records")
    parser.add_argument("--db", type=Path, help="database path (defaults to data/m2-wallet.db)")
    args = parser.parse_args()
    store = M2WalletStore(args.db) if args.db else M2WalletStore()
    payment, _ = store.create_payment(
        {
            "merchant_order_id": "DEMO-PAY-001",
            "merchant": "Internal Demo Merchant",
            "amount": "500",
            "order_currency": "USD",
            "pay_currency": "USDT",
            "network": "TRON",
            "fee_rate_bps": 50,
        }
    )
    store.confirm_payment(payment["id"])
    address = SimulatedAddressProvider().create_address("TRON", "USDT", "seed-withdrawal")
    withdrawal, _ = store.create_withdrawal(
        {
            "merchant_withdraw_id": "DEMO-WD-001",
            "merchant": "Internal Demo Merchant",
            "user_id": "demo-user",
            "amount": "100",
            "currency": "USDT",
            "network": "TRON",
            "to_address": address,
        }
    )
    PayoutService(store).approve_and_send(withdrawal["id"], "demo-finance")
    print("M2 Wallet demo data is ready")


if __name__ == "__main__":
    main()
