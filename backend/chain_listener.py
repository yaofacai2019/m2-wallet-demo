"""Confirmed TRC-20 inbound transfer listener for M2 Wallet."""

from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.store import M2WalletStore


DEFAULT_TRONGRID_URL = os.environ.get("M2_TRONGRID_URL", "https://api.trongrid.io")
TRON_USDT_CONTRACT = os.environ.get("M2_TRON_USDT_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
TRONGRID_API_KEY = os.environ.get("M2_TRONGRID_API_KEY", "")


class TronGridClient:
    def __init__(
        self,
        base_url: str = DEFAULT_TRONGRID_URL,
        api_key: str = TRONGRID_API_KEY,
        opener: Callable[..., Any] = urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.opener = opener

    def confirmed_trc20_inbound(self, address: str, contract_address: str) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "only_confirmed": "true",
                "only_to": "true",
                "limit": "200",
                "order_by": "block_timestamp,desc",
                "contract_address": contract_address,
            }
        )
        request = Request(f"{self.base_url}/v1/accounts/{address}/transactions/trc20?{query}")
        request.add_header("Accept", "application/json")
        if self.api_key:
            request.add_header("TRON-PRO-API-KEY", self.api_key)
        with self.opener(request, timeout=15) as response:
            payload = json.loads(response.read())
        if not payload.get("success", True):
            raise RuntimeError("TronGrid returned an unsuccessful response")
        return list(payload.get("data", []))


class TronUsdtListener:
    def __init__(
        self,
        store: M2WalletStore,
        client: TronGridClient | None = None,
        contract_address: str = TRON_USDT_CONTRACT,
    ):
        self.store = store
        self.client = client or TronGridClient()
        self.contract_address = contract_address

    def run_once(self) -> dict[str, int]:
        scanned = matched = errors = 0
        pending = [
            order
            for order in self.store.list_payments()
            if order["status"] == "PENDING" and order["network"] == "TRON" and order["pay_currency"] == "USDT"
        ]
        for order in pending:
            try:
                transfers = self.client.confirmed_trc20_inbound(order["pay_address"], self.contract_address)
                scanned += len(transfers)
                for transfer in transfers:
                    token = transfer.get("token_info") or {}
                    if token.get("address") and token["address"] != self.contract_address:
                        continue
                    if transfer.get("to") != order["pay_address"] or transfer.get("type") not in (None, "Transfer", "transfer"):
                        continue
                    decimals = int(token.get("decimals", 6))
                    amount = Decimal(str(transfer["value"])) / (Decimal(10) ** decimals)
                    result = self.store.match_inbound_transfer(
                        "TRON", "USDT", order["pay_address"], amount, str(transfer["transaction_id"])
                    )
                    if result:
                        matched += 1
                        break
            except Exception as error:
                errors += 1
                print(f"TRON listener error for {order['id']}: {error}")
        return {"orders": len(pending), "transfers": scanned, "matched": matched, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll confirmed TRON USDT transfers")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--interval", type=int, default=10, help="seconds between polls")
    args = parser.parse_args()
    listener = TronUsdtListener(M2WalletStore())
    while True:
        print(json.dumps(listener.run_once(), ensure_ascii=False))
        if args.once:
            return
        time.sleep(max(args.interval, 2))


if __name__ == "__main__":
    main()
