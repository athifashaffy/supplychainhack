"""
Event-day swap points.

The six functions below are the *only* places the official spec touches this
codebase. Pre-event we ship mock bodies so the rest of the system can be built
and tested. On event day, only the function bodies change. Signatures are
frozen now and any caller code (verify.py, content.py) should not be edited
to accommodate spec changes.

The four genuine unknowns to confirm at kickoff, mapped to which adapter
isolates them:

    canonical serialization (DSSE vs JCS vs custom)   -> canonicalize_payload
    real Ed25519 verify primitive                     -> verify_signature
    real registry format + keying                     -> resolve_key
    cost-flow / partial-consumption rule              -> get_costs
    how 'substantial transformation' is identified    -> handled in content.py
                                                         (find_last_substantial_
                                                         transformation), which
                                                         is its own swap point
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional, Tuple

from .models import Attestation


# ---------------------------------------------------------------------------
# 1. validate  --  schema validation
# ---------------------------------------------------------------------------
# Two payload shapes get validated in the system:
#   - a single attestation object  (used during ingestion at the supplier UI)
#   - the full verification request (root_hash + attestations[])
# We expose two named functions so call sites are unambiguous.

def validate_attestation_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """
    Returns (ok, error_message) for a single attestation object.

    Pre-event: minimal shape check. On event day, swap in the real JSON
    schema provided with the spec.
    """
    if not isinstance(payload, dict):
        return False, "payload is not a JSON object"
    if "hash" not in payload:
        return False, "missing required field: hash"
    return True, None


def validate_verification_request(payload: dict) -> Tuple[bool, Optional[str]]:
    """
    Returns (ok, error_message) for a top-level verification request.

    Pre-event: minimal shape check; Pydantic is doing the heavy lifting at
    the API boundary via main.py's RequestValidationError handler. On event
    day, swap in the spec's request schema if it differs.
    """
    if not isinstance(payload, dict):
        return False, "payload is not a JSON object"
    if "root_hash" not in payload:
        return False, "missing required field: root_hash"
    if "attestations" not in payload:
        return False, "missing required field: attestations"
    if not isinstance(payload["attestations"], list):
        return False, "'attestations' must be a list"
    return True, None


# Deprecated alias kept for compatibility. Remove after event day. Calling
# this is a developer-error tell: prefer one of the two named functions.
def validate(payload: dict) -> Tuple[bool, Optional[str]]:
    return validate_attestation_payload(payload)


# ---------------------------------------------------------------------------
# 2. canonicalize_payload  --  bytes that get signed
# ---------------------------------------------------------------------------
def canonicalize_payload(payload: dict) -> bytes:
    """
    Produces the canonical byte representation of a payload. This is the
    exact input fed to Ed25519.sign() and Ed25519.verify(), so byte-for-byte
    parity with the reference library is the single most fragile point of
    this whole system.

    Pre-event: sorted-key compact JSON (close enough to JCS for testing).
    Event day: swap to the spec's chosen canonicalization. If the spec
    uses DSSE/PAE, return PAE-wrapped bytes here, not raw JCS.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def content_hash(payload: dict) -> str:
    """SHA-256 over the canonical bytes. Used as the attestation id."""
    return hashlib.sha256(canonicalize_payload(payload)).hexdigest()


# ---------------------------------------------------------------------------
# 3. verify_signature  --  cryptographic verification of one attestation
# ---------------------------------------------------------------------------
def verify_signature(att: Attestation) -> bool:
    """
    Pre-event mock: trust the boolean flag on the attestation. This lets
    us hand-craft fixtures that simulate tampering without setting up real
    keys for every test case.

    Event day: replace the body with a call to the spec's reference
    library, something like:

        pubkey = resolve_key(att.supplier_id)
        if pubkey is None:
            return False
        canonical = canonicalize_payload(att.payload_dict_without_signature)
        return reference_lib.verify(canonical, att.signature_bytes, pubkey)

    Never roll your own crypto here. Use the provided reference library.
    """
    return att.signature_valid


# ---------------------------------------------------------------------------
# 4. resolve_key  --  registry lookup
# ---------------------------------------------------------------------------
def resolve_key(supplier_id: str) -> Optional[object]:
    """
    Returns the supplier's public key, or None if the supplier is not in
    the verified registry.

    Pre-event mock: returns a sentinel truthy value if the attestation
    bundle marked the signer as known, None otherwise. The check is
    folded into verify.py via the attestation's signer_known flag at
    construction time; this stub keeps the interface consistent.

    Event day: open the provided registry file, parse it, and look up
    the supplier by id. Cache the parsed registry at startup so this
    function is just a dict lookup.
    """
    # This function is consulted per-attestation in check_node_integrity;
    # the mock looks at the signer_known flag indirectly via the caller.
    # See check_node_integrity for the actual lookup pattern.
    return _MOCK_REGISTRY.get(supplier_id, None)


# Mock registry populated at attestation-construction time. In real adapters
# this would be loaded from the spec's registry file at app startup.
_MOCK_REGISTRY: dict[str, object] = {}


def register_supplier(supplier_id: str, key: object = True) -> None:
    """Helper to seed the mock registry from fixtures or tests."""
    _MOCK_REGISTRY[supplier_id] = key


def bootstrap_registry_from_bundle(attestations) -> None:
    """
    Pre-event only: seed the mock registry from the bundle's signer_known
    flags. Called once per verify request so tests remain independent.

    On event day this function disappears entirely: the registry is loaded
    from the spec-provided file once at app startup, and resolve_key reads
    from that loaded structure for every request.
    """
    _MOCK_REGISTRY.clear()
    for att in attestations:
        if att.signer_known:
            _MOCK_REGISTRY[att.supplier_id] = True


# ---------------------------------------------------------------------------
# 5. get_costs  --  cost extraction with the spec's cost-flow rule
# ---------------------------------------------------------------------------
def get_costs(att: Attestation) -> Tuple[int, int]:
    """
    Returns (materials_cents, labour_cents).

    Pre-event: direct field read from the attestation. On event day, this
    is where the cost-flow / partial-consumption rule encoded by the spec
    lives. If the spec attributes upstream cost differently from our
    quantity-weighted flow (in content.attribute_costs_by_country), the
    weighting parameters get adjusted here, not in the math engine.
    """
    return att.materials_cents, att.labour_cents


# ---------------------------------------------------------------------------
# 6. find_last_st  --  substantial-transformation identification
# ---------------------------------------------------------------------------
# This function lives in content.py as find_last_substantial_transformation
# because it needs the supply-chain graph, not a single attestation. The
# adapter boundary is still respected: replace only that function's body
# on event day, not its signature or callers.
