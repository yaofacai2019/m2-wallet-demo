"""Signed webhook delivery and bounded retry worker for M2 Wallet."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.store import M2WalletStore


class CallbackWorker:
    def __init__(
        self,
        store: M2WalletStore,
        secret: str,
        allowed_hosts: set[str],
        opener: Callable[..., Any] = urlopen,
        max_attempts: int = 5,
        allow_http_loopback: bool = False,
    ):
        if not secret:
            raise ValueError("callback signing secret is required")
        self.store = store
        self.secret = secret.encode()
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.opener = opener
        self.max_attempts = max(max_attempts, 1)
        self.allow_http_loopback = allow_http_loopback

    def _validate_url(self, value: str) -> None:
        parsed = urlparse(value)
        is_demo_loopback = (
            self.allow_http_loopback
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (parsed.scheme != "https" and not is_demo_loopback) or not parsed.hostname:
            raise ValueError("callback URL must use HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("callback URL cannot contain credentials")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise ValueError("callback host is not allowlisted")

    def _deliver(self, event: dict[str, Any]) -> None:
        callback_url = event.get("callback_url")
        if not callback_url:
            self.store.mark_callback_skipped(event["id"], "no callback URL")
            return
        self._validate_url(callback_url)
        timestamp = str(int(time.time()))
        payload = event["payload"].encode()
        signature = hmac.new(self.secret, timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
        request = Request(
            callback_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-M2-Event": event["event_type"],
                "X-M2-Event-Id": event["id"],
                "X-M2-Timestamp": timestamp,
                "X-M2-Signature": f"sha256={signature}",
            },
            method="POST",
        )
        with self.opener(request, timeout=10) as response:
            if not 200 <= int(response.status) < 300:
                raise RuntimeError(f"callback returned HTTP {response.status}")
        self.store.mark_callback_success(event["id"])

    def run_once(self) -> dict[str, int]:
        delivered = failed = skipped = 0
        events = self.store.pending_callbacks(max_attempts=self.max_attempts)
        for event in events:
            if not event.get("callback_url"):
                self.store.mark_callback_skipped(event["id"], "no callback URL")
                skipped += 1
                continue
            try:
                self._deliver(event)
                delivered += 1
            except Exception as error:
                self.store.mark_callback_failure(event["id"], str(error), self.max_attempts)
                failed += 1
        return {"events": len(events), "delivered": delivered, "failed": failed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver pending M2 Wallet callbacks")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--interval", type=int, default=10, help="seconds between runs")
    args = parser.parse_args()
    secret = os.environ.get("M2_CALLBACK_SECRET")
    if not secret:
        raise RuntimeError("M2_CALLBACK_SECRET is required")
    allowed = {host.strip() for host in os.environ.get("M2_CALLBACK_ALLOWED_HOSTS", "").split(",") if host.strip()}
    allow_local = os.environ.get("M2_CALLBACK_ALLOW_LOCAL_HTTP", "false").lower() in {"1", "true", "yes", "on"}
    worker = CallbackWorker(M2WalletStore(), secret, allowed, allow_http_loopback=allow_local)
    while True:
        print(json.dumps(worker.run_once(), ensure_ascii=False))
        if args.once:
            return
        time.sleep(max(args.interval, 2))


if __name__ == "__main__":
    main()
