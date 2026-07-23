from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("M2_WALLET_API_KEY", "unit-test-api-key")
os.environ.setdefault("M2_CALLBACK_SECRET", "unit-test-callback-secret")
os.environ.setdefault("M2_ADMIN_PASSWORD", "unit-test-admin-password")
os.environ.setdefault("M2_FINANCE_PASSWORD", "unit-test-finance-password")
os.environ.setdefault("M2_OPERATOR_PASSWORD", "unit-test-operator-password")
os.environ.setdefault("M2_VIEWER_PASSWORD", "unit-test-viewer-password")

from backend.chain_listener import TronGridClient, TronUsdtListener
from backend.callback_worker import CallbackWorker
from backend.addresses import SimulatedAddressProvider, validate_evm_address, validate_tron_address
from backend.evm_listener import EvmJsonRpcClient, PolygonUsdcListener, TRANSFER_TOPIC
from backend.payout import PayoutService, RpcBroadcaster, SignedTransaction, SimulatedBroadcaster, SimulatedSigner
from backend.confirmation_worker import WithdrawalConfirmationWorker
from backend.server import API_KEY, M2WalletServer
from backend.store import M2WalletStore


TRON_ADDRESS = SimulatedAddressProvider().create_address("TRON", "USDT", "test")
EVM_ADDRESS = SimulatedAddressProvider().create_address("POLYGON", "USDC", "test")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = M2WalletStore(Path(self.tempdir.name) / "test.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_payment_is_idempotent_and_balanced(self) -> None:
        payload = {
            "merchant_order_id": "M-1001",
            "amount": "100.000000",
            "order_currency": "USD",
            "pay_currency": "USDT",
            "network": "TRON",
            "callback_url": "https://merchant.invalid/callback",
        }
        first, created = self.store.create_payment(payload)
        second, created_again = self.store.create_payment(payload)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["fee_amount"], "0.500000")

        confirmed = self.store.confirm_payment(first["id"])
        confirmed_again = self.store.confirm_payment(first["id"])
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["tx_hash"], confirmed_again["tx_hash"])
        lines = self.store.ledger()
        self.assertEqual(sum(Decimal(line["debit"]) for line in lines), Decimal("100"))
        self.assertEqual(sum(Decimal(line["credit"]) for line in lines), Decimal("100"))
        self.assertEqual(len(self.store.callbacks()), 1)

    def test_withdrawal_approval_is_idempotent_and_balanced(self) -> None:
        payload = {
            "merchant_withdraw_id": "W-1001",
            "user_id": "U-9",
            "amount": "20",
            "currency": "USDC",
            "network": "POLYGON",
            "to_address": EVM_ADDRESS,
        }
        first, created = self.store.create_withdrawal(payload)
        second, created_again = self.store.create_withdrawal(payload)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        approved = self.store.approve_withdrawal(first["id"], "finance-a")
        approved_again = self.store.approve_withdrawal(first["id"], "finance-a")
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["id"], approved_again["id"])
        self.assertEqual(approved["reviewed_by"], "finance-a")
        signed = SimulatedSigner().sign(
            {"reference_id": first["id"], "network": "POLYGON", "asset": "USDC", "amount": "20.000000", "to_address": EVM_ADDRESS}
        )
        self.store.mark_withdrawal_signing(first["id"], signed.signature_ref)
        tx_hash = SimulatedBroadcaster().broadcast(signed)
        self.store.mark_withdrawal_broadcasted(first["id"], tx_hash)
        confirmed = self.store.confirm_withdrawal(first["id"])
        self.assertEqual(confirmed["status"], "CONFIRMED")
        lines = self.store.ledger()
        self.assertEqual(sum(Decimal(line["debit"]) for line in lines), Decimal("20"))
        self.assertEqual(sum(Decimal(line["credit"]) for line in lines), Decimal("20"))
        events = list(reversed(self.store.withdrawal_events()))
        self.assertEqual([event["status"] for event in events], ["PENDING_APPROVAL", "APPROVED", "SIGNING", "BROADCASTED", "CONFIRMED"])

    def test_reject_and_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_withdrawal(
                {"merchant_withdraw_id": "bad", "amount": "1", "currency": "USDT", "network": "TRON", "to_address": "bad"}
            )
        item, _ = self.store.create_withdrawal(
            {"merchant_withdraw_id": "W-2", "amount": "1", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
        )
        rejected = self.store.reject_withdrawal(item["id"], "finance-b")
        self.assertEqual(rejected["status"], "REJECTED")
        with self.assertRaises(ValueError):
            self.store.approve_withdrawal(item["id"], "finance-b")

    def test_unsupported_pair_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_payment(
                {"merchant_order_id": "x", "amount": "1", "order_currency": "USD", "pay_currency": "USDC", "network": "TRON"}
            )

    def test_idempotency_rejects_conflicting_payment_and_withdrawal_payloads(self) -> None:
        payment_payload = {
            "merchant_order_id": "IDEMPOTENT-PAYMENT",
            "amount": "10",
            "order_currency": "USD",
            "pay_currency": "USDT",
            "network": "TRON",
        }
        payment, created = self.store.create_payment(payment_payload)
        replay, created_again = self.store.create_payment({**payment_payload, "amount": "10.000000"})
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(payment["id"], replay["id"])
        with self.assertRaisesRegex(ValueError, "idempotency conflict.*amount"):
            self.store.create_payment({**payment_payload, "amount": "11"})

        withdrawal_payload = {
            "merchant_withdraw_id": "IDEMPOTENT-WITHDRAWAL",
            "amount": "2",
            "currency": "USDT",
            "network": "TRON",
            "to_address": TRON_ADDRESS,
        }
        withdrawal, created = self.store.create_withdrawal(withdrawal_payload)
        replay, created_again = self.store.create_withdrawal({**withdrawal_payload, "amount": "2.000000"})
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(withdrawal["id"], replay["id"])
        other_address = SimulatedAddressProvider().create_address("TRON", "USDT", "other")
        with self.assertRaisesRegex(ValueError, "idempotency conflict.*to_address"):
            self.store.create_withdrawal({**withdrawal_payload, "to_address": other_address})

    def test_api_key_hash_scope_rotation_and_status(self) -> None:
        created = self.store.create_api_key(
            {"name": "Merchant payments", "scopes": ["payments:read", "payments:write"]},
            "admin",
        )
        secret = created["secret"]
        self.assertTrue(secret.startswith("m2_live_"))
        self.assertNotIn("key_hash", created)
        resolved = self.store.resolve_api_key(secret)
        self.assertEqual(resolved["id"], created["id"])
        self.assertEqual(resolved["scopes"], ["payments:read", "payments:write"])
        self.assertIsNotNone(resolved["last_used_at"])
        self.store.set_api_key_status(created["id"], False)
        self.assertIsNone(self.store.resolve_api_key(secret))
        rotated = self.store.rotate_api_key(created["id"])
        self.assertNotEqual(rotated["secret"], secret)
        self.assertIsNone(self.store.resolve_api_key(secret))
        self.assertEqual(self.store.resolve_api_key(rotated["secret"])["id"], created["id"])

    def test_generated_addresses_are_checksum_valid(self) -> None:
        tron = SimulatedAddressProvider().create_address("TRON", "USDT", "t")
        evm = SimulatedAddressProvider().create_address("POLYGON", "USDC", "e")
        self.assertTrue(validate_tron_address(tron))
        self.assertTrue(validate_evm_address(evm))
        self.assertFalse(validate_tron_address(tron[:-1] + ("1" if tron[-1] != "1" else "2")))

    def test_demo_readiness_uses_live_operational_evidence(self) -> None:
        readiness = self.store.demo_readiness()
        self.assertEqual(readiness["total"], 8)
        self.assertFalse(readiness["ready"])
        self.assertEqual(len(readiness["checks"]), 8)
        self.assertTrue(next(item for item in readiness["checks"] if item["id"] == "reconciliation")["passed"])
        self.assertFalse(next(item for item in readiness["checks"] if item["id"] == "stablecoin_payments")["passed"])

    def test_collection_groups_confirmed_addresses_and_books_internal_transfer(self) -> None:
        for order_id, value in (("COLLECT-1", "60"), ("COLLECT-2", "50")):
            payment, _ = self.store.create_payment(
                {"merchant_order_id": order_id, "amount": value, "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
            )
            self.store.confirm_payment(payment["id"])
        candidates = self.store.collection_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["amount"], "110.000000")
        self.assertEqual(candidates[0]["source_count"], 2)
        self.assertTrue(candidates[0]["eligible"])

        collected = self.store.run_collection("TRON", "USDT", "operator")
        self.assertEqual(collected["status"], "CONFIRMED")
        self.assertEqual(collected["amount"], "110.000000")
        self.assertEqual(collected["source_count"], 2)
        self.assertEqual(len(collected["tx_hash"]), 64)
        self.assertEqual(self.store.collection_candidates(), [])
        collection_lines = [line for line in self.store.ledger() if line["reference_type"] == "COLLECTION"]
        self.assertEqual(len(collection_lines), 2)
        self.assertEqual(sum(Decimal(line["debit"]) for line in collection_lines), Decimal("110"))
        self.assertEqual(sum(Decimal(line["credit"]) for line in collection_lines), Decimal("110"))
        with self.assertRaises(ValueError):
            self.store.run_collection("TRON", "USDT", "operator")

    def test_address_book_persists_and_blocklist_stops_withdrawal(self) -> None:
        entry, created = self.store.create_address_book_entry(
            {"list_type": "BLOCKLIST", "asset": "USDT", "network": "TRON", "address": TRON_ADDRESS, "alias": "Risk test"},
            "admin",
        )
        duplicate, created_again = self.store.create_address_book_entry(
            {"list_type": "BLOCKLIST", "asset": "USDT", "network": "TRON", "address": TRON_ADDRESS, "alias": "Ignored"},
            "admin",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(entry["id"], duplicate["id"])
        self.assertEqual(self.store.list_address_book()[0]["alias"], "Risk test")
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.store.create_withdrawal(
                {"merchant_withdraw_id": "W-BLOCKED", "amount": "1", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )

    def test_allowlist_enforcement_blocks_unknown_recipient(self) -> None:
        self.store.update_risk_policy({"allowlist_enforced": True}, "admin")
        with self.assertRaisesRegex(ValueError, "not on the allowlist"):
            self.store.create_withdrawal(
                {"merchant_withdraw_id": "W-NOT-ALLOWED", "amount": "1", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
        self.store.create_address_book_entry(
            {"list_type": "ALLOWLIST", "asset": "USDT", "network": "TRON", "address": TRON_ADDRESS, "alias": "Approved"},
            "admin",
        )
        allowed, created = self.store.create_withdrawal(
            {"merchant_withdraw_id": "W-ALLOWED", "amount": "1", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
        )
        self.assertTrue(created)
        self.assertEqual(allowed["status"], "PENDING_APPROVAL")

    def test_project_settings_drive_callbacks_and_ip_allowlist(self) -> None:
        settings = self.store.update_project_settings(
            {"callback_url": "https://merchant.invalid/callback", "minimum_usdt": "2"}, "admin"
        )
        self.assertEqual(settings["minimum_callbacks"]["USDT"], "2.000000")
        payment, _ = self.store.create_payment(
            {"merchant_order_id": "MIN-CALLBACK", "customer_id": "CUST-42", "return_url": "https://merchant.invalid/return", "metadata": {"cart_id": "CART-7"}, "amount": "1", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
        )
        self.assertEqual(payment["customer_id"], "CUST-42")
        self.assertEqual(json.loads(payment["metadata_json"])["cart_id"], "CART-7")
        self.store.confirm_payment(payment["id"])
        callback = self.store.callbacks()[0]
        self.assertEqual(callback["callback_url"], "https://merchant.invalid/callback")
        self.assertEqual(callback["status"], "SKIPPED")
        self.assertEqual(json.loads(callback["payload"])["customer_id"], "CUST-42")
        ip_entry, created = self.store.create_ip_allowlist_entry({"name": "Local API", "cidr": "127.0.0.1"}, "admin")
        self.assertTrue(created)
        self.assertEqual(ip_entry["cidr"], "127.0.0.1/32")
        self.assertTrue(self.store.is_ip_allowed("127.0.0.1"))
        self.assertFalse(self.store.is_ip_allowed("10.0.0.1"))
        self.store.update_project_settings({"enabled": False}, "admin")
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.store.create_payment(
                {"merchant_order_id": "DISABLED", "amount": "1", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
            )

    def test_underpayment_overpayment_and_expiry_states(self) -> None:
        partial, _ = self.store.create_payment(
            {"merchant_order_id": "PARTIAL", "amount": "100", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
        )
        first = self.store.simulate_payment(partial["id"], "50")
        self.assertEqual(first["status"], "PARTIAL")
        self.assertEqual(first["paid_amount"], "50.000000")
        self.assertFalse([line for line in self.store.ledger() if line["reference_id"] == partial["id"]])
        completed = self.store.confirm_payment(partial["id"])
        self.assertEqual(completed["status"], "CONFIRMED")
        self.assertEqual(completed["paid_amount"], "100.000000")

        over, _ = self.store.create_payment(
            {"merchant_order_id": "OVER", "amount": "100", "order_currency": "USD", "pay_currency": "USDC", "network": "POLYGON"}
        )
        overpaid = self.store.simulate_payment(over["id"], "105")
        self.assertEqual(overpaid["status"], "OVERPAID")
        over_lines = [line for line in self.store.ledger() if line["reference_id"] == over["id"]]
        self.assertEqual(sum(Decimal(line["debit"]) for line in over_lines), Decimal("105"))

        expiring, _ = self.store.create_payment(
            {"merchant_order_id": "EXPIRE", "amount": "10", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
        )
        expired = self.store.expire_payment(expiring["id"])
        self.assertEqual(expired["status"], "EXPIRED")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        store = M2WalletStore(Path(cls.tempdir.name) / "api.db")
        cls.server = M2WalletServer(("127.0.0.1", 0), store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

    def request(self, path: str, method: str = "GET", payload: dict | None = None, authorized: bool = True, cookie: str = ""):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-API-Key"] = API_KEY
        if cookie:
            headers["Cookie"] = cookie
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def login(self, username: str, password: str) -> tuple[dict, str]:
        request = Request(
            self.base_url + "/api/v1/session",
            data=json.dumps({"username": username, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            return json.loads(response.read())["data"], cookie

    def test_health_and_authenticated_payment_flow(self) -> None:
        status, health = self.request("/api/v1/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "simulation")
        with self.assertRaises(HTTPError) as error:
            self.request("/api/v1/payment-orders", authorized=False)
        self.assertEqual(error.exception.code, 401)

        status, result = self.request(
            "/api/v1/payment-orders",
            "POST",
            {"merchant_order_id": "API-1", "amount": "9.5", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"},
        )
        self.assertEqual(status, 201)
        payment_id = result["data"]["id"]
        status, public_order = self.request(f"/api/v1/public/payment-orders/{payment_id}", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(public_order["data"]["status"], "PENDING")
        self.assertNotIn("callback_url", public_order["data"])
        with urlopen(self.base_url + f"/pay/{payment_id}", timeout=2) as checkout:
            self.assertEqual(checkout.status, 200)
            self.assertIn(b"M2 Wallet Pay", checkout.read())
        status, result = self.request(f"/api/v1/payment-orders/{payment_id}/simulate-confirm", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["status"], "CONFIRMED")
        _, public_order = self.request(f"/api/v1/public/payment-orders/{payment_id}", authorized=False)
        self.assertEqual(public_order["data"]["status"], "CONFIRMED")

        status, second = self.request(
            "/api/v1/payment-orders",
            "POST",
            {"merchant_order_id": "API-COLLECT", "amount": "100", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"},
        )
        self.assertEqual(status, 201)
        self.request(f"/api/v1/payment-orders/{second['data']['id']}/simulate-confirm", "POST", {})
        status, candidates = self.request("/api/v1/collection-candidates")
        self.assertEqual(status, 200)
        self.assertTrue(candidates["data"][0]["eligible"])
        status, collection = self.request("/api/v1/collections/run", "POST", {"asset": "USDT", "network": "TRON"})
        self.assertEqual(status, 201)
        self.assertEqual(collection["data"]["status"], "CONFIRMED")
        status, readiness = self.request("/api/v1/demo-readiness")
        self.assertEqual(status, 200)
        self.assertEqual(readiness["data"]["total"], 8)
        self.assertEqual(len(readiness["data"]["checks"]), 8)

    def test_local_merchant_sandbox_receives_signed_callback(self) -> None:
        callback_url = f"{self.base_url}/api/v1/demo-merchant/webhook"
        status, settings = self.request(
            "/api/v1/project-settings", "POST", {"callback_url": callback_url}
        )
        self.assertEqual(status, 200)
        self.assertEqual(settings["data"]["callback_url"], callback_url)
        _, created = self.request(
            "/api/v1/payment-orders",
            "POST",
            {
                "merchant_order_id": "SANDBOX-CALLBACK-1",
                "customer_id": "CUSTOMER-1",
                "amount": "12.5",
                "order_currency": "USD",
                "pay_currency": "USDT",
                "network": "TRON",
            },
        )
        payment_id = created["data"]["id"]
        self.request(f"/api/v1/payment-orders/{payment_id}/simulate-confirm", "POST", {})
        status, delivered = self.request("/api/v1/callbacks/deliver-pending", "POST", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(delivered["data"]["delivered"], 1)
        _, receipts = self.request("/api/v1/demo-merchant/webhooks")
        matching = [item for item in receipts["data"] if item["reference_id"] == payment_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["event_type"], "payment.confirmed")
        self.assertEqual(matching[0]["signature_valid"], 1)
        self.assertTrue(matching[0]["event_id"].startswith("EVT-"))

        self.request(
            "/api/v1/project-settings", "POST", {"withdrawal_verification_enabled": True}
        )
        _, withdrawal = self.request(
            "/api/v1/withdrawals",
            "POST",
            {
                "merchant_withdraw_id": "SANDBOX-WITHDRAW-1",
                "user_id": "CUSTOMER-1",
                "amount": "6",
                "currency": "USDT",
                "network": "TRON",
                "to_address": TRON_ADDRESS,
            },
        )
        withdrawal_id = withdrawal["data"]["id"]
        status, approved = self.request(f"/api/v1/withdrawals/{withdrawal_id}/approve", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(approved["data"]["status"], "CONFIRMED")
        _, receipts = self.request("/api/v1/demo-merchant/webhooks")
        validations = [item for item in receipts["data"] if item["reference_id"] == withdrawal_id]
        self.assertEqual(len(validations), 1)
        self.assertEqual(validations[0]["event_type"], "withdrawal.validation")

        _, rejected = self.request(
            "/api/v1/withdrawals",
            "POST",
            {
                "merchant_withdraw_id": "SANDBOX-WITHDRAW-REJECT",
                "amount": "4",
                "currency": "USDT",
                "network": "TRON",
                "to_address": TRON_ADDRESS,
                "metadata": {"force_verification_reject": True},
            },
        )
        rejected_id = rejected["data"]["id"]
        with self.assertRaises(HTTPError) as denied:
            self.request(f"/api/v1/withdrawals/{rejected_id}/approve", "POST", {})
        self.assertEqual(denied.exception.code, 400)
        _, withdrawals = self.request("/api/v1/withdrawals")
        rejected_row = next(item for item in withdrawals["data"] if item["id"] == rejected_id)
        self.assertEqual(rejected_row["status"], "PENDING_APPROVAL")

    def test_merchant_can_query_by_own_reference_and_follow_event_timeline(self) -> None:
        _, payment = self.request(
            "/api/v1/payment-orders",
            "POST",
            {
                "merchant_order_id": "MERCHANT-QUERY-PAY-1",
                "amount": "18",
                "order_currency": "USD",
                "pay_currency": "USDC",
                "network": "POLYGON",
            },
        )
        status, queried_payment = self.request("/api/v1/payment-orders/MERCHANT-QUERY-PAY-1")
        self.assertEqual(status, 200)
        self.assertEqual(queried_payment["data"]["id"], payment["data"]["id"])
        self.request(f"/api/v1/payment-orders/{payment['data']['id']}/simulate-confirm", "POST", {})
        _, payment_callbacks = self.request("/api/v1/payment-orders/MERCHANT-QUERY-PAY-1/callbacks")
        self.assertEqual(payment_callbacks["data"][-1]["event_type"], "payment.confirmed")
        callback_payload = json.loads(payment_callbacks["data"][-1]["payload"])
        self.assertEqual(callback_payload["event_id"], payment_callbacks["data"][-1]["id"])

        _, withdrawal = self.request(
            "/api/v1/withdrawals",
            "POST",
            {
                "merchant_withdraw_id": "MERCHANT-QUERY-WD-1",
                "amount": "3",
                "currency": "USDC",
                "network": "POLYGON",
                "to_address": EVM_ADDRESS,
            },
        )
        status, queried_withdrawal = self.request("/api/v1/withdrawals/MERCHANT-QUERY-WD-1")
        self.assertEqual(status, 200)
        self.assertEqual(queried_withdrawal["data"]["id"], withdrawal["data"]["id"])
        self.request(f"/api/v1/withdrawals/{withdrawal['data']['id']}/approve", "POST", {})
        _, timeline = self.request("/api/v1/withdrawals/MERCHANT-QUERY-WD-1/events")
        payout_statuses = [
            event["status"]
            for event in timeline["data"]
            if event["status"] != "EXTERNAL_VALIDATION"
        ]
        self.assertEqual(
            payout_statuses,
            ["PENDING_APPROVAL", "APPROVED", "SIGNING", "BROADCASTED", "CONFIRMED"],
        )
        _, withdrawal_callbacks = self.request("/api/v1/withdrawals/MERCHANT-QUERY-WD-1/callbacks")
        self.assertEqual(withdrawal_callbacks["data"][-1]["event_type"], "withdrawal.confirmed")

    def test_role_sessions_permissions_audit_and_risk_policy(self) -> None:
        viewer, viewer_cookie = self.login("viewer", "unit-test-viewer-password")
        self.assertEqual(viewer["role"], "VIEWER")
        status, _ = self.request("/api/v1/payment-orders", authorized=False, cookie=viewer_cookie)
        self.assertEqual(status, 200)
        with self.assertRaises(HTTPError) as forbidden:
            self.request(
                "/api/v1/payment-orders",
                "POST",
                {"merchant_order_id": "VIEWER-DENIED", "amount": "1", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"},
                authorized=False,
                cookie=viewer_cookie,
            )
        self.assertEqual(forbidden.exception.code, 403)

        operator, operator_cookie = self.login("operator", "unit-test-operator-password")
        self.assertEqual(operator["role"], "OPERATOR")
        status, result = self.request(
            "/api/v1/withdrawals",
            "POST",
            {"merchant_withdraw_id": "ROLE-WD-1", "amount": "8", "currency": "USDC", "network": "POLYGON", "to_address": EVM_ADDRESS},
            authorized=False,
            cookie=operator_cookie,
        )
        self.assertEqual(status, 201)
        withdrawal_id = result["data"]["id"]
        with self.assertRaises(HTTPError) as operator_forbidden:
            self.request(f"/api/v1/withdrawals/{withdrawal_id}/approve", "POST", {}, authorized=False, cookie=operator_cookie)
        self.assertEqual(operator_forbidden.exception.code, 403)

        finance, finance_cookie = self.login("finance", "unit-test-finance-password")
        self.assertEqual(finance["role"], "FINANCE")
        status, approved = self.request(
            f"/api/v1/withdrawals/{withdrawal_id}/approve", "POST", {}, authorized=False, cookie=finance_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["data"]["reviewed_by"], "finance")
        status, logs = self.request("/api/v1/audit-logs", authorized=False, cookie=finance_cookie)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["actor"] == "finance" and item["action"] == "APPROVE" for item in logs["data"]))

        status, policy = self.request(
            "/api/v1/risk-policy",
            "POST",
            {"payouts_enabled": False, "max_withdrawal_amount": "2500", "daily_withdrawal_limit": "40000", "allowlist_enforced": False},
        )
        self.assertEqual(status, 200)
        self.assertFalse(policy["data"]["payouts_enabled"])
        self.assertEqual(policy["data"]["max_withdrawal_amount"], "2500.000000")
        self.assertEqual(policy["data"]["daily_withdrawal_limit"], "40000.000000")
        self.assertFalse(policy["data"]["allowlist_enforced"])
        self.request("/api/v1/risk-policy", "POST", {"payouts_enabled": True, "max_withdrawal_amount": "10000"})

    def test_admin_manages_scoped_api_key_and_scope_is_enforced(self) -> None:
        _, admin_cookie = self.login("admin", "unit-test-admin-password")
        status, created = self.request(
            "/api/v1/api-keys",
            "POST",
            {"name": "Payment-only integration", "scopes": ["payments:read", "payments:write"]},
            authorized=False,
            cookie=admin_cookie,
        )
        self.assertEqual(status, 201)
        secret = created["data"]["secret"]
        request = Request(
            self.base_url + "/api/v1/payment-orders",
            headers={"X-API-Key": secret},
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
        denied = Request(
            self.base_url + "/api/v1/withdrawals",
            headers={"X-API-Key": secret},
        )
        with self.assertRaises(HTTPError) as missing_scope:
            urlopen(denied, timeout=2)
        self.assertEqual(missing_scope.exception.code, 403)
        status, keys = self.request("/api/v1/api-keys", authorized=False, cookie=admin_cookie)
        self.assertEqual(status, 200)
        managed = next(item for item in keys["data"] if item["id"] == created["data"]["id"])
        self.assertNotIn("secret", managed)
        self.assertNotIn("key_hash", managed)
        self.assertIsNotNone(managed["last_used_at"])
        status, disabled = self.request(
            f"/api/v1/api-keys/{managed['id']}/status",
            "POST",
            {"enabled": False},
            authorized=False,
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(disabled["data"]["status"], "DISABLED")


class _FixtureResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class TronListenerTests(unittest.TestCase):
    def test_confirmed_usdt_transfer_matches_payment_and_books_fee(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "tron.db")
            payment, _ = store.create_payment(
                {"merchant_order_id": "TRON-1", "amount": "12.5", "order_currency": "USD", "pay_currency": "USDT", "network": "TRON"}
            )
            fixture = {
                "success": True,
                "data": [
                    {
                        "transaction_id": "a" * 64,
                        "to": payment["pay_address"],
                        "from": TRON_ADDRESS,
                        "type": "Transfer",
                        "value": "12500000",
                        "token_info": {"symbol": "USDT", "decimals": 6, "address": "USDT-CONTRACT"},
                    }
                ],
            }

            def opener(_request, timeout=0):
                self.assertEqual(timeout, 15)
                return _FixtureResponse(fixture)

            client = TronGridClient("https://tron.invalid", opener=opener)
            result = TronUsdtListener(store, client, "USDT-CONTRACT").run_once()
            self.assertEqual(result, {"orders": 1, "transfers": 1, "matched": 1, "errors": 0})
            confirmed = store.get_payment(payment["id"])
            self.assertEqual(confirmed["status"], "CONFIRMED")
            self.assertEqual(confirmed["tx_hash"], "a" * 64)
            self.assertEqual(len(store.ledger()), 3)


class PolygonListenerTests(unittest.TestCase):
    def test_confirmed_usdc_transfer_log_matches_payment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "polygon.db")
            payment, _ = store.create_payment(
                {"merchant_order_id": "POLYGON-1", "amount": "42.25", "order_currency": "USD", "pay_currency": "USDC", "network": "POLYGON"}
            )
            contract = "0x" + "2" * 40
            recipient_topic = "0x" + "0" * 24 + payment["pay_address"][2:].lower()
            responses = [
                {"jsonrpc": "2.0", "id": 1, "result": "0x2710"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": [
                        {
                            "address": contract,
                            "topics": [TRANSFER_TOPIC, "0x" + "0" * 64, recipient_topic],
                            "data": hex(42_250_000),
                            "transactionHash": "0x" + "a" * 64,
                            "blockNumber": "0x2700",
                            "removed": False,
                        }
                    ],
                },
            ]

            def opener(_request, timeout=0):
                self.assertEqual(timeout, 15)
                return _FixtureResponse(responses.pop(0))

            client = EvmJsonRpcClient("https://polygon.invalid", opener=opener)
            result = PolygonUsdcListener(store, client, contract, confirmations=20, lookback_blocks=200).run_once()
            self.assertEqual(result, {"orders": 1, "logs": 1, "matched": 1, "errors": 0})
            confirmed = store.get_payment(payment["id"])
            self.assertEqual(confirmed["status"], "CONFIRMED")
            self.assertEqual(confirmed["tx_hash"], "0x" + "a" * 64)
            self.assertEqual(len(store.ledger()), 3)


class PayoutServiceTests(unittest.TestCase):
    def test_service_runs_full_approval_sign_broadcast_confirm_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "payout.db")
            withdrawal, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "PAYOUT-1", "amount": "3", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            result = PayoutService(store).approve_and_send(withdrawal["id"], "finance-c")
            self.assertEqual(result["status"], "CONFIRMED")
            self.assertEqual(len(result["tx_hash"]), 64)
            events = list(reversed(store.withdrawal_events()))
            self.assertEqual([event["status"] for event in events], ["PENDING_APPROVAL", "APPROVED", "SIGNING", "BROADCASTED", "CONFIRMED"])
            self.assertEqual([event["event_type"] for event in store.callbacks()], ["withdrawal.confirmed", "withdrawal.broadcasted", "withdrawal.approved"])

    def test_real_mode_leaves_withdrawal_broadcasted_until_confirmation_worker(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "confirmation.db")
            withdrawal, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "REAL-1", "amount": "7", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            broadcasted = PayoutService(store, auto_confirm=False).approve_and_send(withdrawal["id"], "finance-real")
            self.assertEqual(broadcasted["status"], "BROADCASTED")

            class ConfirmedChecker:
                def check(self, _withdrawal):
                    return "CONFIRMED"

            result = WithdrawalConfirmationWorker(store, ConfirmedChecker()).run_once()
            self.assertEqual(result, {"checked": 1, "confirmed": 1, "failed": 0, "errors": 0})
            self.assertEqual(store.get_withdrawal(withdrawal["id"])["status"], "CONFIRMED")

    def test_rpc_broadcaster_supports_tron_and_polygon_signed_payloads(self) -> None:
        responses = [
            _FixtureResponse({"result": True, "txid": "b" * 64}),
            _FixtureResponse({"jsonrpc": "2.0", "id": 1, "result": "0x" + "c" * 64}),
        ]

        def opener(_request, timeout=0):
            self.assertEqual(timeout, 15)
            return responses.pop(0)

        broadcaster = RpcBroadcaster("https://tron.invalid", "https://polygon.invalid", opener)
        tron_hash = broadcaster.broadcast(SignedTransaction("TRON", json.dumps({"txID": "b" * 64, "signature": ["x"]}), "ref"))
        polygon_hash = broadcaster.broadcast(SignedTransaction("POLYGON", "0xdeadbeef", "ref"))
        self.assertEqual(tron_hash, "b" * 64)
        self.assertEqual(polygon_hash, "0x" + "c" * 64)

    def test_payout_pause_and_single_payment_limit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "policy.db")
            withdrawal, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "POLICY-1", "amount": "11", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            store.update_risk_policy({"payouts_enabled": False}, "test-admin")
            with self.assertRaisesRegex(RuntimeError, "paused"):
                PayoutService(store).approve_and_send(withdrawal["id"], "finance-policy")
            store.update_risk_policy({"payouts_enabled": True, "max_withdrawal_amount": "10"}, "test-admin")
            with self.assertRaisesRegex(RuntimeError, "exceeds"):
                PayoutService(store).approve_and_send(withdrawal["id"], "finance-policy")
            self.assertEqual(store.get_withdrawal(withdrawal["id"])["status"], "PENDING_APPROVAL")

            store.update_risk_policy({"max_withdrawal_amount": "100", "daily_withdrawal_limit": "10"}, "test-admin")
            first, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "DAILY-1", "amount": "6", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            PayoutService(store).approve_and_send(first["id"], "finance-policy")
            second, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "DAILY-2", "amount": "5", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            with self.assertRaisesRegex(RuntimeError, "daily payout limit"):
                PayoutService(store).approve_and_send(second["id"], "finance-policy")
            policy = store.risk_policy()
            self.assertEqual(policy["daily_withdrawal_limit"], "10.000000")
            self.assertEqual(policy["daily_withdrawn_amount"], "6.000000")

    def test_external_withdrawal_verification_controls_signing(self) -> None:
        class RejectVerifier:
            def verify(self, _withdrawal):
                return False, "customer cancelled withdrawal"

        class ApproveVerifier:
            def verify(self, _withdrawal):
                return True, "merchant order remains valid"

        with tempfile.TemporaryDirectory() as folder:
            store = M2WalletStore(Path(folder) / "verification.db")
            store.update_project_settings({"withdrawal_verification_enabled": True}, "admin")
            withdrawal, _ = store.create_withdrawal(
                {"merchant_withdraw_id": "VERIFY-1", "amount": "9", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
            )
            with self.assertRaisesRegex(ValueError, "merchant platform rejected"):
                PayoutService(store, verifier=RejectVerifier()).approve_and_send(withdrawal["id"], "finance")
            self.assertEqual(store.get_withdrawal(withdrawal["id"])["status"], "PENDING_APPROVAL")
            rejected_event = store.withdrawal_events()[0]
            self.assertEqual(rejected_event["status"], "EXTERNAL_VALIDATION")
            self.assertIn("REJECTED", rejected_event["detail"])

            confirmed = PayoutService(store, verifier=ApproveVerifier()).approve_and_send(withdrawal["id"], "finance")
            self.assertEqual(confirmed["status"], "CONFIRMED")
            statuses = [event["status"] for event in reversed(store.withdrawal_events())]
            self.assertEqual(statuses[:3], ["PENDING_APPROVAL", "EXTERNAL_VALIDATION", "EXTERNAL_VALIDATION"])


class CallbackAndReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = M2WalletStore(Path(self.tempdir.name) / "callbacks.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_confirmed_payment(self, callback_url: str | None) -> None:
        payment, _ = self.store.create_payment(
            {
                "merchant_order_id": f"CB-{len(self.store.list_payments())}",
                "amount": "10",
                "order_currency": "USD",
                "pay_currency": "USDT",
                "network": "TRON",
                "callback_url": callback_url,
            }
        )
        self.store.confirm_payment(payment["id"])

    def test_signed_callback_delivery(self) -> None:
        self.create_confirmed_payment("https://merchant.example/callback")
        captured = {}

        def opener(request, timeout=0):
            captured["timeout"] = timeout
            captured["headers"] = {key.lower(): value for key, value in request.header_items()}
            captured["body"] = request.data
            return _FixtureResponse({}, status=204)

        result = CallbackWorker(self.store, "test-secret", {"merchant.example"}, opener).run_once()
        self.assertEqual(result, {"events": 1, "delivered": 1, "failed": 0, "skipped": 0})
        self.assertEqual(captured["timeout"], 10)
        self.assertIn("x-m2-signature", captured["headers"])
        self.assertIn("x-m2-event-id", captured["headers"])
        self.assertTrue(captured["headers"]["x-m2-signature"].startswith("sha256="))
        delivered_payload = json.loads(captured["body"])
        self.assertEqual(delivered_payload["status"], "CONFIRMED")
        self.assertEqual(delivered_payload["event_id"], captured["headers"]["x-m2-event-id"])
        self.assertEqual(self.store.callbacks()[0]["status"], "DELIVERED")

    def test_demo_merchant_deduplicates_callback_event_id(self) -> None:
        payload = {"id": "PAY-1", "event_id": "EVT-STABLE", "status": "CONFIRMED"}
        first = self.store.record_demo_webhook("payment.confirmed", payload, True, "EVT-STABLE")
        replay = self.store.record_demo_webhook("payment.confirmed", payload, True, "EVT-STABLE")
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(len(self.store.demo_webhook_receipts()), 1)

    def test_callback_allowlist_failure_moves_to_dead_letter(self) -> None:
        self.create_confirmed_payment("https://blocked.example/callback")
        worker = CallbackWorker(self.store, "test-secret", {"merchant.example"}, max_attempts=2)
        self.assertEqual(worker.run_once()["failed"], 1)
        self.assertEqual(worker.run_once()["failed"], 1)
        event = self.store.callbacks()[0]
        self.assertEqual(event["status"], "FAILED")
        self.assertEqual(event["attempts"], 2)
        retried = self.store.retry_callback(event["id"])
        self.assertEqual(retried["status"], "PENDING")
        self.assertEqual(retried["attempts"], 0)

    def test_reconciliation_detects_imbalanced_journal(self) -> None:
        self.assertTrue(self.store.reconciliation_report()["ok"])
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO ledger_lines
                (journal_id,reference_type,reference_id,account,asset,debit,credit,created_at)
                VALUES('broken','TEST','1','TEST','USDT','1','0','2026-07-22T00:00:00+00:00')"""
            )
        report = self.store.reconciliation_report()
        self.assertFalse(report["ok"])
        self.assertEqual(report["imbalanced_journals"][0]["journal_id"], "broken")

    def test_unified_business_transactions(self) -> None:
        self.create_confirmed_payment(None)
        withdrawal, _ = self.store.create_withdrawal(
            {"merchant_withdraw_id": "TX-VIEW-1", "amount": "2", "currency": "USDT", "network": "TRON", "to_address": TRON_ADDRESS}
        )
        PayoutService(self.store).approve_and_send(withdrawal["id"], "finance-view")
        rows = self.store.business_transactions()
        self.assertEqual({row["direction"] for row in rows}, {"IN", "OUT"})
        self.assertEqual({row["type"] for row in rows}, {"PAYMENT", "WITHDRAWAL"})


if __name__ == "__main__":
    unittest.main()
