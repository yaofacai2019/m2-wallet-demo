"""Confirmed Polygon USDC inbound transfer listener for M2 Wallet."""

from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal
from typing import Any, Callable
from urllib.request import Request, urlopen

from backend.store import M2WalletStore


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
POLYGON_USDC_CONTRACT = os.environ.get(
    "M2_POLYGON_USDC_CONTRACT", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
)
POLYGON_RPC_URL = os.environ.get("M2_POLYGON_RPC_URL", "")
POLYGON_CONFIRMATIONS = int(os.environ.get("M2_POLYGON_CONFIRMATIONS", "20"))
POLYGON_LOOKBACK_BLOCKS = int(os.environ.get("M2_POLYGON_LOOKBACK_BLOCKS", "2000"))


class EvmJsonRpcClient:
    def __init__(self, rpc_url: str, opener: Callable[..., Any] = urlopen):
        if not rpc_url:
            raise ValueError("M2_POLYGON_RPC_URL is required for the Polygon listener")
        self.rpc_url = rpc_url
        self.opener = opener
        self._request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        ).encode()
        request = Request(
            self.rpc_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with self.opener(request, timeout=15) as response:
            payload = json.loads(response.read())
        if payload.get("error"):
            error = payload["error"]
            raise RuntimeError(f"EVM RPC error {error.get('code')}: {error.get('message')}")
        if "result" not in payload:
            raise RuntimeError("EVM RPC response has no result")
        return payload["result"]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def transfer_logs(
        self, contract_address: str, recipient: str, from_block: int, to_block: int
    ) -> list[dict[str, Any]]:
        recipient_topic = "0x" + ("0" * 24) + recipient.removeprefix("0x").lower()
        result = self.call(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "address": contract_address,
                    "topics": [TRANSFER_TOPIC, None, recipient_topic],
                }
            ],
        )
        return list(result)


class PolygonUsdcListener:
    def __init__(
        self,
        store: M2WalletStore,
        client: EvmJsonRpcClient,
        contract_address: str = POLYGON_USDC_CONTRACT,
        confirmations: int = POLYGON_CONFIRMATIONS,
        lookback_blocks: int = POLYGON_LOOKBACK_BLOCKS,
    ):
        self.store = store
        self.client = client
        self.contract_address = contract_address
        self.confirmations = max(confirmations, 1)
        self.lookback_blocks = max(lookback_blocks, 1)

    def run_once(self) -> dict[str, int]:
        pending = [
            order
            for order in self.store.list_payments()
            if order["status"] == "PENDING"
            and order["network"] == "POLYGON"
            and order["pay_currency"] == "USDC"
        ]
        if not pending:
            return {"orders": 0, "logs": 0, "matched": 0, "errors": 0}
        try:
            latest = self.client.block_number()
        except Exception as error:
            print(f"Polygon listener block error: {error}")
            return {"orders": len(pending), "logs": 0, "matched": 0, "errors": len(pending)}
        confirmed_to = latest - self.confirmations
        if confirmed_to < 0:
            return {"orders": len(pending), "logs": 0, "matched": 0, "errors": 0}
        from_block = max(0, confirmed_to - self.lookback_blocks + 1)
        scanned = matched = errors = 0
        for order in pending:
            try:
                logs = self.client.transfer_logs(
                    self.contract_address, order["pay_address"], from_block, confirmed_to
                )
                scanned += len(logs)
                for log in logs:
                    if log.get("removed") is True:
                        continue
                    if str(log.get("address", "")).lower() != self.contract_address.lower():
                        continue
                    topics = log.get("topics") or []
                    if len(topics) < 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
                        continue
                    recipient = "0x" + str(topics[2])[-40:].lower()
                    if recipient != order["pay_address"].lower():
                        continue
                    amount = Decimal(int(str(log["data"]), 16)) / Decimal(1_000_000)
                    result = self.store.match_inbound_transfer(
                        "POLYGON", "USDC", order["pay_address"], amount, str(log["transactionHash"])
                    )
                    if result:
                        matched += 1
                        break
            except Exception as error:
                errors += 1
                print(f"Polygon listener error for {order['id']}: {error}")
        return {"orders": len(pending), "logs": scanned, "matched": matched, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll confirmed Polygon USDC transfers")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--interval", type=int, default=10, help="seconds between polls")
    args = parser.parse_args()
    listener = PolygonUsdcListener(M2WalletStore(), EvmJsonRpcClient(POLYGON_RPC_URL))
    while True:
        print(json.dumps(listener.run_once(), ensure_ascii=False))
        if args.once:
            return
        time.sleep(max(args.interval, 2))


if __name__ == "__main__":
    main()
