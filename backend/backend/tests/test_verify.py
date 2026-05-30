"""
Coverage:
  - happy_path        : VERIFIED, MADE_IN_CANADA, 96.6%
  - diamond           : shared input with no upstream; flow stays trivial
  - deep_diamond      : shared component WITH upstream; flow must
                        re-propagate (this is the regression test for the
                        seen_edges bug)
  - tampered          : SIGNATURE_INVALID -> UNVERIFIABLE
  - unknown_signer    : UNKNOWN_ISSUER -> UNVERIFIABLE
  - broken_link       : BROKEN_LINK -> UNVERIFIABLE
  - missing_root      : single BROKEN_LINK, no duplicate
  - cycle             : CYCLE -> UNVERIFIABLE, downstream checks skipped
  - overdraw          : MASS_BALANCE -> UNVERIFIABLE
  - temporal_inversion: TEMPORAL_INVERSION -> UNVERIFIABLE
  - orphan_attestation: unreachable nodes ignored
  - precedence        : when multiple anomalies fire, primary sorts first
"""
import json
from pathlib import Path

from app.models import (
    ChainStatus,
    Designation,
    Reason,
    VerificationRequest,
)
from app.verify import verify_supply_chain


FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> VerificationRequest:
    return VerificationRequest(**json.loads((FIXTURES / name).read_text()))


# ---------- happy path ----------

def test_happy_path_made_in_canada():
    r = verify_supply_chain(load("happy_path.json"))
    assert r.chain_status == ChainStatus.VERIFIED
    assert r.designation == Designation.MADE_IN_CANADA
    assert r.total_cost_cents == 1470
    assert r.canadian_cost_cents == 1420
    assert round(r.canadian_pct * 100, 1) == 96.6


# ---------- diamond DAG cost attribution ----------

def test_diamond_shared_input():
    """Shared ALU with no upstream. Flows are trivially 1 everywhere."""
    r = verify_supply_chain(load("diamond.json"))
    assert r.chain_status == ChainStatus.VERIFIED
    assert r.cost_by_country == {"CA": 1800, "US": 200}
    assert r.total_cost_cents == 2000
    assert round(r.canadian_pct * 100, 1) == 90.0


def test_deep_diamond_propagates_through_shared_component():
    """
    Regression test for the seen_edges bug.

    RAW -> MOTOR(2 units) -> {LEFT_ARM, RIGHT_ARM} -> DRONE.
    MOTOR has TWO consumers and ITS OWN upstream. The fix is to compute
    flow in topological order so MOTOR's flow is final (= 1.0) before it
    propagates to RAW.

    With the old stack+seen_edges code: RAW reports 500 cents (half lost).
    With the fix: RAW reports 1000 cents (full upstream cost preserved).
    """
    r = verify_supply_chain(load("deep_diamond.json"))
    assert r.chain_status == ChainStatus.VERIFIED
    assert r.cost_by_country == {"CA": 1200, "US": 1000}
    assert r.total_cost_cents == 2200
    assert round(r.canadian_pct * 100, 2) == 54.55
    assert r.designation == Designation.MADE_IN_CANADA  # 54.55% >= 51%


# ---------- integrity ----------

