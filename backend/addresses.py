"""Address helpers and the wallet-address provider boundary for M2 Wallet."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any, Protocol
from urllib.request import Request, urlopen


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    padding = len(data) - len(data.lstrip(b"\0"))
    return "1" * padding + (encoded or "1")


def _base58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        try:
            digit = BASE58_ALPHABET.index(char)
        except ValueError as exc:
            raise ValueError("invalid Base58 character") from exc
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    padding = len(value) - len(value.lstrip("1"))
    return b"\0" * padding + raw


def tron_base58check(payload: bytes) -> str:
    if len(payload) != 21 or payload[0] != 0x41:
        raise ValueError("TRON payload must be 21 bytes and start with 0x41")
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _base58_encode(payload + checksum)


def validate_tron_address(value: str) -> bool:
    try:
        decoded = _base58_decode(value)
    except ValueError:
        return False
    if len(decoded) != 25 or decoded[0] != 0x41:
        return False
    payload, checksum = decoded[:-4], decoded[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return secrets.compare_digest(checksum, expected)


def validate_evm_address(value: str) -> bool:
    if len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


class AddressProvider(Protocol):
    def create_address(self, network: str, asset: str, reference_id: str) -> str: ...


class SimulatedAddressProvider:
    """Creates checksum-valid but non-spendable demo addresses.

    Funds must never be sent to these addresses. Real environments must use
    HttpAddressProvider backed by the organization's wallet/signing service.
    """

    def create_address(self, network: str, asset: str, reference_id: str) -> str:
        del asset, reference_id
        if network == "TRON":
            return tron_base58check(b"\x41" + secrets.token_bytes(20))
        if network == "POLYGON":
            return "0x" + secrets.token_hex(20)
        raise ValueError("unsupported address network")


class HttpAddressProvider:
    """Client for an external wallet service that owns address derivation."""

    def __init__(self, url: str, token: str, opener=urlopen):
        self.url, self.token, self.opener = url.rstrip("/"), token, opener

    def create_address(self, network: str, asset: str, reference_id: str) -> str:
        request = Request(
            f"{self.url}/v1/addresses",
            data=json.dumps({"network": network, "asset": asset, "reference_id": reference_id}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with self.opener(request, timeout=15) as response:
            result: dict[str, Any] = json.loads(response.read())
        address = str(result["address"])
        if network == "TRON" and not validate_tron_address(address):
            raise ValueError("wallet service returned an invalid TRON address")
        if network == "POLYGON" and not validate_evm_address(address):
            raise ValueError("wallet service returned an invalid EVM address")
        return address


def address_provider_from_env() -> AddressProvider:
    url = os.environ.get("M2_ADDRESS_PROVIDER_URL")
    if not url:
        return SimulatedAddressProvider()
    token = os.environ.get("M2_ADDRESS_PROVIDER_TOKEN")
    if not token:
        raise RuntimeError("M2_ADDRESS_PROVIDER_TOKEN is required when M2_ADDRESS_PROVIDER_URL is set")
    return HttpAddressProvider(url, token)
