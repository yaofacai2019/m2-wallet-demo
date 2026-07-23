"""Signing and broadcasting boundary for M2 Wallet withdrawals."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.store import M2WalletStore


@dataclass(frozen=True)
class SignedTransaction:
    network: str
    signed_payload: str
    signature_ref: str


class Signer(Protocol):
    def sign(self, intent: dict[str, Any]) -> SignedTransaction: ...


class Broadcaster(Protocol):
    def broadcast(self, transaction: SignedTransaction) -> str: ...


class WithdrawalVerifier(Protocol):
    def verify(self, withdrawal: dict[str, Any]) -> tuple[bool, str]: ...


class HttpWithdrawalVerifier:
    """Signed pre-payout verification call to the merchant platform."""

    def __init__(self, url: str, secret: str, opener=urlopen, allow_http_loopback: bool = False):
        if not url or not secret:
            raise ValueError("withdrawal verification URL and secret are required")
        parsed = urlparse(url)
        is_loopback = (
            allow_http_loopback
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (parsed.scheme != "https" and not is_loopback) or not parsed.hostname:
            raise ValueError("withdrawal verification URL must use HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("withdrawal verification URL cannot contain credentials")
        self.url, self.secret, self.opener = url, secret.encode(), opener

    def verify(self, withdrawal: dict[str, Any]) -> tuple[bool, str]:
        metadata: dict[str, Any] = {}
        if withdrawal.get("metadata_json"):
            loaded = json.loads(withdrawal["metadata_json"])
            if isinstance(loaded, dict):
                metadata = loaded
        payload = {
            "id": withdrawal["id"],
            "merchant_withdraw_id": withdrawal["merchant_withdraw_id"],
            "customer_id": withdrawal.get("user_id"),
            "amount": withdrawal["amount"],
            "asset": withdrawal["currency"],
            "network": withdrawal["network"],
            "to_address": withdrawal["to_address"],
            "metadata": metadata,
        }
        body = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(self.secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-M2-Timestamp": timestamp,
                "X-M2-Signature": f"sha256={signature}",
            },
            method="POST",
        )
        with self.opener(request, timeout=10) as response:
            result = json.loads(response.read())
        return bool(result.get("approved")), str(result.get("reason") or "merchant platform returned no reason")[:300]


class SimulatedSigner:
    """Returns a non-spendable signature envelope; never accepts private keys."""

    def sign(self, intent: dict[str, Any]) -> SignedTransaction:
        canonical = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return SignedTransaction(intent["network"], f"demo-signed:{digest}", f"sim-sign-{digest[:16]}")


class HttpSignerClient:
    """Interface for a separately deployed signer/HSM service."""

    def __init__(self, url: str, token: str, opener=urlopen):
        self.url, self.token, self.opener = url.rstrip("/"), token, opener

    def sign(self, intent: dict[str, Any]) -> SignedTransaction:
        request = Request(
            f"{self.url}/v1/sign",
            data=json.dumps({"intent": intent}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with self.opener(request, timeout=15) as response:
            result = json.loads(response.read())
        return SignedTransaction(intent["network"], str(result["signed_payload"]), str(result["signature_ref"]))


class SimulatedBroadcaster:
    def broadcast(self, transaction: SignedTransaction) -> str:
        digest = hashlib.sha256(transaction.signed_payload.encode()).hexdigest()
        return digest if transaction.network == "TRON" else "0x" + digest


class RpcBroadcaster:
    """Broadcast externally signed payloads to TRON or EVM JSON-RPC endpoints."""

    def __init__(self, tron_url: str = "", polygon_rpc_url: str = "", opener=urlopen):
        self.tron_url = tron_url.rstrip("/")
        self.polygon_rpc_url = polygon_rpc_url
        self.opener = opener

    def broadcast(self, transaction: SignedTransaction) -> str:
        if transaction.network == "TRON":
            if not self.tron_url:
                raise RuntimeError("M2_TRON_BROADCAST_URL is required for TRON broadcasts")
            signed = json.loads(transaction.signed_payload)
            request = Request(
                f"{self.tron_url}/wallet/broadcasttransaction",
                data=json.dumps(signed).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.opener(request, timeout=15) as response:
                result = json.loads(response.read())
            if not result.get("result"):
                raise RuntimeError(f"TRON broadcast rejected: {result.get('message', 'unknown error')}")
            return str(result.get("txid") or signed.get("txID"))
        if transaction.network == "POLYGON":
            if not self.polygon_rpc_url:
                raise RuntimeError("M2_POLYGON_RPC_URL is required for Polygon broadcasts")
            request = Request(
                self.polygon_rpc_url,
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": [transaction.signed_payload]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.opener(request, timeout=15) as response:
                result = json.loads(response.read())
            if result.get("error"):
                raise RuntimeError(f"Polygon broadcast rejected: {result['error'].get('message', 'unknown error')}")
            return str(result["result"])
        raise ValueError("unsupported broadcast network")


class PayoutService:
    def __init__(
        self,
        store: M2WalletStore,
        signer: Signer | None = None,
        broadcaster: Broadcaster | None = None,
        auto_confirm: bool | None = None,
        verifier: WithdrawalVerifier | None = None,
    ):
        self.store = store
        self.signer = signer or self._default_signer()
        self.broadcaster = broadcaster or self._default_broadcaster()
        self.auto_confirm = isinstance(self.broadcaster, SimulatedBroadcaster) if auto_confirm is None else auto_confirm
        self.verifier = verifier

    def _enforce_policy(self, withdrawal: dict[str, Any]) -> None:
        policy = self.store.risk_policy()
        if not policy["payouts_enabled"]:
            raise RuntimeError("payouts are paused by risk policy")
        raw_limit = policy["max_withdrawal_amount"]
        try:
            limit = Decimal(raw_limit)
            amount = Decimal(str(withdrawal["amount"]))
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError("M2_MAX_WITHDRAWAL_AMOUNT must be a positive number") from exc
        if limit <= 0:
            raise RuntimeError("M2_MAX_WITHDRAWAL_AMOUNT must be a positive number")
        if amount > limit:
            raise RuntimeError(f"withdrawal exceeds the configured single-payment limit ({limit})")
        try:
            daily_limit = Decimal(policy["daily_withdrawal_limit"])
            daily_total = Decimal(policy["daily_withdrawn_amount"])
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError("M2_DAILY_WITHDRAWAL_LIMIT must be a positive number") from exc
        if daily_limit <= 0:
            raise RuntimeError("M2_DAILY_WITHDRAWAL_LIMIT must be a positive number")
        if withdrawal["status"] == "PENDING_APPROVAL" and daily_total + amount > daily_limit:
            raise RuntimeError(f"withdrawal exceeds the configured daily payout limit ({daily_limit})")
        reserve = next(
            (item for item in self.store.network_reserves() if item["network"] == withdrawal["network"]),
            None,
        )
        if not reserve:
            raise RuntimeError(f"network fee reserve is not configured for {withdrawal['network']}")
        if not reserve["healthy"]:
            raise RuntimeError(
                f"{withdrawal['network']} fee reserve cannot fund another transaction above the minimum "
                f"({reserve['available']} {reserve['native_asset']} available; "
                f"{reserve['minimum_required']} minimum; "
                f"{reserve['estimated_per_transaction']} estimated per transaction)"
            )

    @staticmethod
    def _default_signer() -> Signer:
        signer_url = os.environ.get("M2_SIGNER_URL")
        if signer_url:
            token = os.environ.get("M2_SIGNER_TOKEN")
            if not token:
                raise RuntimeError("M2_SIGNER_TOKEN is required when M2_SIGNER_URL is set")
            return HttpSignerClient(signer_url, token)
        return SimulatedSigner()

    @staticmethod
    def _default_broadcaster() -> Broadcaster:
        mode = os.environ.get("M2_BROADCAST_MODE", "simulation").lower()
        if mode == "simulation":
            return SimulatedBroadcaster()
        if mode == "rpc":
            return RpcBroadcaster(
                os.environ.get("M2_TRON_BROADCAST_URL", ""),
                os.environ.get("M2_POLYGON_RPC_URL", ""),
            )
        raise RuntimeError("M2_BROADCAST_MODE must be simulation or rpc")

    def approve_and_send(self, withdrawal_id: str, reviewed_by: str) -> dict[str, Any]:
        pending = self.store.get_withdrawal(withdrawal_id)
        if not pending:
            raise KeyError("withdrawal not found")
        if pending["status"] == "CONFIRMED":
            return pending
        self._enforce_policy(pending)
        if self.store.project_settings()["withdrawal_verification_enabled"]:
            if not self.verifier:
                raise RuntimeError("external withdrawal verification is enabled but no verifier is configured")
            try:
                approved, reason = self.verifier.verify(pending)
            except Exception as error:
                self.store.record_withdrawal_validation(withdrawal_id, False, f"verification unavailable: {error}")
                raise RuntimeError(f"external withdrawal verification failed: {error}") from error
            self.store.record_withdrawal_validation(withdrawal_id, approved, reason)
            if not approved:
                raise ValueError(f"merchant platform rejected withdrawal: {reason}")
        withdrawal = self.store.approve_withdrawal(withdrawal_id, reviewed_by)
        if withdrawal["status"] == "CONFIRMED":
            return withdrawal
        if withdrawal["status"] == "BROADCASTED":
            return withdrawal
        intent = {
            "reference_id": withdrawal["id"],
            "network": withdrawal["network"],
            "asset": withdrawal["currency"],
            "amount": withdrawal["amount"],
            "to_address": withdrawal["to_address"],
        }
        self.store.reserve_network_fee(withdrawal["network"], "WITHDRAWAL", withdrawal_id, reviewed_by)
        try:
            signed = self.signer.sign(intent)
            self.store.mark_withdrawal_signing(withdrawal_id, signed.signature_ref)
            tx_hash = self.broadcaster.broadcast(signed)
            broadcasted = self.store.mark_withdrawal_broadcasted(withdrawal_id, tx_hash)
            self.store.consume_network_fee("WITHDRAWAL", withdrawal_id)
            return self.store.confirm_withdrawal(withdrawal_id) if self.auto_confirm else broadcasted
        except Exception as error:
            self.store.release_network_fee("WITHDRAWAL", withdrawal_id)
            current = self.store.get_withdrawal(withdrawal_id)
            if current and current["status"] in ("APPROVED", "SIGNING", "BROADCASTED"):
                self.store.fail_withdrawal(withdrawal_id, str(error))
            raise