def test_tampered_chain_is_unverifiable():
    r = verify_supply_chain(load("tampered.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert r.designation is None
    assert any(a.reason == Reason.SIGNATURE_INVALID for a in r.anomalies)


def test_unknown_signer_is_unverifiable():
    r = verify_supply_chain(load("unknown_signer.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.UNKNOWN_ISSUER for a in r.anomalies)


# ---------- structural ----------

def test_broken_link_is_unverifiable():
    r = verify_supply_chain(load("broken_link.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    broken = [a for a in r.anomalies if a.reason == Reason.BROKEN_LINK]
    assert broken, "expected a BROKEN_LINK anomaly"
    assert "GHOST_MOTOR" in broken[0].detail


def test_missing_root_emits_one_broken_link():
    """No duplicate anomaly when root itself is missing."""
    r = verify_supply_chain(load("missing_root.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    broken = [a for a in r.anomalies if a.reason == Reason.BROKEN_LINK]
    assert len(broken) == 1, f"expected exactly 1 BROKEN_LINK, got {len(broken)}"
    assert broken[0].attestation_hash == "DOES_NOT_EXIST"


def test_cycle_is_unverifiable_and_short_circuits():
    """Cycle means downstream checks are skipped; CYCLE is the only anomaly."""
    r = verify_supply_chain(load("cycle.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.CYCLE for a in r.anomalies)
    assert not any(a.reason == Reason.MASS_BALANCE for a in r.anomalies)


# ---------- consistency ----------

def test_overdraw_chain_is_unverifiable():
    r = verify_supply_chain(load("overdraw.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.MASS_BALANCE for a in r.anomalies)


def test_temporal_inversion_is_unverifiable():
    r = verify_supply_chain(load("temporal_inversion.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.TEMPORAL_INVERSION for a in r.anomalies)


# ---------- isolation & precedence ----------

def test_orphan_attestation_is_ignored():
    """Unreachable nodes must not affect the verdict or pollute totals."""
    data = json.loads((FIXTURES / "happy_path.json").read_text())
    data["attestations"].append({
        "hash": "ORPHAN", "supplier_id": "SUP-EVIL",
        "output": {"product_id": "ghost", "quantity": 1, "unit": "unit"},
        "inputs": [], "materials_cents": 99999, "labour_cents": 0,
        "work_country": "CN", "is_substantial_transformation": False,
        "timestamp": "2026-05-30T08:00:00Z",
        "signature_valid": False, "signer_known": False,
    })
    r = verify_supply_chain(VerificationRequest(**data))
    assert r.chain_status == ChainStatus.VERIFIED
    assert r.total_cost_cents == 1470
    assert round(r.canadian_pct * 100, 1) == 96.6


def test_anomaly_precedence_signature_before_mass_balance():
    """
    When both signature and mass-balance fail, SIGNATURE_INVALID sorts first.
    """
    data = json.loads((FIXTURES / "overdraw.json").read_text())
    # Tamper the root so it has both signature failure and overdraw.
    data["attestations"][1]["signature_valid"] = False
    r = verify_supply_chain(VerificationRequest(**data))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert r.anomalies[0].reason == Reason.SIGNATURE_INVALID


# ---------- new in this patch ----------

def test_duplicate_conflicting_body_is_hash_mismatch():
    """
    Two attestations with the same declared hash but different bodies
    violate content addressing. Now fires HASH_MISMATCH, not DUPLICATE_HASH.
    """
    r = verify_supply_chain(load("duplicate_conflicting.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    mismatches = [a for a in r.anomalies if a.reason == Reason.HASH_MISMATCH]
    assert len(mismatches) == 1
    assert mismatches[0].attestation_hash == "DRONE"


def test_duplicate_identical_body_is_silently_ignored():
    """
    Byte-identical duplicates are noise, not an attack. Bundle with the
    same root attestation listed twice should verify normally.
    """
    r = verify_supply_chain(load("duplicate_identical.json"))
    assert r.chain_status == ChainStatus.VERIFIED
    assert not any(a.reason == Reason.HASH_MISMATCH for a in r.anomalies)
    assert not any(a.reason == Reason.DUPLICATE_HASH for a in r.anomalies)


def test_naive_timestamp_returns_malformed_not_crash():
    """
    Regression: a naive datetime input compared against an aware consumer
    used to raise TypeError mid-verification. Now emits MALFORMED.
    """
    r = verify_supply_chain(load("naive_timestamp.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.MALFORMED for a in r.anomalies)


def test_adapters_validate_split():
    """
    The two named validation functions accept the right payload shapes
    and reject the wrong ones.
    """
    from app import adapters

    # Attestation-shape validator.
    ok, _ = adapters.validate_attestation_payload({"hash": "abc"})
    assert ok
    ok, err = adapters.validate_attestation_payload({"root_hash": "x"})
    assert not ok and "hash" in err

    # Request-shape validator.
    ok, _ = adapters.validate_verification_request(
        {"root_hash": "abc", "attestations": []}
    )
    assert ok
    ok, err = adapters.validate_verification_request({"root_hash": "abc"})
    assert not ok and "attestations" in err


def test_bad_timestamp_emits_malformed():
    """Unparseable timestamp string emits MALFORMED, not a crash."""
    r = verify_supply_chain(load("bad_timestamp.json"))
    assert r.chain_status == ChainStatus.UNVERIFIABLE
    assert any(a.reason == Reason.MALFORMED for a in r.anomalies)


def test_mixed_offset_timestamps_parse_correctly():
    """
    Regression: Z and +00:00 represent the same instant. String comparison
    would mis-sort '2026-05-30T11:00:00+00:00' as later than
    '2026-05-30T10:00:00Z' (correct here) but would also wrongly accept
    inverted offsets. Datetime parsing handles both correctly.
    """
    r = verify_supply_chain(load("mixed_offset_ok.json"))
    assert r.chain_status == ChainStatus.VERIFIED
    assert not any(a.reason == Reason.TEMPORAL_INVERSION for a in r.anomalies)
    assert not any(a.reason == Reason.MALFORMED for a in r.anomalies)


def test_get_costs_adapter_is_routed_through():
    """
    Patch adapters.get_costs to return doubled costs; the verifier should
    pick this up automatically. This confirms content.py uses the adapter
    rather than reading the attestation directly.
    """
    from app import adapters

    original = adapters.get_costs
    adapters.get_costs = lambda att: (att.materials_cents * 2, att.labour_cents * 2)
    try:
        r = verify_supply_chain(load("happy_path.json"))
        # Original totals are 1470 / 1420; doubled should be 2940 / 2840.
        assert r.total_cost_cents == 2940
        assert r.canadian_cost_cents == 2840
    finally:
        adapters.get_costs = original


# ---------- malformed-request HTTP handler ----------

def test_malformed_request_returns_structured_unverifiable():
    """
    Pydantic schema failures must come back as VerificationResult with
    MALFORMED, not as HTTP 422 with FastAPI's default error shape.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # Missing required field 'attestations'.
    r = client.post("/verify", json={"root_hash": "DRONE"})
    assert r.status_code == 200
    body = r.json()
    assert body["chain_status"] == "UNVERIFIABLE"
    assert body["designation"] is None
    assert len(body["anomalies"]) >= 1
    assert body["anomalies"][0]["reason"] == "MALFORMED"


def test_malformed_field_type_returns_structured_unverifiable():
    """Wrong type in a nested field also routes through MALFORMED."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    bundle = json.loads((FIXTURES / "happy_path.json").read_text())
    # Corrupt a nested int field with a string.
    bundle["attestations"][0]["materials_cents"] = "not a number"

    r = client.post("/verify", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["chain_status"] == "UNVERIFIABLE"
    assert body["anomalies"][0]["reason"] == "MALFORMED"
