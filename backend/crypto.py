"""
Cryptographic utilities for attestation signing and verification.
Ed25519 via the `cryptography` library.
"""

import hashlib
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)
from cryptography.exceptions import InvalidSignature


def canonical_json(obj: dict) -> bytes:
    """Deterministic JSON serialization: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def content_addressed_id(payload: dict) -> str:
    """SHA-256 of canonical JSON — any mutation produces a different ID."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def generate_keypair() -> tuple[str, str]:
    """
    Generate a new Ed25519 keypair.
    Returns (private_key_hex, public_key_hex).
    """
    private_key = Ed25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes.hex(), pub_bytes.hex()


def sign_payload(payload: dict, private_key_hex: str) -> str:
    """
    Sign canonical JSON of payload with Ed25519 private key.
    Returns hex-encoded signature.
    """
    priv_bytes = bytes.fromhex(private_key_hex)
    private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    message = canonical_json(payload)
    signature = private_key.sign(message)
    return signature.hex()


def verify_signature(payload: dict, signature_hex: str, public_key_hex: str) -> bool:
    """
    Verify Ed25519 signature over canonical JSON of payload.
    Returns True if valid, False otherwise.
    """
    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        message = canonical_json(payload)
        sig_bytes = bytes.fromhex(signature_hex)
        public_key.verify(sig_bytes, message)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False
