"""SQLite data and accounting layer for the M2 Wallet demo."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from backend.addresses import AddressProvider, SimulatedAddressProvider, validate_evm_address, validate_tron_address

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "m2-wallet.db"
MONEY_QUANTUM = Decimal("0.000001")
SUPPORTED_PAIRS = {("TRON", "USDT"), ("POLYGON", "USDC")}
API_KEY_SCOPES = {
    "payments:read",
    "payments:write",
    "withdrawals:read",
    "withdrawals:write",
    "operations:read",
    "operations:write",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_amount(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("amount must be a decimal string") from exc
    if amount <= 0:
        raise ValueError("amount must be greater than zero")
    return amount


def money(value: Decimal) -> str:
    return format(value, "f")


class M2WalletStore:
    """Thread-safe demo store with idempotent business operations."""

    def __init__(self, db_path: Path | str = DEFAULT_DB, address_provider: AddressProvider | None = None):
        self.db_path = str(db_path)
        self.address_provider = address_provider or SimulatedAddressProvider()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS payment_orders (
                    id TEXT PRIMARY KEY,
                    merchant_order_id TEXT NOT NULL UNIQUE,
                    merchant TEXT NOT NULL,
                    customer_id TEXT,
                    amount TEXT NOT NULL,
                    order_currency TEXT NOT NULL,
                    pay_currency TEXT NOT NULL,
                    network TEXT NOT NULL,
                    fee_rate_bps INTEGER NOT NULL,
                    fee_amount TEXT NOT NULL,
                    paid_amount TEXT NOT NULL DEFAULT '0',
                    pay_address TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    callback_url TEXT,
                    return_url TEXT,
                    metadata_json TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS payment_tx_hash_unique_idx
                    ON payment_orders(tx_hash) WHERE tx_hash IS NOT NULL;
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id TEXT PRIMARY KEY,
                    merchant_withdraw_id TEXT NOT NULL UNIQUE,
                    merchant TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    network TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    callback_url TEXT,
                    metadata_json TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS withdrawal_tx_hash_unique_idx
                    ON withdrawals(tx_hash) WHERE tx_hash IS NOT NULL;
                CREATE TABLE IF NOT EXISTS withdrawal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    withdrawal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(withdrawal_id) REFERENCES withdrawals(id)
                );
                CREATE TABLE IF NOT EXISTS ledger_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    journal_id TEXT NOT NULL,
                    reference_type TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    account TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    debit TEXT NOT NULL DEFAULT '0',
                    credit TEXT NOT NULL DEFAULT '0',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_reference_idx
                    ON ledger_lines(reference_type, reference_id);
                CREATE TABLE IF NOT EXISTS callback_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    callback_url TEXT,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_webhook_receipts (
                    id TEXT PRIMARY KEY,
                    event_id TEXT,
                    event_type TEXT NOT NULL,
                    reference_id TEXT,
                    payload TEXT NOT NULL,
                    signature_valid INTEGER NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    outcome TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_created_idx ON audit_logs(created_at DESC);
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_tasks (
                    id TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    network TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    destination_address TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    triggered_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS collection_tx_hash_unique_idx
                    ON collection_tasks(tx_hash) WHERE tx_hash IS NOT NULL;
                CREATE TABLE IF NOT EXISTS collection_items (
                    task_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL UNIQUE,
                    amount TEXT NOT NULL,
                    PRIMARY KEY(task_id, payment_id),
                    FOREIGN KEY(task_id) REFERENCES collection_tasks(id),
                    FOREIGN KEY(payment_id) REFERENCES payment_orders(id)
                );
                CREATE TABLE IF NOT EXISTS address_book_entries (
                    id TEXT PRIMARY KEY,
                    list_type TEXT NOT NULL CHECK(list_type IN ('ALLOWLIST','BLOCKLIST')),
                    asset TEXT NOT NULL,
                    network TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    address TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(list_type, network, address)
                );
                CREATE INDEX IF NOT EXISTS address_book_lookup_idx
                    ON address_book_entries(network, asset, address, list_type);
                CREATE TABLE IF NOT EXISTS ip_allowlist_entries (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cidr TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    scopes TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED')),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS api_keys_status_idx ON api_keys(status, created_at DESC);
                """
            )
            payment_columns = {row["name"] for row in db.execute("PRAGMA table_info(payment_orders)")}
            if "paid_amount" not in payment_columns:
                db.execute("ALTER TABLE payment_orders ADD COLUMN paid_amount TEXT NOT NULL DEFAULT '0'")
            if "expires_at" not in payment_columns:
                db.execute("ALTER TABLE payment_orders ADD COLUMN expires_at TEXT")
            if "customer_id" not in payment_columns:
                db.execute("ALTER TABLE payment_orders ADD COLUMN customer_id TEXT")
            if "return_url" not in payment_columns:
                db.execute("ALTER TABLE payment_orders ADD COLUMN return_url TEXT")
            if "metadata_json" not in payment_columns:
                db.execute("ALTER TABLE payment_orders ADD COLUMN metadata_json TEXT")
            withdrawal_columns = {row["name"] for row in db.execute("PRAGMA table_info(withdrawals)")}
            if "metadata_json" not in withdrawal_columns:
                db.execute("ALTER TABLE withdrawals ADD COLUMN metadata_json TEXT")
            receipt_columns = {row["name"] for row in db.execute("PRAGMA table_info(demo_webhook_receipts)")}
            if "event_id" not in receipt_columns:
                db.execute("ALTER TABLE demo_webhook_receipts ADD COLUMN event_id TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS demo_webhook_event_unique_idx "
                "ON demo_webhook_receipts(event_id) WHERE event_id IS NOT NULL"
            )
            db.execute(
                """UPDATE payment_orders SET paid_amount=amount
                WHERE status='CONFIRMED' AND (paid_amount IS NULL OR paid_amount='0')"""
            )
            now = utc_now()
            db.executemany(
                "INSERT OR IGNORE INTO runtime_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)",
                [
                    ("payouts_enabled", os.environ.get("M2_PAYOUTS_ENABLED", "true").lower(), "environment", now),
                    ("max_withdrawal_amount", os.environ.get("M2_MAX_WITHDRAWAL_AMOUNT", "10000"), "environment", now),
                    ("collections_enabled", os.environ.get("M2_COLLECTIONS_ENABLED", "true").lower(), "environment", now),
                    ("collection_threshold_usdt", os.environ.get("M2_COLLECTION_THRESHOLD_USDT", "100"), "environment", now),
                    ("collection_threshold_usdc", os.environ.get("M2_COLLECTION_THRESHOLD_USDC", "100"), "environment", now),
                    ("collection_destination_tron", os.environ.get("M2_COLLECTION_DESTINATION_TRON", "TCGrXtAjSDfUV2xxNEgaui28odN6MdyHun"), "environment", now),
                    ("collection_destination_polygon", os.environ.get("M2_COLLECTION_DESTINATION_POLYGON", "0xcee5bda9569f39eb9657472c4f8d5290f58653ad"), "environment", now),
                    ("allowlist_enforced", os.environ.get("M2_ALLOWLIST_ENFORCED", "false").lower(), "environment", now),
                    ("daily_withdrawal_limit", os.environ.get("M2_DAILY_WITHDRAWAL_LIMIT", "50000"), "environment", now),
                    ("project_enabled", os.environ.get("M2_PROJECT_ENABLED", "true").lower(), "environment", now),
                    ("project_callback_url", os.environ.get("M2_PROJECT_CALLBACK_URL", ""), "environment", now),
                    ("min_callback_usdt", os.environ.get("M2_MIN_CALLBACK_USDT", "0"), "environment", now),
                    ("min_callback_usdc", os.environ.get("M2_MIN_CALLBACK_USDC", "0"), "environment", now),
                    ("withdrawal_verification_enabled", os.environ.get("M2_WITHDRAWAL_VERIFICATION_ENABLED", "false").lower(), "environment", now),
                    ("tron_fee_available", os.environ.get("M2_TRON_FEE_AVAILABLE", "250"), "environment", now),
                    ("tron_fee_minimum", os.environ.get("M2_TRON_FEE_MINIMUM", "50"), "environment", now),
                    ("tron_fee_per_transaction", os.environ.get("M2_TRON_FEE_PER_TRANSACTION", "15"), "environment", now),
                    ("polygon_fee_available", os.environ.get("M2_POLYGON_FEE_AVAILABLE", "8"), "environment", now),
                    ("polygon_fee_minimum", os.environ.get("M2_POLYGON_FEE_MINIMUM", "1"), "environment", now),
                    ("polygon_fee_per_transaction", os.environ.get("M2_POLYGON_FEE_PER_TRANSACTION", "0.02"), "environment", now),
                ],
            )
            environment_key = os.environ.get("M2_WALLET_API_KEY") or secrets.token_urlsafe(32)
            environment_hash = hashlib.sha256(environment_key.encode()).hexdigest()
            environment_prefix = environment_key[:8]
            all_scopes = ",".join(sorted(API_KEY_SCOPES))
            db.execute(
                """INSERT INTO api_keys
                (id,name,key_prefix,key_hash,scopes,status,created_by,created_at,updated_at)
                VALUES('API-ENVIRONMENT','Environment API Key',?,?,?,'ACTIVE','environment',?,?)
                ON CONFLICT(id) DO UPDATE SET
                    key_prefix=excluded.key_prefix,
                    key_hash=excluded.key_hash,
                    scopes=excluded.scopes,
                    status='ACTIVE',
                    updated_at=excluded.updated_at""",
                (environment_prefix, environment_hash, all_scopes, now, now),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{datetime.now().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"

    @staticmethod
    def _tx_hash(network: str, reference: str) -> str:
        digest = hashlib.sha256(f"{network}:{reference}:{secrets.token_hex(8)}".encode()).hexdigest()
        return digest if network == "TRON" else "0x" + digest

    @staticmethod
    def _validate_pair(network: str, asset: str) -> None:
        if (network, asset) not in SUPPORTED_PAIRS:
            raise ValueError("demo supports TRON/USDT and POLYGON/USDC")

    @staticmethod
    def _validate_address(network: str, address: str) -> None:
        if network == "TRON" and not validate_tron_address(address):
            raise ValueError("invalid TRON address")
        if network == "POLYGON" and not validate_evm_address(address):
            raise ValueError("invalid EVM address")

    @staticmethod
    def _public_api_key(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        record = dict(row)
        record.pop("key_hash", None)
        record["scopes"] = [scope for scope in str(record.get("scopes", "")).split(",") if scope]
        return record

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                self._public_api_key(row)
                for row in db.execute("SELECT * FROM api_keys ORDER BY created_at DESC, rowid DESC")
            ]

    def create_api_key(self, payload: dict[str, Any], created_by: str) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("API key name is required and must be at most 80 characters")
        supplied_scopes = payload.get("scopes")
        if not isinstance(supplied_scopes, list) or not supplied_scopes:
            raise ValueError("select at least one API key scope")
        scopes = {str(scope).strip() for scope in supplied_scopes}
        invalid = scopes - API_KEY_SCOPES
        if invalid:
            raise ValueError(f"unsupported API key scope: {', '.join(sorted(invalid))}")
        key_id, now = self._id("API"), utc_now()
        secret = "m2_live_" + secrets.token_urlsafe(24)
        key_hash = hashlib.sha256(secret.encode()).hexdigest()
        prefix = secret[:12]
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO api_keys
                (id,name,key_prefix,key_hash,scopes,status,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,'ACTIVE',?,?,?)""",
                (key_id, name, prefix, key_hash, ",".join(sorted(scopes)), created_by, now, now),
            )
            row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return {**self._public_api_key(row), "secret": secret}

    def rotate_api_key(self, key_id: str) -> dict[str, Any]:
        if key_id == "API-ENVIRONMENT":
            raise ValueError("the environment API key must be changed through M2_WALLET_API_KEY")
        secret, now = "m2_live_" + secrets.token_urlsafe(24), utc_now()
        key_hash = hashlib.sha256(secret.encode()).hexdigest()
        with self._lock, self.connect() as db:
            current = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
            if not current:
                raise KeyError("API key not found")
            db.execute(
                "UPDATE api_keys SET key_prefix=?,key_hash=?,status='ACTIVE',updated_at=? WHERE id=?",
                (secret[:12], key_hash, now, key_id),
            )
            row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return {**self._public_api_key(row), "secret": secret}

    def set_api_key_status(self, key_id: str, enabled: bool) -> dict[str, Any]:
        if key_id == "API-ENVIRONMENT":
            raise ValueError("the environment API key cannot be disabled in the console")
        now = utc_now()
        with self._lock, self.connect() as db:
            current = db.execute("SELECT id FROM api_keys WHERE id=?", (key_id,)).fetchone()
            if not current:
                raise KeyError("API key not found")
            db.execute(
                "UPDATE api_keys SET status=?,updated_at=? WHERE id=?",
                ("ACTIVE" if enabled else "DISABLED", now, key_id),
            )
            row = db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return self._public_api_key(row)

    def resolve_api_key(self, secret: str) -> dict[str, Any] | None:
        if not secret:
            return None
        supplied_hash = hashlib.sha256(secret.encode()).hexdigest()
        with self._lock, self.connect() as db:
            rows = db.execute("SELECT * FROM api_keys WHERE status='ACTIVE'").fetchall()
            match = next((row for row in rows if hmac.compare_digest(row["key_hash"], supplied_hash)), None)
            if not match:
                return None
            used_at = utc_now()
            db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (used_at, match["id"]))
            record = dict(match)
            record["last_used_at"] = used_at
        return self._public_api_key(record)

    def _event(
        self,
        db: sqlite3.Connection,
        event_type: str,
        reference_id: str,
        callback_url: str | None,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        settings = {row["key"]: row["value"] for row in db.execute(
            "SELECT key,value FROM runtime_settings WHERE key IN ('project_callback_url','min_callback_usdt','min_callback_usdc')"
        )}
        callback_url = callback_url or settings.get("project_callback_url") or None
        status, last_error = "PENDING", None
        if event_type == "payment.confirmed" and payload.get("amount") is not None:
            asset = str(payload.get("asset", "")).lower()
            minimum = Decimal(settings.get(f"min_callback_{asset}", "0"))
            if Decimal(str(payload["amount"])) < minimum:
                status, last_error = "SKIPPED", "below minimum callback amount"
        event_id = self._id("EVT")
        event_payload = dict(payload)
        event_payload.setdefault("event_id", event_id)
        event_payload.setdefault("event_type", event_type)
        event_payload.setdefault("occurred_at", now)
        db.execute(
            """INSERT INTO callback_events
            (id, event_type, reference_id, callback_url, payload, status, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, reference_id, callback_url, __import__("json").dumps(event_payload), status, last_error, now, now),
        )

    def list_payments(self) -> list[dict[str, Any]]:
        self.expire_due_payments()
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM payment_orders ORDER BY created_at DESC")]

    def get_payment(self, payment_id: str) -> dict[str, Any] | None:
        self.expire_due_payments()
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM payment_orders WHERE id=?", (payment_id,)).fetchone())

    def get_payment_by_reference(self, reference: str) -> dict[str, Any] | None:
        self.expire_due_payments()
        with self.connect() as db:
            return self._row(
                db.execute(
                    "SELECT * FROM payment_orders WHERE id=? OR merchant_order_id=? LIMIT 1",
                    (reference, reference),
                ).fetchone()
            )

    def create_payment(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not self.project_settings()["enabled"]:
            raise ValueError("project is disabled")
        required = ("merchant_order_id", "amount", "order_currency", "pay_currency", "network")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        network = str(payload["network"]).upper()
        asset = str(payload["pay_currency"]).upper()
        self._validate_pair(network, asset)
        amount = parse_amount(payload["amount"])
        fee_rate_bps = int(payload.get("fee_rate_bps", 50))
        if not 0 <= fee_rate_bps <= 1000:
            raise ValueError("fee_rate_bps must be between 0 and 1000")
        fee = (amount * Decimal(fee_rate_bps) / Decimal(10000)).quantize(MONEY_QUANTUM)
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata_json = __import__("json").dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        if len(metadata_json) > 4000:
            raise ValueError("metadata is too large")
        return_url = str(payload.get("return_url") or "").strip()
        if return_url and not return_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("return_url must use HTTPS (localhost HTTP is allowed for demo)")
        now, payment_id = utc_now(), self._id("PAY")
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=int(payload.get("expires_in_minutes", 30)))).isoformat(timespec="seconds")
        with self._lock, self.connect() as db:
            existing = db.execute(
                "SELECT * FROM payment_orders WHERE merchant_order_id=?", (payload["merchant_order_id"],)
            ).fetchone()
            if existing:
                expected = {
                    "amount": money(amount),
                    "order_currency": str(payload["order_currency"]).upper(),
                    "pay_currency": asset,
                    "network": network,
                }
                conflicts = [field for field, value in expected.items() if str(existing[field]) != value]
                if conflicts:
                    raise ValueError(
                        "idempotency conflict for merchant_order_id; mismatched fields: "
                        + ", ".join(conflicts)
                    )
                return dict(existing), False
            db.execute(
                """INSERT INTO payment_orders
                (id, merchant_order_id, merchant, customer_id, amount, order_currency, pay_currency, network,
                 fee_rate_bps, fee_amount, paid_amount, pay_address, status, callback_url, return_url,
                 metadata_json, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', ?, 'PENDING', ?, ?, ?, ?, ?, ?)""",
                (
                    payment_id,
                    str(payload["merchant_order_id"]),
                    str(payload.get("merchant", "Internal Merchant")),
                    str(payload.get("customer_id") or "")[:120] or None,
                    money(amount),
                    str(payload["order_currency"]).upper(),
                    asset,
                    network,
                    fee_rate_bps,
                    money(fee),
                    self.address_provider.create_address(network, asset, payment_id),
                    payload.get("callback_url"),
                    return_url or None,
                    metadata_json,
                    expires_at,
                    now,
                    now,
                ),
            )
        return self.get_payment(payment_id) or {}, True

    def confirm_payment(self, payment_id: str, tx_hash: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT amount, paid_amount FROM payment_orders WHERE id=?", (payment_id,)).fetchone()
            if not row:
                raise KeyError("payment order not found")
        remaining = Decimal(row["amount"]) - Decimal(row["paid_amount"] or "0")
        if remaining <= 0:
            return self.get_payment(payment_id) or {}
        return self.simulate_payment(payment_id, remaining, tx_hash=tx_hash)

    def simulate_payment(self, payment_id: str, paid_amount: Any, tx_hash: str | None = None) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM payment_orders WHERE id=?", (payment_id,)).fetchone()
            if not row:
                raise KeyError("payment order not found")
            if row["status"] in {"CONFIRMED", "OVERPAID"}:
                return dict(row)
            if row["status"] not in {"PENDING", "PARTIAL"}:
                raise ValueError("payment cannot be paid from current status")
            now = utc_now()
            received = parse_amount(paid_amount)
            total = Decimal(row["paid_amount"] or "0") + received
            expected = Decimal(row["amount"])
            lower, upper = expected * Decimal("0.99"), expected * Decimal("1.01")
            if total < lower:
                status = "PARTIAL"
            elif total <= upper:
                status = "CONFIRMED"
            else:
                status = "OVERPAID"
            tx_hash = tx_hash or self._tx_hash(row["network"], payment_id)
            duplicate = db.execute(
                "SELECT id FROM payment_orders WHERE tx_hash=? AND id<>?", (tx_hash, payment_id)
            ).fetchone()
            if duplicate:
                raise ValueError("transaction hash is already assigned to another payment")
            fee = (total * Decimal(row["fee_rate_bps"]) / Decimal(10000)).quantize(MONEY_QUANTUM)
            db.execute(
                "UPDATE payment_orders SET status=?,paid_amount=?,fee_amount=?,tx_hash=?,updated_at=? WHERE id=?",
                (status, money(total), money(fee), tx_hash, now, payment_id),
            )
            if status in {"CONFIRMED", "OVERPAID"}:
                journal, net = self._id("JRN"), total - fee
                db.executemany(
                    """INSERT INTO ledger_lines
                    (journal_id,reference_type,reference_id,account,asset,debit,credit,created_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    [
                        (journal, "PAYMENT", payment_id, "CHAIN_CLEARING", row["pay_currency"], money(total), "0", now),
                        (journal, "PAYMENT", payment_id, "MERCHANT_PAYABLE", row["pay_currency"], "0", money(net), now),
                        (journal, "PAYMENT", payment_id, "PLATFORM_FEE_REVENUE", row["pay_currency"], "0", money(fee), now),
                    ],
                )
            self._event(db, f"payment.{status.lower()}", payment_id, row["callback_url"], {
                "id": payment_id, "merchant_order_id": row["merchant_order_id"], "customer_id": row["customer_id"],
                "status": status, "tx_hash": tx_hash, "paid_amount": money(total), "expected_amount": row["amount"],
                "amount": money(total), "asset": row["pay_currency"], "network": row["network"]
            }, now)
        return self.get_payment(payment_id) or {}

    def expire_payment(self, payment_id: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM payment_orders WHERE id=?", (payment_id,)).fetchone()
            if not row:
                raise KeyError("payment order not found")
            if row["status"] == "EXPIRED":
                return dict(row)
            if row["status"] not in {"PENDING", "PARTIAL"}:
                raise ValueError("only pending payments can expire")
            now = utc_now()
            db.execute("UPDATE payment_orders SET status='EXPIRED',updated_at=? WHERE id=?", (now, payment_id))
            self._event(db, "payment.expired", payment_id, row["callback_url"], {"id": payment_id, "merchant_order_id": row["merchant_order_id"], "customer_id": row["customer_id"], "status": "EXPIRED", "amount": row["paid_amount"], "expected_amount": row["amount"], "asset": row["pay_currency"], "network": row["network"]}, now)
        return self.get_payment(payment_id) or {}

    def expire_due_payments(self) -> int:
        now = utc_now()
        with self._lock, self.connect() as db:
            rows = db.execute(
                """SELECT * FROM payment_orders WHERE status IN ('PENDING','PARTIAL')
                AND expires_at IS NOT NULL AND expires_at<=?""",
                (now,),
            ).fetchall()
            for row in rows:
                db.execute("UPDATE payment_orders SET status='EXPIRED',updated_at=? WHERE id=?", (now, row["id"]))
                self._event(db, "payment.expired", row["id"], row["callback_url"], {"id": row["id"], "merchant_order_id": row["merchant_order_id"], "customer_id": row["customer_id"], "status": "EXPIRED", "amount": row["paid_amount"], "expected_amount": row["amount"], "asset": row["pay_currency"], "network": row["network"]}, now)
            return len(rows)

    def match_inbound_transfer(
        self, network: str, asset: str, to_address: str, amount: Any, tx_hash: str
    ) -> dict[str, Any] | None:
        """Match one confirmed chain transfer to an exact pending payment order."""
        network, asset = network.upper(), asset.upper()
        self._validate_pair(network, asset)
        normalized_amount = money(parse_amount(amount))
        with self.connect() as db:
            row = db.execute(
                """SELECT id FROM payment_orders
                WHERE network=? AND pay_currency=? AND pay_address=? AND amount=? AND status='PENDING'
                ORDER BY created_at ASC LIMIT 1""",
                (network, asset, to_address, normalized_amount),
            ).fetchone()
            if not row:
                return None
        return self.confirm_payment(row["id"], tx_hash=tx_hash)

    def list_withdrawals(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM withdrawals ORDER BY created_at DESC")]

    def get_withdrawal(self, withdrawal_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone())

    def get_withdrawal_by_reference(self, reference: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(
                db.execute(
                    "SELECT * FROM withdrawals WHERE id=? OR merchant_withdraw_id=? LIMIT 1",
                    (reference, reference),
                ).fetchone()
            )

    def create_withdrawal(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not self.project_settings()["enabled"]:
            raise ValueError("project is disabled")
        required = ("merchant_withdraw_id", "amount", "currency", "network", "to_address")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        network, asset = str(payload["network"]).upper(), str(payload["currency"]).upper()
        address = str(payload["to_address"])
        self._validate_pair(network, asset)
        self._validate_address(network, address)
        self._validate_address_policy(network, asset, address)
        amount, now, withdrawal_id = parse_amount(payload["amount"]), utc_now(), self._id("WD")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata_json = __import__("json").dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        if len(metadata_json) > 4000:
            raise ValueError("metadata is too large")
        with self._lock, self.connect() as db:
            existing = db.execute(
                "SELECT * FROM withdrawals WHERE merchant_withdraw_id=?", (payload["merchant_withdraw_id"],)
            ).fetchone()
            if existing:
                expected = {
                    "amount": money(amount),
                    "currency": asset,
                    "network": network,
                    "to_address": address,
                }
                conflicts = [field for field, value in expected.items() if str(existing[field]) != value]
                if conflicts:
                    raise ValueError(
                        "idempotency conflict for merchant_withdraw_id; mismatched fields: "
                        + ", ".join(conflicts)
                    )
                return dict(existing), False
            db.execute(
                """INSERT INTO withdrawals
                (id,merchant_withdraw_id,merchant,user_id,amount,currency,network,to_address,status,
                 callback_url,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,'PENDING_APPROVAL',?,?,?,?)""",
                (
                    withdrawal_id,
                    str(payload["merchant_withdraw_id"]),
                    str(payload.get("merchant", "Internal Merchant")),
                    str(payload.get("user_id", "unknown")),
                    money(amount),
                    asset,
                    network,
                    address,
                    payload.get("callback_url"),
                    metadata_json,
                    now,
                    now,
                ),
            )
            self._withdrawal_event(db, withdrawal_id, "PENDING_APPROVAL", "withdrawal created", now)
        return self.get_withdrawal(withdrawal_id) or {}, True

    def list_address_book(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM address_book_entries ORDER BY list_type, created_at DESC, rowid DESC"
                )
            ]

    def create_address_book_entry(self, payload: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
        required = ("list_type", "asset", "network", "address")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        list_type = str(payload["list_type"]).upper()
        if list_type not in {"ALLOWLIST", "BLOCKLIST"}:
            raise ValueError("list_type must be ALLOWLIST or BLOCKLIST")
        network, asset = str(payload["network"]).upper(), str(payload["asset"]).upper()
        address = str(payload["address"]).strip()
        self._validate_pair(network, asset)
        self._validate_address(network, address)
        now, entry_id = utc_now(), self._id("ADDR")
        with self._lock, self.connect() as db:
            existing = db.execute(
                "SELECT * FROM address_book_entries WHERE list_type=? AND network=? AND address=?",
                (list_type, network, address),
            ).fetchone()
            if existing:
                return dict(existing), False
            db.execute(
                """INSERT INTO address_book_entries
                (id,list_type,asset,network,alias,address,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (entry_id, list_type, asset, network, str(payload.get("alias") or "Unnamed address")[:80], address, actor, now),
            )
        with self.connect() as db:
            row = db.execute("SELECT * FROM address_book_entries WHERE id=?", (entry_id,)).fetchone()
            return dict(row), True

    def _validate_address_policy(self, network: str, asset: str, address: str) -> None:
        with self.connect() as db:
            blocked = db.execute(
                """SELECT 1 FROM address_book_entries
                WHERE list_type='BLOCKLIST' AND network=? AND address=?""",
                (network, address),
            ).fetchone()
            if blocked:
                raise ValueError("recipient address is blocked")
            setting = db.execute(
                "SELECT value FROM runtime_settings WHERE key='allowlist_enforced'"
            ).fetchone()
            enforced = bool(setting and setting["value"] in {"1", "true", "yes", "on"})
            if enforced:
                allowed = db.execute(
                    """SELECT 1 FROM address_book_entries
                    WHERE list_type='ALLOWLIST' AND network=? AND asset=? AND address=?""",
                    (network, asset, address),
                ).fetchone()
                if not allowed:
                    raise ValueError("recipient address is not on the allowlist")

    @staticmethod
    def _withdrawal_event(
        db: sqlite3.Connection, withdrawal_id: str, status: str, detail: str | None, now: str
    ) -> None:
        db.execute(
            "INSERT INTO withdrawal_events(withdrawal_id,status,detail,created_at) VALUES(?,?,?,?)",
            (withdrawal_id, status, detail, now),
        )

    def approve_withdrawal(self, withdrawal_id: str, reviewed_by: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] in ("APPROVED", "SIGNING", "BROADCASTED", "CONFIRMED"):
                return dict(row)
            if row["status"] != "PENDING_APPROVAL":
                raise ValueError("withdrawal cannot be approved from current status")
            now = utc_now()
            db.execute(
                """UPDATE withdrawals SET status='APPROVED',reviewed_by=?,reviewed_at=?,
                updated_at=? WHERE id=?""",
                (reviewed_by, now, now, withdrawal_id),
            )
            self._withdrawal_event(db, withdrawal_id, "APPROVED", f"approved by {reviewed_by}", now)
            self._event(db, "withdrawal.approved", withdrawal_id, row["callback_url"], {"id": withdrawal_id, "merchant_withdraw_id": row["merchant_withdraw_id"], "customer_id": row["user_id"], "status": "APPROVED", "amount": row["amount"], "asset": row["currency"], "network": row["network"]}, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def record_withdrawal_validation(self, withdrawal_id: str, approved: bool, detail: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            now = utc_now()
            outcome = "APPROVED" if approved else "REJECTED"
            self._withdrawal_event(db, withdrawal_id, "EXTERNAL_VALIDATION", f"{outcome}: {detail[:400]}", now)
        return self.get_withdrawal(withdrawal_id) or {}

    def mark_withdrawal_signing(self, withdrawal_id: str, signature_ref: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] == "SIGNING":
                return dict(row)
            if row["status"] != "APPROVED":
                raise ValueError("withdrawal cannot enter signing from current status")
            now = utc_now()
            db.execute("UPDATE withdrawals SET status='SIGNING',updated_at=? WHERE id=?", (now, withdrawal_id))
            self._withdrawal_event(db, withdrawal_id, "SIGNING", signature_ref, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def mark_withdrawal_broadcasted(self, withdrawal_id: str, tx_hash: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] in ("BROADCASTED", "CONFIRMED"):
                if row["tx_hash"] != tx_hash:
                    raise ValueError("withdrawal already has a different transaction hash")
                return dict(row)
            if row["status"] != "SIGNING":
                raise ValueError("withdrawal cannot be broadcast from current status")
            duplicate = db.execute("SELECT id FROM withdrawals WHERE tx_hash=? AND id<>?", (tx_hash, withdrawal_id)).fetchone()
            if duplicate:
                raise ValueError("transaction hash is already assigned to another withdrawal")
            now = utc_now()
            db.execute(
                "UPDATE withdrawals SET status='BROADCASTED',tx_hash=?,updated_at=? WHERE id=?",
                (tx_hash, now, withdrawal_id),
            )
            self._withdrawal_event(db, withdrawal_id, "BROADCASTED", tx_hash, now)
            self._event(db, "withdrawal.broadcasted", withdrawal_id, row["callback_url"], {"id": withdrawal_id, "merchant_withdraw_id": row["merchant_withdraw_id"], "customer_id": row["user_id"], "status": "BROADCASTED", "tx_hash": tx_hash, "amount": row["amount"], "asset": row["currency"], "network": row["network"]}, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def confirm_withdrawal(self, withdrawal_id: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] == "CONFIRMED":
                return dict(row)
            if row["status"] != "BROADCASTED":
                raise ValueError("withdrawal cannot be confirmed from current status")
            now, journal = utc_now(), self._id("JRN")
            db.execute("UPDATE withdrawals SET status='CONFIRMED',updated_at=? WHERE id=?", (now, withdrawal_id))
            db.executemany(
                """INSERT INTO ledger_lines
                (journal_id,reference_type,reference_id,account,asset,debit,credit,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (journal, "WITHDRAWAL", withdrawal_id, "MERCHANT_PAYABLE", row["currency"], row["amount"], "0", now),
                    (journal, "WITHDRAWAL", withdrawal_id, "HOT_WALLET", row["currency"], "0", row["amount"], now),
                ],
            )
            self._withdrawal_event(db, withdrawal_id, "CONFIRMED", row["tx_hash"], now)
            self._event(db, "withdrawal.confirmed", withdrawal_id, row["callback_url"], {"id": withdrawal_id, "merchant_withdraw_id": row["merchant_withdraw_id"], "customer_id": row["user_id"], "status": "CONFIRMED", "tx_hash": row["tx_hash"], "amount": row["amount"], "asset": row["currency"], "network": row["network"]}, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def fail_withdrawal(self, withdrawal_id: str, reason: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] not in ("APPROVED", "SIGNING", "BROADCASTED"):
                raise ValueError("withdrawal cannot fail from current status")
            now = utc_now()
            db.execute("UPDATE withdrawals SET status='FAILED',updated_at=? WHERE id=?", (now, withdrawal_id))
            self._withdrawal_event(db, withdrawal_id, "FAILED", reason[:500], now)
            self._event(db, "withdrawal.failed", withdrawal_id, row["callback_url"], {"id": withdrawal_id, "merchant_withdraw_id": row["merchant_withdraw_id"], "customer_id": row["user_id"], "status": "FAILED", "reason": reason[:200], "amount": row["amount"], "asset": row["currency"], "network": row["network"]}, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def reject_withdrawal(self, withdrawal_id: str, reviewed_by: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
            if not row:
                raise KeyError("withdrawal not found")
            if row["status"] != "PENDING_APPROVAL":
                raise ValueError("withdrawal cannot be rejected from current status")
            now = utc_now()
            db.execute(
                """UPDATE withdrawals SET status='REJECTED',reviewed_by=?,reviewed_at=?,updated_at=?
                WHERE id=?""",
                (reviewed_by, now, now, withdrawal_id),
            )
            self._withdrawal_event(db, withdrawal_id, "REJECTED", f"rejected by {reviewed_by}", now)
            self._event(db, "withdrawal.rejected", withdrawal_id, row["callback_url"], {"id": withdrawal_id, "merchant_withdraw_id": row["merchant_withdraw_id"], "customer_id": row["user_id"], "status": "REJECTED", "amount": row["amount"], "asset": row["currency"], "network": row["network"]}, now)
        return self.get_withdrawal(withdrawal_id) or {}

    def ledger(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM ledger_lines ORDER BY id DESC")]

    def callbacks(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM callback_events ORDER BY created_at DESC, rowid DESC")]

    def record_demo_webhook(
        self,
        event_type: str,
        payload: dict[str, Any],
        signature_valid: bool,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        receipt_id, now = self._id("HOOK"), utc_now()
        event_id = str(event_id or payload.get("event_id") or "").strip()[:120] or None
        reference_id = str(payload.get("id") or payload.get("reference_id") or "")[:120] or None
        encoded = __import__("json").dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self.connect() as db:
            if event_id:
                existing = db.execute(
                    "SELECT * FROM demo_webhook_receipts WHERE event_id=?", (event_id,)
                ).fetchone()
                if existing:
                    return dict(existing)
            db.execute(
                """INSERT INTO demo_webhook_receipts
                (id,event_id,event_type,reference_id,payload,signature_valid,received_at)
                VALUES(?,?,?,?,?,?,?)""",
                (receipt_id, event_id, event_type[:120], reference_id, encoded, 1 if signature_valid else 0, now),
            )
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM demo_webhook_receipts WHERE id=?", (receipt_id,)).fetchone())

    def demo_webhook_receipts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM demo_webhook_receipts ORDER BY received_at DESC, rowid DESC LIMIT ?",
                    (min(max(int(limit), 1), 500),),
                )
            ]

    def demo_readiness(self) -> dict[str, Any]:
        payments = self.list_payments()
        withdrawals = self.list_withdrawals()
        callbacks = self.callbacks()
        collections = self.list_collections()
        events = self.withdrawal_events()
        receipts = self.demo_webhook_receipts()
        address_book = self.list_address_book()
        reconciliation = self.reconciliation_report()
        confirmed_assets = {item["pay_currency"] for item in payments if item["status"] in {"CONFIRMED", "OVERPAID"}}
        exception_states = {item["status"] for item in payments}
        collected_assets = {item["asset"] for item in collections if item["status"] == "CONFIRMED"}
        payout_stages: dict[str, set[str]] = {}
        for event in events:
            payout_stages.setdefault(event["withdrawal_id"], set()).add(event["status"])
        full_payout = any(
            {"PENDING_APPROVAL", "APPROVED", "SIGNING", "BROADCASTED", "CONFIRMED"}.issubset(stages)
            for stages in payout_stages.values()
        )
        validation_passed = any(
            event["status"] == "EXTERNAL_VALIDATION" and str(event.get("detail", "")).startswith("APPROVED:")
            for event in events
        )
        list_types = {item["list_type"] for item in address_book}
        risk = self.risk_policy()
        network_reserves = self.network_reserves()
        healthy_networks = {item["network"] for item in network_reserves if item["healthy"]}
        checks = [
            {"id": "stablecoin_payments", "label": "USDT and USDC payment flows", "passed": {"USDT", "USDC"}.issubset(confirmed_assets), "evidence": ", ".join(sorted(confirmed_assets)) or "No confirmed assets"},
            {"id": "payment_exceptions", "label": "Underpaid, overpaid, and expired states", "passed": {"PARTIAL", "OVERPAID", "EXPIRED"}.issubset(exception_states), "evidence": ", ".join(sorted({"PARTIAL", "OVERPAID", "EXPIRED"}.intersection(exception_states))) or "No exception scenarios"},
            {"id": "collections", "label": "USDT and USDC wallet sweeping", "passed": {"USDT", "USDC"}.issubset(collected_assets), "evidence": ", ".join(sorted(collected_assets)) or "No completed sweeps"},
            {"id": "finance_payout", "label": "Finance approval, signing, and broadcast", "passed": full_payout, "evidence": f"{sum(1 for item in withdrawals if item['status'] == 'CONFIRMED')} confirmed withdrawal(s)"},
            {"id": "external_validation", "label": "Merchant pre-payout validation", "passed": validation_passed, "evidence": "Approved validation recorded" if validation_passed else "No approved validation"},
            {"id": "signed_callbacks", "label": "Signed callbacks and merchant receipts", "passed": any(item["status"] == "DELIVERED" for item in callbacks) and bool(receipts), "evidence": f"{len(receipts)} verified request(s)"},
            {"id": "risk_controls", "label": "Limits, allowlist, and blocklist", "passed": {"ALLOWLIST", "BLOCKLIST"}.issubset(list_types) and Decimal(risk["daily_withdrawal_limit"]) > 0, "evidence": f"Daily {risk['daily_withdrawn_amount']} / {risk['daily_withdrawal_limit']}"},
            {"id": "network_fee_reserves", "label": "TRON and Polygon network fee reserves", "passed": {"TRON", "POLYGON"}.issubset(healthy_networks), "evidence": ", ".join(f"{item['network']} {item['available']} {item['native_asset']} ({item['projected_transactions']} tx)" for item in network_reserves)},
            {"id": "reconciliation", "label": "Balanced ledger and operational reconciliation", "passed": bool(reconciliation["ok"]), "evidence": "Balanced" if reconciliation["ok"] else "Reconciliation exceptions found"},
        ]
        passed = sum(1 for item in checks if item["passed"])
        return {
            "ready": passed == len(checks),
            "passed": passed,
            "total": len(checks),
            "score": round(passed * 100 / len(checks)),
            "mode": os.environ.get("M2_BROADCAST_MODE", "simulation").lower(),
            "generated_at": utc_now(),
            "checks": checks,
        }

    def record_audit(
        self,
        actor: str,
        role: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str = "SUCCESS",
        detail: str | None = None,
    ) -> dict[str, Any]:
        audit_id, now = self._id("AUD"), utc_now()
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO audit_logs
                (id,actor,role,action,resource_type,resource_id,outcome,detail,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (audit_id, actor, role, action, resource_type, resource_id, outcome, (detail or "")[:500], now),
            )
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM audit_logs WHERE id=?", (audit_id,)).fetchone())

    def audit_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit_logs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (min(max(int(limit), 1), 500),),
                )
            ]

    def project_settings(self) -> dict[str, Any]:
        with self.connect() as db:
            values = {row["key"]: row["value"] for row in db.execute(
                """SELECT key,value FROM runtime_settings WHERE key IN
                ('project_enabled','project_callback_url','min_callback_usdt','min_callback_usdc','withdrawal_verification_enabled')"""
            )}
        return {
            "enabled": values.get("project_enabled", "true") in {"1", "true", "yes", "on"},
            "callback_url": values.get("project_callback_url", ""),
            "minimum_callbacks": {
                "USDT": values.get("min_callback_usdt", "0"),
                "USDC": values.get("min_callback_usdc", "0"),
            },
            "withdrawal_verification_enabled": values.get("withdrawal_verification_enabled", "false") in {"1", "true", "yes", "on"},
        }

    def update_project_settings(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = {"enabled", "callback_url", "minimum_usdt", "minimum_usdc", "withdrawal_verification_enabled"}
        if not payload or not set(payload).issubset(allowed):
            raise ValueError("project settings contain unsupported fields")
        updates: list[tuple[str, str]] = []
        for field, key in (("enabled", "project_enabled"), ("withdrawal_verification_enabled", "withdrawal_verification_enabled")):
            if field in payload:
                if not isinstance(payload[field], bool):
                    raise ValueError(f"{field} must be boolean")
                updates.append((key, "true" if payload[field] else "false"))
        if "callback_url" in payload:
            callback_url = str(payload["callback_url"]).strip()
            if callback_url and not (callback_url.startswith("https://") or callback_url.startswith("http://127.0.0.1") or callback_url.startswith("http://localhost")):
                raise ValueError("callback_url must use HTTPS (localhost HTTP is allowed for demo)")
            updates.append(("project_callback_url", callback_url))
        for field, key in (("minimum_usdt", "min_callback_usdt"), ("minimum_usdc", "min_callback_usdc")):
            if field in payload:
                updates.append((key, money(parse_amount(payload[field]))))
        now = utc_now()
        with self._lock, self.connect() as db:
            db.executemany(
                """INSERT INTO runtime_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                [(key, value, actor, now) for key, value in updates],
            )
        return self.project_settings()

    def list_ip_allowlist(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM ip_allowlist_entries ORDER BY created_at DESC, rowid DESC")]

    def create_ip_allowlist_entry(self, payload: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
        cidr = str(payload.get("cidr", "")).strip()
        if not cidr:
            raise ValueError("cidr is required")
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as error:
            raise ValueError("invalid IP address or CIDR") from error
        normalized, now, entry_id = str(network), utc_now(), self._id("IP")
        with self._lock, self.connect() as db:
            existing = db.execute("SELECT * FROM ip_allowlist_entries WHERE cidr=?", (normalized,)).fetchone()
            if existing:
                return dict(existing), False
            db.execute(
                "INSERT INTO ip_allowlist_entries(id,name,cidr,created_by,created_at) VALUES(?,?,?,?,?)",
                (entry_id, str(payload.get("name") or "API access")[:80], normalized, actor, now),
            )
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM ip_allowlist_entries WHERE id=?", (entry_id,)).fetchone()), True

    def is_ip_allowed(self, address: str) -> bool:
        rows = self.list_ip_allowlist()
        if not rows:
            return True
        try:
            candidate = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(candidate in ipaddress.ip_network(row["cidr"], strict=False) for row in rows)

    def risk_policy(self) -> dict[str, Any]:
        with self.connect() as db:
            values = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM runtime_settings")}
            today = datetime.now(timezone.utc).date().isoformat()
            daily_rows = db.execute(
                """SELECT amount FROM withdrawals
                WHERE status IN ('APPROVED','SIGNING','BROADCASTED','CONFIRMED')
                AND substr(reviewed_at,1,10)=?""",
                (today,),
            ).fetchall()
            daily_total = sum((Decimal(row["amount"]) for row in daily_rows), Decimal("0"))
        return {
            "payouts_enabled": values.get("payouts_enabled", "true") in {"1", "true", "yes", "on"},
            "max_withdrawal_amount": values.get("max_withdrawal_amount", "10000"),
            "daily_withdrawal_limit": values.get("daily_withdrawal_limit", "50000"),
            "daily_withdrawn_amount": money(daily_total.quantize(MONEY_QUANTUM)),
            "allowlist_enforced": values.get("allowlist_enforced", "false") in {"1", "true", "yes", "on"},
            "broadcast_mode": os.environ.get("M2_BROADCAST_MODE", "simulation").lower(),
        }

    def update_risk_policy(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = {"payouts_enabled", "max_withdrawal_amount", "daily_withdrawal_limit", "allowlist_enforced"}
        if not payload or not set(payload).issubset(allowed):
            raise ValueError("risk policy contains unsupported fields")
        updates: list[tuple[str, str]] = []
        for field in ("payouts_enabled", "allowlist_enforced"):
            if field in payload:
                value = payload[field]
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be boolean")
                updates.append((field, "true" if value else "false"))
        for field in ("max_withdrawal_amount", "daily_withdrawal_limit"):
            if field in payload:
                limit = parse_amount(payload[field])
                updates.append((field, money(limit)))
        now = utc_now()
        with self._lock, self.connect() as db:
            db.executemany(
                """INSERT INTO runtime_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                [(key, value, actor, now) for key, value in updates],
            )
        return self.risk_policy()

    def network_reserves(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = {
                row["key"]: row
                for row in db.execute(
                    "SELECT key,value,updated_by,updated_at FROM runtime_settings WHERE key LIKE '%_fee_%'"
                )
            }
        result = []
        for network, native_asset, label in (
            ("TRON", "TRX", "TRON energy and bandwidth reserve"),
            ("POLYGON", "POL", "Polygon gas reserve"),
        ):
            prefix = network.lower()
            available = Decimal(rows[f"{prefix}_fee_available"]["value"])
            minimum = Decimal(rows[f"{prefix}_fee_minimum"]["value"])
            per_transaction = Decimal(rows[f"{prefix}_fee_per_transaction"]["value"])
            spendable = max(available - minimum, Decimal("0"))
            projected = int(spendable / per_transaction) if per_transaction > 0 else 0
            updated = max(
                (
                    rows[f"{prefix}_fee_available"],
                    rows[f"{prefix}_fee_minimum"],
                    rows[f"{prefix}_fee_per_transaction"],
                ),
                key=lambda row: row["updated_at"],
            )
            result.append(
                {
                    "network": network,
                    "native_asset": native_asset,
                    "label": label,
                    "available": money(available.quantize(MONEY_QUANTUM)),
                    "minimum_required": money(minimum.quantize(MONEY_QUANTUM)),
                    "estimated_per_transaction": money(per_transaction.quantize(MONEY_QUANTUM)),
                    "projected_transactions": projected,
                    "healthy": projected >= 1,
                    "updated_by": updated["updated_by"],
                    "updated_at": updated["updated_at"],
                }
            )
        return result

    def update_network_reserve(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        network = str(payload.get("network", "")).upper()
        if network not in {"TRON", "POLYGON"}:
            raise ValueError("network must be TRON or POLYGON")
        allowed = {"network", "available", "minimum_required", "estimated_per_transaction"}
        if not set(payload).issubset(allowed) or not any(field in payload for field in allowed - {"network"}):
            raise ValueError("network reserve contains unsupported or missing fields")
        prefix = network.lower()
        field_map = {
            "available": f"{prefix}_fee_available",
            "minimum_required": f"{prefix}_fee_minimum",
            "estimated_per_transaction": f"{prefix}_fee_per_transaction",
        }
        updates = []
        for field, key in field_map.items():
            if field in payload:
                try:
                    value = Decimal(str(payload[field])).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValueError(f"{field} must be a decimal string") from exc
                if field == "available" and value < 0:
                    raise ValueError("available must be zero or greater")
                if field in {"minimum_required", "estimated_per_transaction"} and value <= 0:
                    raise ValueError(f"{field} must be greater than zero")
                updates.append((key, money(value)))
        now = utc_now()
        with self._lock, self.connect() as db:
            db.executemany(
                """INSERT INTO runtime_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                [(key, value, actor, now) for key, value in updates],
            )
        return next(item for item in self.network_reserves() if item["network"] == network)

    def collection_policy(self) -> dict[str, Any]:
        with self.connect() as db:
            values = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM runtime_settings")}
        return {
            "enabled": values.get("collections_enabled", "true") in {"1", "true", "yes", "on"},
            "thresholds": {
                "USDT": values.get("collection_threshold_usdt", "100"),
                "USDC": values.get("collection_threshold_usdc", "100"),
            },
            "destinations": {
                "TRON": values.get("collection_destination_tron", "TCGrXtAjSDfUV2xxNEgaui28odN6MdyHun"),
                "POLYGON": values.get("collection_destination_polygon", "0xcee5bda9569f39eb9657472c4f8d5290f58653ad"),
            },
            "mode": os.environ.get("M2_BROADCAST_MODE", "simulation").lower(),
        }

    def update_collection_policy(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = {"enabled", "threshold_usdt", "threshold_usdc", "destination_tron", "destination_polygon"}
        if not payload or not set(payload).issubset(allowed):
            raise ValueError("collection policy contains unsupported fields")
        updates: list[tuple[str, str]] = []
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise ValueError("enabled must be boolean")
            updates.append(("collections_enabled", "true" if payload["enabled"] else "false"))
        for field, key in (("threshold_usdt", "collection_threshold_usdt"), ("threshold_usdc", "collection_threshold_usdc")):
            if field in payload:
                updates.append((key, money(parse_amount(payload[field]))))
        if "destination_tron" in payload:
            address = str(payload["destination_tron"])
            self._validate_address("TRON", address)
            updates.append(("collection_destination_tron", address))
        if "destination_polygon" in payload:
            address = str(payload["destination_polygon"])
            self._validate_address("POLYGON", address)
            updates.append(("collection_destination_polygon", address))
        now = utc_now()
        with self._lock, self.connect() as db:
            db.executemany(
                """INSERT INTO runtime_settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                [(key, value, actor, now) for key, value in updates],
            )
        return self.collection_policy()

    def collection_candidates(self) -> list[dict[str, Any]]:
        policy, grouped = self.collection_policy(), {}
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.id,p.pay_currency,p.network,p.amount,p.pay_address
                FROM payment_orders p
                WHERE p.status='CONFIRMED' AND NOT EXISTS(
                    SELECT 1 FROM collection_items i WHERE i.payment_id=p.id
                ) ORDER BY p.created_at ASC"""
            ).fetchall()
        for row in rows:
            key = (row["network"], row["pay_currency"])
            item = grouped.setdefault(key, {"network": key[0], "asset": key[1], "amount": Decimal("0"), "source_count": 0})
            item["amount"] += Decimal(row["amount"])
            item["source_count"] += 1
        result = []
        for (network, asset), item in sorted(grouped.items()):
            threshold = Decimal(policy["thresholds"].get(asset, "0"))
            result.append(
                {
                    "network": network,
                    "asset": asset,
                    "amount": money(item["amount"]),
                    "source_count": item["source_count"],
                    "threshold": money(threshold),
                    "eligible": bool(policy["enabled"] and item["amount"] >= threshold),
                    "destination_address": policy["destinations"].get(network, ""),
                }
            )
        return result

    def list_collections(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM collection_tasks ORDER BY created_at DESC, rowid DESC")]

    def get_collection(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM collection_tasks WHERE id=?", (task_id,)).fetchone())

    def create_collection(self, network: str, asset: str, triggered_by: str) -> dict[str, Any]:
        network, asset = network.upper(), asset.upper()
        self._validate_pair(network, asset)
        policy = self.collection_policy()
        if not policy["enabled"]:
            raise ValueError("collections are disabled")
        destination = str(policy["destinations"].get(network, ""))
        self._validate_address(network, destination)
        threshold = Decimal(policy["thresholds"].get(asset, "0"))
        task_id, now = self._id("COL"), utc_now()
        with self._lock, self.connect() as db:
            rows = db.execute(
                """SELECT p.id,p.amount FROM payment_orders p
                WHERE p.status='CONFIRMED' AND p.network=? AND p.pay_currency=? AND NOT EXISTS(
                    SELECT 1 FROM collection_items i WHERE i.payment_id=p.id
                ) ORDER BY p.created_at ASC""",
                (network, asset),
            ).fetchall()
            total = sum((Decimal(row["amount"]) for row in rows), Decimal("0"))
            if not rows:
                raise ValueError("no collectable payment addresses")
            if total < threshold:
                raise ValueError(f"collectable balance is below threshold ({money(threshold)} {asset})")
            db.execute(
                """INSERT INTO collection_tasks
                (id,asset,network,source_count,destination_address,amount,status,triggered_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'PENDING',?,?,?)""",
                (task_id, asset, network, len(rows), destination, money(total), triggered_by, now, now),
            )
            db.executemany(
                "INSERT INTO collection_items(task_id,payment_id,amount) VALUES(?,?,?)",
                [(task_id, row["id"], row["amount"]) for row in rows],
            )
        return self.get_collection(task_id) or {}

    def confirm_collection(self, task_id: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM collection_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError("collection task not found")
            if row["status"] == "CONFIRMED":
                return dict(row)
            if row["status"] != "PENDING":
                raise ValueError("collection cannot be confirmed from current status")
            now, journal = utc_now(), self._id("JRN")
            tx_hash = self._tx_hash(row["network"], task_id)
            db.execute(
                "UPDATE collection_tasks SET status='CONFIRMED',tx_hash=?,updated_at=? WHERE id=?",
                (tx_hash, now, task_id),
            )
            db.executemany(
                """INSERT INTO ledger_lines
                (journal_id,reference_type,reference_id,account,asset,debit,credit,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (journal, "COLLECTION", task_id, "HOT_WALLET", row["asset"], row["amount"], "0", now),
                    (journal, "COLLECTION", task_id, "COLLECTION_ADDRESSES", row["asset"], "0", row["amount"], now),
                ],
            )
        return self.get_collection(task_id) or {}

    def run_collection(self, network: str, asset: str, triggered_by: str) -> dict[str, Any]:
        task = self.create_collection(network, asset, triggered_by)
        return self.confirm_collection(task["id"])

    def pending_callbacks(self, limit: int = 50, max_attempts: int = 5) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT * FROM callback_events
                    WHERE status IN ('PENDING','RETRY') AND attempts < ?
                    ORDER BY created_at ASC LIMIT ?""",
                    (max_attempts, min(max(limit, 1), 200)),
                )
            ]

    def mark_callback_success(self, event_id: str) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                "UPDATE callback_events SET status='DELIVERED',attempts=attempts+1,last_error=NULL,updated_at=? WHERE id=?",
                (utc_now(), event_id),
            )

    def mark_callback_failure(self, event_id: str, error: str, max_attempts: int = 5) -> None:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT attempts FROM callback_events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise KeyError("callback event not found")
            attempts = int(row["attempts"]) + 1
            status = "FAILED" if attempts >= max_attempts else "RETRY"
            db.execute(
                "UPDATE callback_events SET status=?,attempts=?,last_error=?,updated_at=? WHERE id=?",
                (status, attempts, error[:500], utc_now(), event_id),
            )

    def mark_callback_skipped(self, event_id: str, reason: str) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                "UPDATE callback_events SET status='SKIPPED',last_error=?,updated_at=? WHERE id=?",
                (reason[:500], utc_now(), event_id),
            )

    def retry_callback(self, event_id: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM callback_events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise KeyError("callback event not found")
            if row["status"] not in ("FAILED", "RETRY"):
                raise ValueError("only failed callbacks can be retried")
            db.execute(
                "UPDATE callback_events SET status='PENDING',attempts=0,last_error=NULL,updated_at=? WHERE id=?",
                (utc_now(), event_id),
            )
            updated = db.execute("SELECT * FROM callback_events WHERE id=?", (event_id,)).fetchone()
            return dict(updated)

    def withdrawal_events(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM withdrawal_events ORDER BY id DESC")]

    def withdrawal_events_for(self, withdrawal_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM withdrawal_events WHERE withdrawal_id=? ORDER BY id ASC",
                    (withdrawal_id,),
                )
            ]

    def callbacks_for(self, reference_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM callback_events WHERE reference_id=? ORDER BY created_at ASC, rowid ASC",
                    (reference_id,),
                )
            ]

    def business_transactions(self) -> list[dict[str, Any]]:
        """Return a unified operational view of inbound and outbound business transactions."""
        rows: list[dict[str, Any]] = []
        with self.connect() as db:
            for row in db.execute(
                """SELECT id,merchant,amount,pay_currency AS asset,network,pay_address AS address,
                status,tx_hash,created_at,updated_at FROM payment_orders"""
            ):
                item = dict(row)
                rows.append(
                    {
                        "reference_id": item["id"],
                        "merchant": item["merchant"],
                        "direction": "IN",
                        "type": "PAYMENT",
                        "amount": item["amount"],
                        "asset": item["asset"],
                        "network": item["network"],
                        "address": item["address"],
                        "status": item["status"],
                        "tx_hash": item["tx_hash"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                    }
                )
            for row in db.execute(
                """SELECT id,merchant,amount,currency AS asset,network,to_address AS address,
                status,tx_hash,created_at,updated_at FROM withdrawals"""
            ):
                item = dict(row)
                rows.append(
                    {
                        "reference_id": item["id"],
                        "merchant": item["merchant"],
                        "direction": "OUT",
                        "type": "WITHDRAWAL",
                        "amount": item["amount"],
                        "asset": item["asset"],
                        "network": item["network"],
                        "address": item["address"],
                        "status": item["status"],
                        "tx_hash": item["tx_hash"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                    }
                )
            for row in db.execute(
                """SELECT id,amount,asset,network,destination_address AS address,status,tx_hash,
                created_at,updated_at FROM collection_tasks"""
            ):
                item = dict(row)
                rows.append(
                    {
                        "reference_id": item["id"],
                        "merchant": "M2 Automatic Sweep",
                        "direction": "INTERNAL",
                        "type": "COLLECTION",
                        "amount": item["amount"],
                        "asset": item["asset"],
                        "network": item["network"],
                        "address": item["address"],
                        "status": item["status"],
                        "tx_hash": item["tx_hash"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                    }
                )
        rows.sort(key=lambda item: (item["updated_at"], item["reference_id"]), reverse=True)
        return rows

    def reconciliation_report(self) -> dict[str, Any]:
        with self.connect() as db:
            imbalanced = [
                dict(row)
                for row in db.execute(
                    """SELECT journal_id,asset,
                    printf('%.6f',SUM(CAST(debit AS REAL))) AS debit,
                    printf('%.6f',SUM(CAST(credit AS REAL))) AS credit
                    FROM ledger_lines GROUP BY journal_id,asset
                    HAVING ABS(SUM(CAST(debit AS REAL))-SUM(CAST(credit AS REAL))) > 0.0000005"""
                )
            ]
            counts = {
                "pending_payments": db.execute("SELECT COUNT(*) FROM payment_orders WHERE status='PENDING'").fetchone()[0],
                "open_withdrawals": db.execute(
                    "SELECT COUNT(*) FROM withdrawals WHERE status NOT IN ('CONFIRMED','REJECTED')"
                ).fetchone()[0],
                "failed_withdrawals": db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='FAILED'").fetchone()[0],
                "failed_callbacks": db.execute("SELECT COUNT(*) FROM callback_events WHERE status='FAILED'").fetchone()[0],
                "pending_collections": db.execute("SELECT COUNT(*) FROM collection_tasks WHERE status='PENDING'").fetchone()[0],
                "failed_collections": db.execute("SELECT COUNT(*) FROM collection_tasks WHERE status='FAILED'").fetchone()[0],
            }
        return {
            "ok": not imbalanced and not counts["failed_withdrawals"] and not counts["failed_collections"],
            "imbalanced_journals": imbalanced,
            **counts,
        }
