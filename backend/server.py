"""Local HTTP API and static server for the M2 Wallet demo.

The server deliberately uses only Python's standard library so the internal demo
can run on a clean machine without downloading application dependencies.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import mimetypes
import os
import re
import time
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.store import M2WalletStore
from backend.payout import HttpWithdrawalVerifier, PayoutService
from backend.addresses import address_provider_from_env
from backend.auth import Principal, SessionAuth
from backend.callback_worker import CallbackWorker


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_DIR = ROOT / "prototype"
DEFAULT_HOST = os.environ.get("M2_WALLET_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("M2_WALLET_PORT", "8787"))
API_KEY = os.environ.get("M2_WALLET_API_KEY") or secrets.token_urlsafe(32)
MAX_BODY_BYTES = 64 * 1024
CALLBACK_SECRET = os.environ.get("M2_CALLBACK_SECRET") or secrets.token_urlsafe(32)
os.environ.setdefault("M2_WALLET_API_KEY", API_KEY)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class M2WalletHandler(BaseHTTPRequestHandler):
    server_version = "M2WalletDemo/0.1"

    @property
    def store(self) -> M2WalletStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def payout(self) -> PayoutService:
        return self.server.payout  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _headers(self, status: int, content_type: str, length: int, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status: int, data: Any, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self._headers(status, "application/json; charset=utf-8", len(body), extra_headers)
        self.wfile.write(body)

    def _error(self, error: Exception) -> None:
        if isinstance(error, ApiError):
            status, message = error.status, error.message
        elif isinstance(error, KeyError):
            status, message = HTTPStatus.NOT_FOUND, str(error).strip("'")
        elif isinstance(error, (ValueError, json.JSONDecodeError)):
            status, message = HTTPStatus.BAD_REQUEST, str(error)
        else:
            status, message = HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error"
            print(f"Unhandled error: {error!r}")
        self._json(int(status), {"error": {"message": message}})

    def _principal(self) -> Principal:
        supplied_key = self.headers.get("X-API-Key", "")
        if supplied_key:
            if not self.store.is_ip_allowed(self.client_address[0]):
                raise ApiError(HTTPStatus.FORBIDDEN, "source IP is not on the allowlist")
            record = self.store.resolve_api_key(supplied_key)
            if not record:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid or disabled API key")
            if record["id"] == "API-ENVIRONMENT":
                return Principal(record["id"], record["name"], "ADMIN", tuple(record.get("scopes", [])))
            required_scope = self._required_api_scope()
            if required_scope == "console-only":
                raise ApiError(HTTPStatus.FORBIDDEN, "this endpoint requires an administrator session")
            scopes = tuple(record.get("scopes", []))
            if required_scope and required_scope not in scopes:
                raise ApiError(HTTPStatus.FORBIDDEN, f"API key is missing scope {required_scope}")
            return Principal(record["id"], record["name"], "API", scopes)
        principal = self.server.auth.resolve(self.headers.get("Cookie", ""), "")  # type: ignore[attr-defined]
        if not principal:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "login required")
        return principal

    def _required_api_scope(self) -> str | None:
        path = urlparse(self.path).path
        if path.startswith("/api/v1/api-keys") or path in {"/api/v1/session", "/api/v1/audit-logs"}:
            return "console-only"
        if self.command == "GET":
            if path.startswith("/api/v1/payment-orders"):
                return "payments:read"
            if path.startswith("/api/v1/withdrawals"):
                return "withdrawals:read"
            return "operations:read"
        if path == "/api/v1/payment-orders" or path.startswith("/api/v1/payment-orders/"):
            return "payments:write"
        if path == "/api/v1/withdrawals":
            return "withdrawals:write"
        if path.startswith("/api/v1/withdrawals/") or path.startswith("/api/v1/callbacks/") or path == "/api/v1/callbacks/deliver-pending" or path == "/api/v1/collections/run":
            return "operations:write"
        return "console-only"

    def _require_roles(self, *roles: str) -> Principal:
        principal = self._principal()
        if principal.role == "API":
            return principal
        if roles and principal.role not in roles:
            raise ApiError(HTTPStatus.FORBIDDEN, "this account does not have permission")
        return principal

    def _audit(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str = "SUCCESS",
        detail: str | None = None,
    ) -> None:
        self.store.record_audit(
            principal.username, principal.role, action, resource_type, resource_id, outcome, detail
        )

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        raw = self.rfile.read(length)
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
        return value

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (PROTOTYPE_DIR / relative).resolve()
        if PROTOTYPE_DIR.resolve() not in target.parents and target != PROTOTYPE_DIR.resolve():
            raise ApiError(HTTPStatus.NOT_FOUND, "file not found")
        if not target.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "file not found")
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._headers(HTTPStatus.OK, content_type, len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            if path == "/api/v1/health":
                self._json(HTTPStatus.OK, {"status": "ok", "mode": os.environ.get("M2_BROADCAST_MODE", "simulation").lower()})
                return
            checkout_match = re.fullmatch(r"/pay/([^/]+)", path)
            if checkout_match:
                self._serve_static("/checkout.html")
                return
            public_payment_match = re.fullmatch(r"/api/v1/public/payment-orders/([^/]+)", path)
            if public_payment_match:
                record = self.store.get_payment(public_payment_match.group(1))
                if not record:
                    raise ApiError(HTTPStatus.NOT_FOUND, "payment order not found")
                public_fields = (
                    "id", "merchant_order_id", "merchant", "customer_id", "amount", "order_currency",
                    "pay_currency", "network", "pay_address", "paid_amount", "status", "return_url", "expires_at", "created_at", "updated_at",
                )
                self._json(HTTPStatus.OK, {"data": {field: record[field] for field in public_fields}})
                return
            if path.startswith("/api/"):
                principal = self._principal()
                if path == "/api/v1/session":
                    self._json(HTTPStatus.OK, {"data": principal.__dict__})
                    return
                if path == "/api/v1/audit-logs":
                    self._require_roles("ADMIN", "FINANCE")
                    self._json(HTTPStatus.OK, {"data": self.store.audit_logs()})
                    return
                if path == "/api/v1/api-keys":
                    self._require_roles("ADMIN")
                    self._json(HTTPStatus.OK, {"data": self.store.list_api_keys()})
                    return
                if path == "/api/v1/risk-policy":
                    self._json(HTTPStatus.OK, {"data": self.store.risk_policy()})
                    return
                if path == "/api/v1/collection-policy":
                    self._json(HTTPStatus.OK, {"data": self.store.collection_policy()})
                    return
                if path == "/api/v1/project-settings":
                    self._json(HTTPStatus.OK, {"data": self.store.project_settings()})
                    return
                payment_callbacks_match = re.fullmatch(r"/api/v1/payment-orders/([^/]+)/callbacks", path)
                if payment_callbacks_match:
                    record = self.store.get_payment_by_reference(payment_callbacks_match.group(1))
                    if not record:
                        raise ApiError(HTTPStatus.NOT_FOUND, "payment order not found")
                    self._json(HTTPStatus.OK, {"data": self.store.callbacks_for(record["id"])})
                    return
                payment_detail_match = re.fullmatch(r"/api/v1/payment-orders/([^/]+)", path)
                if payment_detail_match:
                    record = self.store.get_payment_by_reference(payment_detail_match.group(1))
                    if not record:
                        raise ApiError(HTTPStatus.NOT_FOUND, "payment order not found")
                    self._json(HTTPStatus.OK, {"data": record})
                    return
                withdrawal_events_match = re.fullmatch(r"/api/v1/withdrawals/([^/]+)/events", path)
                if withdrawal_events_match:
                    record = self.store.get_withdrawal_by_reference(withdrawal_events_match.group(1))
                    if not record:
                        raise ApiError(HTTPStatus.NOT_FOUND, "withdrawal not found")
                    self._json(HTTPStatus.OK, {"data": self.store.withdrawal_events_for(record["id"])})
                    return
                withdrawal_callbacks_match = re.fullmatch(r"/api/v1/withdrawals/([^/]+)/callbacks", path)
                if withdrawal_callbacks_match:
                    record = self.store.get_withdrawal_by_reference(withdrawal_callbacks_match.group(1))
                    if not record:
                        raise ApiError(HTTPStatus.NOT_FOUND, "withdrawal not found")
                    self._json(HTTPStatus.OK, {"data": self.store.callbacks_for(record["id"])})
                    return
                withdrawal_detail_match = re.fullmatch(r"/api/v1/withdrawals/([^/]+)", path)
                if withdrawal_detail_match:
                    record = self.store.get_withdrawal_by_reference(withdrawal_detail_match.group(1))
                    if not record:
                        raise ApiError(HTTPStatus.NOT_FOUND, "withdrawal not found")
                    self._json(HTTPStatus.OK, {"data": record})
                    return
                routes = {
                    "/api/v1/payment-orders": self.store.list_payments,
                    "/api/v1/withdrawals": self.store.list_withdrawals,
                    "/api/v1/ledger": self.store.ledger,
                    "/api/v1/callbacks": self.store.callbacks,
                    "/api/v1/withdrawal-events": self.store.withdrawal_events,
                    "/api/v1/reconciliation": self.store.reconciliation_report,
                    "/api/v1/transactions": self.store.business_transactions,
                    "/api/v1/collections": self.store.list_collections,
                    "/api/v1/collection-candidates": self.store.collection_candidates,
                    "/api/v1/address-book": self.store.list_address_book,
                    "/api/v1/ip-allowlist": self.store.list_ip_allowlist,
                    "/api/v1/demo-merchant/webhooks": self.store.demo_webhook_receipts,
                    "/api/v1/demo-readiness": self.store.demo_readiness,
                    "/api/v1/network-reserves": self.store.network_reserves,
                    "/api/v1/network-fee-events": self.store.network_fee_events,
                }
                if path not in routes:
                    raise ApiError(HTTPStatus.NOT_FOUND, "API route not found")
                self._json(HTTPStatus.OK, {"data": routes[path]()})
                return
            self._serve_static(path)
        except Exception as error:
            self._error(error)

    def do_POST(self) -> None:  # noqa: N802
        try:
            path, payload = urlparse(self.path).path, self._body()
            if path == "/api/v1/demo-merchant/webhook":
                timestamp = self.headers.get("X-M2-Timestamp", "")
                supplied = self.headers.get("X-M2-Signature", "")
                try:
                    fresh = abs(int(time.time()) - int(timestamp)) <= 300
                except ValueError:
                    fresh = False
                raw = json.dumps(payload).encode()
                expected = hmac.new(CALLBACK_SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
                valid = fresh and hmac.compare_digest(supplied, f"sha256={expected}")
                if not valid:
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid or expired callback signature")
                receipt = self.store.record_demo_webhook(
                    self.headers.get("X-M2-Event", "unknown"),
                    payload,
                    True,
                    self.headers.get("X-M2-Event-Id"),
                )
                self._json(HTTPStatus.OK, {"data": receipt})
                return
            if path == "/api/v1/demo-merchant/withdrawal-validation":
                timestamp = self.headers.get("X-M2-Timestamp", "")
                supplied = self.headers.get("X-M2-Signature", "")
                try:
                    fresh = abs(int(time.time()) - int(timestamp)) <= 300
                except ValueError:
                    fresh = False
                raw = json.dumps(payload).encode()
                expected = hmac.new(CALLBACK_SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
                valid = fresh and hmac.compare_digest(supplied, f"sha256={expected}")
                if not valid:
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid or expired verification signature")
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                approved = not bool(metadata.get("force_verification_reject"))
                reason = "approved by local merchant sandbox" if approved else "customer withdrawal is no longer valid"
                self.store.record_demo_webhook(
                    "withdrawal.validation", payload, True, self.headers.get("X-M2-Event-Id")
                )
                self._json(HTTPStatus.OK, {"approved": approved, "reason": reason})
                return
            if path == "/api/v1/session":
                username = str(payload.get("username", ""))
                result = self.server.auth.login(username, str(payload.get("password", "")))  # type: ignore[attr-defined]
                if not result:
                    self.store.record_audit(username or "unknown", "UNKNOWN", "LOGIN", "SESSION", username, "DENIED", "invalid credentials")
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid username or password")
                token, principal = result
                self._audit(principal, "LOGIN", "SESSION", principal.username)
                self._json(
                    HTTPStatus.OK,
                    {"data": principal.__dict__},
                    {"Set-Cookie": f"m2_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"},
                )
                return
            if path == "/api/v1/session/logout":
                principal = self._principal()
                self.server.auth.logout(self.headers.get("Cookie", ""))  # type: ignore[attr-defined]
                self._audit(principal, "LOGOUT", "SESSION", principal.username)
                self._json(HTTPStatus.OK, {"data": {"logged_out": True}}, {"Set-Cookie": "m2_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
                return
            if path == "/api/v1/risk-policy":
                principal = self._require_roles("ADMIN")
                policy = self.store.update_risk_policy(payload, principal.username)
                self._audit(principal, "UPDATE", "RISK_POLICY", "payout", detail=json.dumps(policy, ensure_ascii=False))
                self._json(HTTPStatus.OK, {"data": policy})
                return
            if path == "/api/v1/network-reserves":
                principal = self._require_roles("ADMIN")
                reserve = self.store.update_network_reserve(payload, principal.username)
                self._audit(
                    principal,
                    "UPDATE",
                    "NETWORK_RESERVE",
                    reserve["network"],
                    detail=json.dumps(reserve, ensure_ascii=False),
                )
                self._json(HTTPStatus.OK, {"data": reserve})
                return
            if path == "/api/v1/collection-policy":
                principal = self._require_roles("ADMIN")
                policy = self.store.update_collection_policy(payload, principal.username)
                self._audit(principal, "UPDATE", "COLLECTION_POLICY", "collection", detail=json.dumps(policy, ensure_ascii=False))
                self._json(HTTPStatus.OK, {"data": policy})
                return
            if path == "/api/v1/project-settings":
                principal = self._require_roles("ADMIN")
                settings = self.store.update_project_settings(payload, principal.username)
                self._audit(principal, "UPDATE", "PROJECT_SETTINGS", "M2-PAY", detail=json.dumps(settings, ensure_ascii=False))
                self._json(HTTPStatus.OK, {"data": settings})
                return
            if path == "/api/v1/api-keys":
                principal = self._require_roles("ADMIN")
                record = self.store.create_api_key(payload, principal.username)
                self._audit(principal, "CREATE", "API_KEY", record["id"], detail=f"name={record['name']};scopes={','.join(record['scopes'])}")
                self._json(HTTPStatus.CREATED, {"data": record})
                return
            if path == "/api/v1/callbacks/deliver-pending":
                principal = self._require_roles("ADMIN", "OPERATOR")
                allowed_hosts = {"127.0.0.1", "localhost", "::1"}
                allowed_hosts.update(
                    host.strip().lower()
                    for host in os.environ.get("M2_CALLBACK_ALLOWED_HOSTS", "").split(",")
                    if host.strip()
                )
                result = CallbackWorker(
                    self.store,
                    CALLBACK_SECRET,
                    allowed_hosts,
                    allow_http_loopback=True,
                ).run_once()
                self._audit(principal, "DELIVER", "CALLBACK", None, detail=json.dumps(result))
                self._json(HTTPStatus.OK, {"data": result})
                return
            if path == "/api/v1/ip-allowlist":
                principal = self._require_roles("ADMIN")
                record, created = self.store.create_ip_allowlist_entry(payload, principal.username)
                if created:
                    self._audit(principal, "CREATE", "IP_ALLOWLIST", record["id"], detail=record["cidr"])
                self._json(HTTPStatus.CREATED if created else HTTPStatus.OK, {"data": record, "created": created})
                return
            if path == "/api/v1/collections/run":
                principal = self._require_roles("ADMIN", "OPERATOR")
                record = self.store.run_collection(str(payload.get("network", "")), str(payload.get("asset", "")), principal.username)
                self._audit(principal, "RUN", "COLLECTION", record["id"], detail=f"status={record['status']};amount={record['amount']} {record['asset']}")
                self._json(HTTPStatus.CREATED, {"data": record})
                return
            if path == "/api/v1/address-book":
                principal = self._require_roles("ADMIN")
                record, created = self.store.create_address_book_entry(payload, principal.username)
                if created:
                    self._audit(principal, "CREATE", "ADDRESS_BOOK", record["id"], detail=f"type={record['list_type']};network={record['network']}")
                self._json(HTTPStatus.CREATED if created else HTTPStatus.OK, {"data": record, "created": created})
                return
            if path == "/api/v1/payment-orders":
                principal = self._require_roles("ADMIN", "OPERATOR")
                record, created = self.store.create_payment(payload)
                if created:
                    self._audit(principal, "CREATE", "PAYMENT", record["id"])
                self._json(HTTPStatus.CREATED if created else HTTPStatus.OK, {"data": record, "created": created})
                return
            if path == "/api/v1/withdrawals":
                principal = self._require_roles("ADMIN", "OPERATOR")
                record, created = self.store.create_withdrawal(payload)
                if created:
                    self._audit(principal, "CREATE", "WITHDRAWAL", record["id"])
                self._json(HTTPStatus.CREATED if created else HTTPStatus.OK, {"data": record, "created": created})
                return

            payment_match = re.fullmatch(r"/api/v1/payment-orders/([^/]+)/simulate-confirm", path)
            if payment_match:
                principal = self._require_roles("ADMIN", "OPERATOR")
                current = self.store.get_payment(payment_match.group(1))
                if not current:
                    raise ApiError(HTTPStatus.NOT_FOUND, "payment order not found")
                paid_amount = payload.get("paid_amount")
                if paid_amount is None:
                    paid_amount = Decimal(current["amount"]) - Decimal(current.get("paid_amount") or "0")
                record = self.store.simulate_payment(payment_match.group(1), paid_amount)
                self._audit(principal, "SIMULATE_CONFIRM", "PAYMENT", record["id"])
                self._json(HTTPStatus.OK, {"data": record})
                return
            payment_expire_match = re.fullmatch(r"/api/v1/payment-orders/([^/]+)/simulate-expire", path)
            if payment_expire_match:
                principal = self._require_roles("ADMIN", "OPERATOR")
                record = self.store.expire_payment(payment_expire_match.group(1))
                self._audit(principal, "SIMULATE_EXPIRE", "PAYMENT", record["id"])
                self._json(HTTPStatus.OK, {"data": record})
                return

            withdrawal_match = re.fullmatch(r"/api/v1/withdrawals/([^/]+)/(approve|reject)", path)
            if withdrawal_match:
                principal = self._require_roles("ADMIN", "FINANCE")
                reviewed_by = principal.username
                withdrawal_id, action = withdrawal_match.groups()
                try:
                    record = (
                        self.payout.approve_and_send(withdrawal_id, reviewed_by)
                        if action == "approve"
                        else self.store.reject_withdrawal(withdrawal_id, reviewed_by)
                    )
                except Exception as error:
                    self._audit(principal, action.upper(), "WITHDRAWAL", withdrawal_id, "FAILED", str(error))
                    raise
                self._audit(principal, action.upper(), "WITHDRAWAL", withdrawal_id, detail=f"status={record['status']}")
                self._json(HTTPStatus.OK, {"data": record})
                return
            callback_match = re.fullmatch(r"/api/v1/callbacks/([^/]+)/retry", path)
            if callback_match:
                principal = self._require_roles("ADMIN", "OPERATOR")
                record = self.store.retry_callback(callback_match.group(1))
                self._audit(principal, "RETRY", "CALLBACK", record["id"])
                self._json(HTTPStatus.OK, {"data": record})
                return
            api_key_match = re.fullmatch(r"/api/v1/api-keys/([^/]+)/(rotate|status)", path)
            if api_key_match:
                principal = self._require_roles("ADMIN")
                key_id, action = api_key_match.groups()
                if action == "rotate":
                    record = self.store.rotate_api_key(key_id)
                    self._audit(principal, "ROTATE", "API_KEY", key_id, detail=f"name={record['name']}")
                else:
                    record = self.store.set_api_key_status(key_id, bool(payload.get("enabled")))
                    self._audit(principal, "ENABLE" if record["status"] == "ACTIVE" else "DISABLE", "API_KEY", key_id, detail=f"name={record['name']}")
                self._json(HTTPStatus.OK, {"data": record})
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "API route not found")
        except Exception as error:
            self._error(error)


class M2WalletServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: M2WalletStore | None = None):
        super().__init__(address, M2WalletHandler)
        self.store = store or M2WalletStore(address_provider=address_provider_from_env())
        verification_url = os.environ.get("M2_WITHDRAWAL_VERIFICATION_URL", "").strip()
        if not verification_url and address[0] in {"127.0.0.1", "localhost", "::1"}:
            verification_url = f"http://127.0.0.1:{self.server_port}/api/v1/demo-merchant/withdrawal-validation"
        verifier = (
            HttpWithdrawalVerifier(
                verification_url,
                CALLBACK_SECRET,
                allow_http_loopback=verification_url.startswith(("http://127.0.0.1", "http://localhost")),
            )
            if verification_url
            else None
        )
        self.payout = PayoutService(self.store, verifier=verifier)
        self.auth = SessionAuth(API_KEY)


def main() -> None:
    if DEFAULT_HOST not in {"127.0.0.1", "localhost", "::1"}:
        required = ["M2_WALLET_API_KEY", "M2_ADMIN_PASSWORD", "M2_FINANCE_PASSWORD", "M2_OPERATOR_PASSWORD", "M2_VIEWER_PASSWORD"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"configure credentials before listening beyond localhost: {', '.join(missing)}")
    server = M2WalletServer((DEFAULT_HOST, DEFAULT_PORT))
    print(f"M2 Wallet demo: http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    print(f"API key: {API_KEY} (change M2_WALLET_API_KEY outside local demo)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping M2 Wallet demo")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
