# M2 Wallet Merchant Integration Contract

Base URL: `http://127.0.0.1:8787/api/v1` for the local Demo.

All merchant API requests use:

```http
X-API-Key: <your-configured-api-key>
Content-Type: application/json
```

## Create a stablecoin payment

`POST /payment-orders`

```json
{
  "merchant_order_id": "ORDER-10001",
  "merchant": "Merchant Platform",
  "customer_id": "CUSTOMER-8821",
  "amount": "100.00",
  "order_currency": "USD",
  "pay_currency": "USDT",
  "network": "TRON",
  "callback_url": "https://merchant.example/webhooks/m2-wallet",
  "return_url": "https://merchant.example/orders/ORDER-10001",
  "metadata": {
    "cart_id": "CART-91",
    "channel": "checkout"
  }
}
```

Supported pairs in the one-week Demo are `USDT/TRON` and `USDC/POLYGON`.
`merchant_order_id` is the idempotency key. Repeating it returns the original order.
Reusing the same value with a different amount, currency, or network returns a
`400 idempotency conflict` response instead of silently changing the order.

The platform resolves `fee_rate_bps` from admin-managed merchant and asset rules;
merchant requests cannot set or override their own commission. The resolved rate
is stored on the order at creation, so later rule changes affect only new orders.
Admins manage rules through `GET|POST /payment-fee-rules`. Resolution priority is
exact merchant + asset, merchant + `ALL`, platform default + asset, then platform
default + `ALL`.

Query an order by either the M2 Wallet ID or the merchant order ID:

```http
GET /payment-orders/{payment_id_or_merchant_order_id}
GET /payment-orders/{payment_id_or_merchant_order_id}/callbacks
```

Payment states:

- `PENDING`: no confirmed payment
- `PARTIAL`: underpaid beyond the 1% tolerance
- `CONFIRMED`: paid within the 1% tolerance
- `OVERPAID`: paid above the 1% tolerance
- `EXPIRED`: payment window elapsed or was expired by an operator

The public checkout URL is `/pay/{M2 payment id}`.

## Create a withdrawal request

`POST /withdrawals`

```json
{
  "merchant_withdraw_id": "WITHDRAW-7001",
  "merchant": "Merchant Platform",
  "user_id": "CUSTOMER-8821",
  "amount": "25.00",
  "currency": "USDT",
  "network": "TRON",
  "to_address": "TCGrXtAjSDfUV2xxNEgaui28odN6MdyHun",
  "callback_url": "https://merchant.example/webhooks/m2-wallet",
  "metadata": {
    "request_source": "customer_portal"
  }
}
```

`merchant_withdraw_id` is the idempotency key. A withdrawal remains
`PENDING_APPROVAL` until FINANCE or ADMIN approves it. Approval then starts the
sign, broadcast, and confirmation flow automatically.

Reusing a withdrawal ID with a different amount, asset, network, or destination
address is rejected as an idempotency conflict. Query the current state and its
complete approval timeline using:

```http
GET /withdrawals/{withdrawal_id_or_merchant_withdraw_id}
GET /withdrawals/{withdrawal_id_or_merchant_withdraw_id}/events
GET /withdrawals/{withdrawal_id_or_merchant_withdraw_id}/callbacks
```

## Signed callbacks

Callbacks contain the merchant reference, customer ID, asset, network, amount,
status, and transaction hash when available. Headers:

```http
X-M2-Event: payment.confirmed
X-M2-Event-Id: EVT-260723-ABC123
X-M2-Timestamp: 1784750000
X-M2-Signature: sha256=<hex digest>
```

Verify `HMAC-SHA256(secret, timestamp + "." + raw_request_body)` and reject old
timestamps. Return any `2xx` response after the event is durably accepted.
Persist `X-M2-Event-Id` with a unique constraint before applying business
effects. A retry carries the same event ID and must return `2xx` without posting
the payment or withdrawal twice. The JSON body also includes `event_id`,
`event_type`, and `occurred_at` for auditability.

## Local merchant sandbox

For the internal demo, set the project callback URL to
`http://127.0.0.1:8787/api/v1/demo-merchant/webhook`. The receiver validates the
same timestamp and HMAC signature used by a real merchant platform and persists
verified receipts. In the admin UI, open `WaaS Projects → Settings → Callback
settings`, click `Use demo receiver`, then open `Callback history` and click
`Deliver pending callbacks`.

The sandbox permits plain HTTP only on loopback addresses. External callback
URLs still require HTTPS and an allowlisted hostname.

## Pre-payout merchant validation

When external withdrawal validation is enabled, M2 Wallet sends a signed request
to `M2_WITHDRAWAL_VERIFICATION_URL` before changing a withdrawal from
`PENDING_APPROVAL` to `APPROVED`. The request includes the merchant withdrawal
ID, customer ID, amount, asset, network, destination address, and metadata. It
uses the same timestamp and HMAC-SHA256 headers as callbacks.

The merchant returns:

```json
{"approved": true, "reason": "customer withdrawal remains valid"}
```

A rejected or unavailable validation stops signing and broadcasting. The
withdrawal remains pending for review and the result is written to the approval
timeline and audit log. The local demo automatically uses
`/api/v1/demo-merchant/withdrawal-validation`.

## Demo test scenarios

The payment-order detail drawer exposes buttons for exact payment, underpayment,
overpayment, and expiry. These are local simulation controls and are not part of
the commercial merchant API.
