"""Chain confirmation polling for broadcasted M2 Wallet withdrawals."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Protocol
from urllib.request import Request, urlopen

from backend.store import M2WalletStore


class ConfirmationChecker(Protocol):
    def check(self, withdrawal: dict[str, Any]) -> str: ...


class RpcConfirmationChecker:
    def __init__(
        self,
        tron_url: str,
        polygon_rpc_url: str,
        confirmations: int = 20,
        opener=urlopen,
    ):
        self.tron_url = tron_url.rstrip("/")
        self.polygon_rpc_url = polygon_rpc_url
        self.confirmations = max(confirmations, 1)
        self.opener = opener

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with self.opener(request, timeout=15) as response:
            return json.loads(response.read())

    def check(self, withdrawal: dict[str, Any]) -> str:
        if withdrawal["network"] == "TRON":
            if not self.tron_url:
                raise RuntimeError("M2_TRON_BROADCAST_URL is required for TRON confirmation checks")
            info = self._post(f"{self.tron_url}/wallet/gettransactioninfobyid", {"value": withdrawal["tx_hash"]})
            if not info or "blockNumber" not in info:
                return "PENDING"
            if (info.get("receipt") or {}).get("result") not in (None, "SUCCESS"):
                return "FAILED"
            latest = self._post(f"{self.tron_url}/wallet/getnowblock", {})
            latest_number = int(latest["block_header"]["raw_data"]["number"])
            return "CONFIRMED" if latest_number - int(info["blockNumber"]) + 1 >= self.confirmations else "PENDING"
        if withdrawal["network"] == "POLYGON":
            if not self.polygon_rpc_url:
                raise RuntimeError("M2_POLYGON_RPC_URL is required for Polygon confirmation checks")
            receipt = self._post(self.polygon_rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [withdrawal["tx_hash"]]}).get("result")
            if not receipt:
                return "PENDING"
            if receipt.get("status") != "0x1":
                return "FAILED"
            latest_hex = self._post(self.polygon_rpc_url, {"jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber", "params": []})["result"]
            confirmations = int(latest_hex, 16) - int(receipt["blockNumber"], 16) + 1
            return "CONFIRMED" if confirmations >= self.confirmations else "PENDING"
        raise ValueError("unsupported confirmation network")


class WithdrawalConfirmationWorker:
    def __init__(self, store: M2WalletStore, checker: ConfirmationChecker):
        self.store, self.checker = store, checker

    def run_once(self) -> dict[str, int]:
        broadcasted = [item for item in self.store.list_withdrawals() if item["status"] == "BROADCASTED"]
        confirmed = failed = errors = 0
        for withdrawal in broadcasted:
            try:
                status = self.checker.check(withdrawal)
                if status == "CONFIRMED":
                    self.store.confirm_withdrawal(withdrawal["id"])
                    confirmed += 1
                elif status == "FAILED":
                    self.store.fail_withdrawal(withdrawal["id"], "chain transaction failed")
                    failed += 1
            except Exception as error:
                errors += 1
                print(f"confirmation error for {withdrawal['id']}: {error}")
        return {"checked": len(broadcasted), "confirmed": confirmed, "failed": failed, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll broadcasted withdrawal confirmations")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    checker = RpcConfirmationChecker(
        os.environ.get("M2_TRON_BROADCAST_URL", ""),
        os.environ.get("M2_POLYGON_RPC_URL", ""),
        int(os.environ.get("M2_WITHDRAW_CONFIRMATIONS", "20")),
    )
    worker = WithdrawalConfirmationWorker(M2WalletStore(), checker)
    while True:
        print(json.dumps(worker.run_once(), ensure_ascii=False))
        if args.once:
            return
        time.sleep(max(args.interval, 2))


if __name__ == "__main__":
    main()
