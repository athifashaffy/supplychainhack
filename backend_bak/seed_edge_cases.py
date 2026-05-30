"""
Edge-case seed data for the Canadian supply chain provenance system.
Covers every anomaly type the scoring harness will test.

Each scenario is clearly labelled with:
  - what it tests
  - what anomalies/designations are expected
"""

import asyncpg
import json
import hashlib
from datetime import datetime, timezone
from crypto import generate_keypair, sign_payload, content_addressed_id, canonical_json
from registry import register_supplier

EDGE_CASES: dict[str, str] = {}  # name -> root attestation_id (populated during seed)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fake_id(label: str) -> str:
    """Fabricated deterministic ID for cycle injection (NOT content-addressed)."""
    return hashlib.sha256(f"__fake__{label}".encode()).hexdigest()


async def _insert(conn, att_id: str, payload: dict, signature: str, signer_id: str, sig_valid: bool):
    await conn.execute("""
        INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
        VALUES ($1, $2::jsonb, $3, $4, $5)
        ON CONFLICT (id) DO UPDATE
            SET payload = EXCLUDED.payload,
                signature = EXCLUDED.signature,
                signer_id = EXCLUDED.signer_id,
                sig_valid = EXCLUDED.sig_valid
    """, att_id, json.dumps(payload), signature, signer_id, sig_valid)

    # Populate attestation_inputs for cross-chain reuse detection
    for input_id in payload.get("inputs", []):
        await conn.execute("""
            INSERT INTO attestation_inputs (attestation_id, input_id)
            VALUES ($1, $2) ON CONFLICT DO NOTHING
        """, att_id, input_id)


async def seed_edge_cases(pool: asyncpg.Pool) -> dict:
    results = {}

    # ── Register edge-case suppliers ─────────────────────────────────────────
    suppliers = {}
    for sid, name, country, province in [
        ("ec-ca-only",    "PureCanada Parts Inc.",      "CA", "ON"),
        ("ec-us-only",    "AllAmerican Components LLC",  "US", None),
        ("ec-mixed",      "Continental Assemblies Ltd.", "CA", "QC"),
        ("ec-assembler",  "Final Touch Systems Inc.",    "CA", "BC"),
        ("ec-offshore",   "Pacific Rim Manufacturing",   "CN", None),
        ("ec-tamper",     "Integrity Composites Ltd.",   "CA", "AB"),
        ("ec-qty",        "Quantity Gadgets Corp.",      "CA", "ON"),
        ("ec-diamond",    "Diamond Base Materials Ltd.", "CA", "MB"),
    ]:
        priv, pub = generate_keypair()
        suppliers[sid] = {"priv": priv, "pub": pub}
        await register_supplier(pool, sid, name, country, province, pub)

    async with pool.acquire() as conn:

        # ── SCENARIO 1: 100% Canadian — "Product of Canada" ─────────────────
        # All materials and labour sourced in Canada, last transformation in CA
        # Expected: 100.0% → "Product of Canada", 0 anomalies
        s1_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-01: All-Canadian Sensor Module",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Canadian titanium alloy", "cost_cad": 600.0, "country": "CA"},
                {"name": "Canadian optics glass",   "cost_cad": 400.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 1000.0, "country": "CA"},
            "transformation_description": "Precision machining and optical assembly — all Canadian",
            "quantity_produced": 10.0,
            "unit": "unit",
        }
        s1_id = content_addressed_id(s1_payload)
        s1_sig = sign_payload(s1_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s1_id, s1_payload, s1_sig, "ec-ca-only", True)
        results["ec01_all_canadian_100pct"] = s1_id

        # ── SCENARIO 2: 0% Canadian — "Not Qualified" ───────────────────────
        # All materials and labour foreign, last transformation in CN
        # Expected: 0.0% → "Not Qualified", 0 anomalies (valid chain, just not Canadian)
        s2_payload = {
            "supplier_id": "ec-us-only",
            "product_name": "EC-02: Fully Foreign Control Board",
            "timestamp": now_iso(),
            "location": {"country": "US", "province": None},
            "inputs": [],
            "materials": [
                {"name": "US silicon chips",    "cost_cad": 800.0, "country": "US"},
                {"name": "Taiwanese PCB",       "cost_cad": 300.0, "country": "TW"},
            ],
            "labour": {"cost_cad": 500.0, "country": "US"},
            "transformation_description": "US assembly of foreign components",
            "quantity_produced": 5.0,
            "unit": "unit",
        }
        s2_id = content_addressed_id(s2_payload)
        s2_sig = sign_payload(s2_payload, suppliers["ec-us-only"]["priv"])
        await _insert(conn, s2_id, s2_payload, s2_sig, "ec-us-only", True)
        results["ec02_all_foreign_0pct"] = s2_id

        # ── SCENARIO 3: Exactly 98% Canadian — "Product of Canada" ──────────
        # $980 Canadian / $1000 total = exactly 98.0%
        # Last transformation in Canada
        # Expected: 98.0% → "Product of Canada"
        s3_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-03: Exactly 98pct Canadian Frame",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Canadian carbon fibre", "cost_cad": 880.0, "country": "CA"},
                {"name": "Imported fasteners",    "cost_cad":  20.0, "country": "US"},
            ],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Frame lay-up with 98% Canadian content",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s3_id = content_addressed_id(s3_payload)
        s3_sig = sign_payload(s3_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s3_id, s3_payload, s3_sig, "ec-ca-only", True)
        results["ec03_exactly_98pct"] = s3_id

        # ── SCENARIO 4: 97.9% Canadian — just below "Product of Canada" ─────
        # $979 Canadian / $1000 total = 97.9% → drops to "Made in Canada"
        s4_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-04: 97.9pct Canadian — Just Below Product of Canada",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Canadian carbon fibre", "cost_cad": 879.0, "country": "CA"},
                {"name": "Imported fasteners",    "cost_cad":  21.0, "country": "US"},
            ],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Same as EC-03 but $1 more foreign content",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s4_id = content_addressed_id(s4_payload)
        s4_sig = sign_payload(s4_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s4_id, s4_payload, s4_sig, "ec-ca-only", True)
        results["ec04_below_98pct"] = s4_id

        # ── SCENARIO 5: Exactly 51% Canadian — "Made in Canada" ─────────────
        # $510 Canadian / $1000 total = exactly 51.0%
        s5_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-05: Exactly 51pct Canadian Assembly",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [],
            "materials": [
                {"name": "Canadian aluminum",     "cost_cad": 360.0, "country": "CA"},
                {"name": "US electronic modules", "cost_cad": 490.0, "country": "US"},
            ],
            "labour": {"cost_cad": 150.0, "country": "CA"},
            "transformation_description": "Assembly at Quebec facility — 51% Canadian",
            "quantity_produced": 2.0,
            "unit": "unit",
        }
        s5_id = content_addressed_id(s5_payload)
        s5_sig = sign_payload(s5_payload, suppliers["ec-mixed"]["priv"])
        await _insert(conn, s5_id, s5_payload, s5_sig, "ec-mixed", True)
        results["ec05_exactly_51pct"] = s5_id

        # ── SCENARIO 6: 50.9% Canadian — just below "Made in Canada" ────────
        # $509 Canadian / $1000 = 50.9% → "Not Qualified"
        s6_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-06: 50.9pct — Just Below Made in Canada",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [],
            "materials": [
                {"name": "Canadian aluminum",     "cost_cad": 359.0, "country": "CA"},
                {"name": "US electronic modules", "cost_cad": 491.0, "country": "US"},
            ],
            "labour": {"cost_cad": 150.0, "country": "CA"},
            "transformation_description": "Assembly at Quebec — $1 too much foreign content",
            "quantity_produced": 2.0,
            "unit": "unit",
        }
        s6_id = content_addressed_id(s6_payload)
        s6_sig = sign_payload(s6_payload, suppliers["ec-mixed"]["priv"])
        await _insert(conn, s6_id, s6_payload, s6_sig, "ec-mixed", True)
        results["ec06_below_51pct"] = s6_id

        # ── SCENARIO 7: Last transformation NOT in Canada ────────────────────
        # All costs are Canadian ($1000 CAD) but final assembly is in the US
        # Expected: 0% eligible → "Not Qualified" (last transformation rule fails)
        s7_raw_ca_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-07a: Canadian Raw Materials (upstream)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Canadian copper wire",  "cost_cad": 400.0, "country": "CA"},
                {"name": "Canadian steel bracket", "cost_cad": 200.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 200.0, "country": "CA"},
            "transformation_description": "Raw material processing in Canada",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s7_raw_id = content_addressed_id(s7_raw_ca_payload)
        s7_raw_sig = sign_payload(s7_raw_ca_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s7_raw_id, s7_raw_ca_payload, s7_raw_sig, "ec-ca-only", True)

        s7_final_payload = {
            "supplier_id": "ec-us-only",
            "product_name": "EC-07: All-Canadian Costs But US Final Assembly",
            "timestamp": now_iso(),
            "location": {"country": "US", "province": None},  # last transformation in US!
            "inputs": [s7_raw_id],
            "materials": [],
            "labour": {"cost_cad": 200.0, "country": "CA"},  # Canadian labour, but US location
            "transformation_description": "Final packaging done in the US",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s7_id = content_addressed_id(s7_final_payload)
        s7_sig = sign_payload(s7_final_payload, suppliers["ec-us-only"]["priv"])
        await _insert(conn, s7_id, s7_final_payload, s7_sig, "ec-us-only", True)
        results["ec07_not_in_canada_last_transform"] = s7_id

        # ── SCENARIO 8: TAMPERED PAYLOAD ─────────────────────────────────────
        # Insert a valid attestation, then directly mutate the payload in DB.
        # The stored signature won't match the mutated payload.
        # Expected anomaly: invalid_signature
        s8_original_payload = {
            "supplier_id": "ec-tamper",
            "product_name": "EC-08: Tampered Attestation (original)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "AB"},
            "inputs": [],
            "materials": [
                {"name": "Canadian titanium", "cost_cad": 200.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 50.0, "country": "US"},  # honest: 80% Canadian
            "transformation_description": "Legitimate machining",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s8_id = content_addressed_id(s8_original_payload)
        s8_sig = sign_payload(s8_original_payload, suppliers["ec-tamper"]["priv"])
        await _insert(conn, s8_id, s8_original_payload, s8_sig, "ec-tamper", True)

        # NOW TAMPER: change labour.country from "US" to "CA" to fraudulently boost Canadian content
        s8_tampered = dict(s8_original_payload)
        s8_tampered["materials"] = [
            {"name": "Canadian titanium", "cost_cad": 200.0, "country": "CA"},
        ]
        s8_tampered["labour"] = {"cost_cad": 50.0, "country": "CA"}  # falsified!
        await conn.execute("""
            UPDATE attestations SET payload = $1::jsonb, sig_valid = FALSE
            WHERE id = $2
        """, json.dumps(s8_tampered), s8_id)
        results["ec08_tampered_payload"] = s8_id

        # ── SCENARIO 9: UNKNOWN SIGNER ───────────────────────────────────────
        # Attestation signed with a key not in the supplier registry.
        # Expected anomaly: unknown_signer
        s9_priv, _s9_pub = generate_keypair()  # key NOT registered
        s9_payload = {
            "supplier_id": "ghost-supplier-999",
            "product_name": "EC-09: Unknown Signer Attestation",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Mystery material", "cost_cad": 500.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 500.0, "country": "CA"},
            "transformation_description": "Signed by unregistered supplier",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s9_id = content_addressed_id(s9_payload)
        s9_sig = sign_payload(s9_payload, s9_priv)
        await _insert(conn, s9_id, s9_payload, s9_sig, "ghost-supplier-999", False)
        results["ec09_unknown_signer"] = s9_id

        # ── SCENARIO 10: QUANTITY FRAUD ───────────────────────────────────────
        # Upstream produced 2 units; downstream claims to have consumed 10 units.
        # Expected anomaly: quantity_fraud
        s10_upstream_payload = {
            "supplier_id": "ec-qty",
            "product_name": "EC-10a: Upstream Produced 2 Units",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Raw polymer", "cost_cad": 100.0, "country": "CA", "quantity": 2.0, "unit": "unit"},
            ],
            "labour": {"cost_cad": 50.0, "country": "CA"},
            "transformation_description": "Polymer moulding — batch of 2",
            "quantity_produced": 2.0,   # ← only 2 produced
            "unit": "unit",
        }
        s10_up_id = content_addressed_id(s10_upstream_payload)
        s10_up_sig = sign_payload(s10_upstream_payload, suppliers["ec-qty"]["priv"])
        await _insert(conn, s10_up_id, s10_upstream_payload, s10_up_sig, "ec-qty", True)

        s10_fraud_payload = {
            "supplier_id": "ec-qty",
            "product_name": "EC-10: Quantity Fraud — Claims 10 But Upstream Only Made 2",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [s10_up_id],
            "materials": [
                {"name": "Fraudulently claimed polymer", "cost_cad": 500.0, "country": "CA",
                 "quantity": 10.0, "unit": "unit"},  # ← claims 10 but only 2 exist!
            ],
            "labour": {"cost_cad": 200.0, "country": "CA"},
            "transformation_description": "Assembly using 10x units (fraudulent overclaim)",
            "quantity_produced": 10.0,
            "unit": "unit",
        }
        s10_id = content_addressed_id(s10_fraud_payload)
        s10_sig = sign_payload(s10_fraud_payload, suppliers["ec-qty"]["priv"])
        await _insert(conn, s10_id, s10_fraud_payload, s10_sig, "ec-qty", True)
        results["ec10_quantity_fraud"] = s10_id

        # ── SCENARIO 11: MISSING REFERENCE ───────────────────────────────────
        # References an attestation ID that doesn't exist in the database.
        # Expected anomaly: missing_reference
        ghost_id = "a" * 64  # valid SHA-256 hex length but non-existent
        s11_payload = {
            "supplier_id": "ec-assembler",
            "product_name": "EC-11: Missing Reference Input",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "BC"},
            "inputs": [ghost_id],   # ← this attestation doesn't exist
            "materials": [
                {"name": "Canadian casing", "cost_cad": 200.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Assembly referencing a missing upstream attestation",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s11_id = content_addressed_id(s11_payload)
        s11_sig = sign_payload(s11_payload, suppliers["ec-assembler"]["priv"])
        await _insert(conn, s11_id, s11_payload, s11_sig, "ec-assembler", True)
        results["ec11_missing_reference"] = s11_id

        # ── SCENARIO 12: CYCLE (artificially injected) ────────────────────────
        # Three attestations forming A→B→C→A.
        # Content-addressed IDs can't form real cycles, so we inject with fake IDs.
        # The sig_valid=False flags are appropriate since IDs don't match payloads.
        # Expected anomaly: cycle
        cy_a_id = fake_id("cycle-A")
        cy_b_id = fake_id("cycle-B")
        cy_c_id = fake_id("cycle-C")

        cy_a_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-12a: Cycle Node A (references C)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [cy_c_id],   # A depends on C
            "materials": [{"name": "Cyclic material A", "cost_cad": 100.0, "country": "CA"}],
            "labour": {"cost_cad": 50.0, "country": "CA"},
            "transformation_description": "Cycle node A",
        }
        cy_b_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-12b: Cycle Node B (references A)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [cy_a_id],   # B depends on A
            "materials": [{"name": "Cyclic material B", "cost_cad": 100.0, "country": "CA"}],
            "labour": {"cost_cad": 50.0, "country": "CA"},
            "transformation_description": "Cycle node B",
        }
        cy_c_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-12c: Cycle Node C (references B) — traverse from here",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [cy_b_id],   # C depends on B → forms cycle C→B→A→C
            "materials": [{"name": "Cyclic material C", "cost_cad": 100.0, "country": "CA"}],
            "labour": {"cost_cad": 50.0, "country": "CA"},
            "transformation_description": "Cycle node C — start traversal here",
        }
        # Use a dummy sig — IDs are fake so sig_valid = False
        dummy_sig = "00" * 64
        await _insert(conn, cy_a_id, cy_a_payload, dummy_sig, "ec-ca-only", False)
        await _insert(conn, cy_b_id, cy_b_payload, dummy_sig, "ec-ca-only", False)
        await _insert(conn, cy_c_id, cy_c_payload, dummy_sig, "ec-ca-only", False)
        results["ec12_cycle_root"] = cy_c_id  # traverse from C to detect cycle

        # ── SCENARIO 13: DIAMOND DAG (double-counting test) ───────────────────
        # D is a shared base material used by both B and C.
        # A consumes both B and C.
        # D's costs must be counted ONCE, not twice.
        #
        #       A (final)
        #      / \
        #     B   C
        #      \ /
        #       D (shared raw material, $1000 CAD)
        #
        # If double-counted: D = $2000 → total = $3600, CA% inflated
        # Correct: D = $1000 → total = $2600, CA% correct
        #
        # Expected: correct single-counting; 0 anomalies (valid chain)
        d_payload = {
            "supplier_id": "ec-diamond",
            "product_name": "EC-13d: Diamond Base — Shared Raw Material",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "MB"},
            "inputs": [],
            "materials": [
                {"name": "Rare earth ore (Canadian)", "cost_cad": 1000.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 0.0, "country": "CA"},
            "transformation_description": "Mining of Canadian rare earth — shared upstream input",
            "quantity_produced": 100.0,
            "unit": "kg",
        }
        d_id = content_addressed_id(d_payload)
        d_sig = sign_payload(d_payload, suppliers["ec-diamond"]["priv"])
        await _insert(conn, d_id, d_payload, d_sig, "ec-diamond", True)

        b_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-13b: Diamond Left Branch",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [d_id],
            "materials": [
                {"name": "Canadian coating", "cost_cad": 300.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 200.0, "country": "CA"},
            "transformation_description": "Left branch processing using shared base",
            "quantity_produced": 50.0,
            "unit": "kg",
        }
        b_id = content_addressed_id(b_payload)
        b_sig = sign_payload(b_payload, suppliers["ec-mixed"]["priv"])
        await _insert(conn, b_id, b_payload, b_sig, "ec-mixed", True)

        c_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-13c: Diamond Right Branch",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [d_id],
            "materials": [
                {"name": "US processing additive", "cost_cad": 100.0, "country": "US"},
            ],
            "labour": {"cost_cad": 300.0, "country": "CA"},
            "transformation_description": "Right branch processing using shared base",
            "quantity_produced": 50.0,
            "unit": "kg",
        }
        c_id = content_addressed_id(c_payload)
        c_sig = sign_payload(c_payload, suppliers["ec-mixed"]["priv"])
        await _insert(conn, c_id, c_payload, c_sig, "ec-mixed", True)

        a_payload = {
            "supplier_id": "ec-assembler",
            "product_name": "EC-13: Diamond DAG Final Product (D counted once)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "BC"},
            "inputs": [b_id, c_id],   # both branches, both reference D
            "materials": [
                {"name": "Canadian packaging", "cost_cad": 50.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 150.0, "country": "CA"},
            "transformation_description": "Final integration — diamond DAG topology",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        a_id = content_addressed_id(a_payload)
        a_sig = sign_payload(a_payload, suppliers["ec-assembler"]["priv"])
        await _insert(conn, a_id, a_payload, a_sig, "ec-assembler", True)
        results["ec13_diamond_dag"] = a_id

        # ── SCENARIO 14: MISSING REQUIRED FIELD (location.country) ───────────
        # Expected anomaly: missing_field
        s14_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-14: Missing location.country",
            "timestamp": now_iso(),
            "location": {"province": "ON"},   # ← country absent
            "inputs": [],
            "materials": [
                {"name": "Canadian widget", "cost_cad": 200.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Missing location country field",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s14_id = content_addressed_id(s14_payload)
        s14_sig = sign_payload(s14_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s14_id, s14_payload, s14_sig, "ec-ca-only", True)
        results["ec14_missing_location_country"] = s14_id

        # ── SCENARIO 15: ZERO-COST ATTESTATION ───────────────────────────────
        # No materials and no labour — total cost = $0.
        # Expected: 0% Canadian (division by zero guard), no crash
        s15_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-15: Zero-Cost Attestation (no materials, no labour)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [],
            "labour": None,
            "transformation_description": "Consultation / design step with no material costs",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s15_id = content_addressed_id(s15_payload)
        s15_sig = sign_payload(s15_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s15_id, s15_payload, s15_sig, "ec-ca-only", True)
        results["ec15_zero_cost"] = s15_id

        # ── SCENARIO 16: CROSS-CHAIN REUSE (double-spend) ────────────────────
        # The same base component attestation (specific batch) is used as input
        # by two different final products — quantity double-spend.
        # Both final products claim to have consumed the single batch.
        s16_base_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-16b: Single Batch of 1 Unit",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Specialized alloy batch", "cost_cad": 500.0, "country": "CA",
                 "quantity": 1.0, "unit": "unit"},
            ],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Produced exactly 1 unit of specialized alloy",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s16_base_id = content_addressed_id(s16_base_payload)
        s16_base_sig = sign_payload(s16_base_payload, suppliers["ec-ca-only"]["priv"])
        await _insert(conn, s16_base_id, s16_base_payload, s16_base_sig, "ec-ca-only", True)

        # Product X consumes the batch (legitimate)
        s16_x_payload = {
            "supplier_id": "ec-assembler",
            "product_name": "EC-16x: Product X (legitimate use of batch)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "BC"},
            "inputs": [s16_base_id],
            "materials": [
                {"name": "Casing", "cost_cad": 100.0, "country": "CA",
                 "quantity": 1.0, "unit": "unit"},
            ],
            "labour": {"cost_cad": 150.0, "country": "CA"},
            "transformation_description": "Product X using the single alloy batch",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s16_x_id = content_addressed_id(s16_x_payload)
        s16_x_sig = sign_payload(s16_x_payload, suppliers["ec-assembler"]["priv"])
        await _insert(conn, s16_x_id, s16_x_payload, s16_x_sig, "ec-assembler", True)
        results["ec16x_reuse_product_x"] = s16_x_id

        # Product Y also consumes the SAME batch — double-spend!
        s16_y_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-16y: Product Y (cross-chain reuse — same batch)",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [s16_base_id],   # ← same base_id as Product X
            "materials": [
                {"name": "Extra casing", "cost_cad": 80.0, "country": "CA",
                 "quantity": 1.0, "unit": "unit"},  # ← also claims 1 unit from same batch
            ],
            "labour": {"cost_cad": 120.0, "country": "CA"},
            "transformation_description": "Product Y fraudulently reusing the same alloy batch",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        s16_y_id = content_addressed_id(s16_y_payload)
        s16_y_sig = sign_payload(s16_y_payload, suppliers["ec-mixed"]["priv"])
        await _insert(conn, s16_y_id, s16_y_payload, s16_y_sig, "ec-mixed", True)
        results["ec16y_reuse_product_y"] = s16_y_id

        # ── SCENARIO 17: MULTI-TIER VALID CHAIN (deep, all Canadian) ─────────
        # 4-tier chain: raw ore → ingot → plate → finished part
        # All Canadian, validates DAG traversal across multiple hops
        # Expected: high Canadian %, "Product of Canada", no anomalies
        tier1_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-17a: Tier 1 — Canadian Iron Ore",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [{"name": "Raw iron ore", "cost_cad": 200.0, "country": "CA"}],
            "labour": {"cost_cad": 100.0, "country": "CA"},
            "transformation_description": "Mining and crushing of Canadian iron ore",
            "quantity_produced": 1000.0, "unit": "kg",
        }
        t1_id = content_addressed_id(tier1_payload)
        await _insert(conn, t1_id, tier1_payload,
                      sign_payload(tier1_payload, suppliers["ec-ca-only"]["priv"]),
                      "ec-ca-only", True)

        tier2_payload = {
            "supplier_id": "ec-ca-only",
            "product_name": "EC-17b: Tier 2 — Steel Ingot",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [t1_id],
            "materials": [{"name": "Alloying agents", "cost_cad": 80.0, "country": "CA"}],
            "labour": {"cost_cad": 150.0, "country": "CA"},
            "transformation_description": "Smelting ore into steel ingots",
            "quantity_produced": 500.0, "unit": "kg",
        }
        t2_id = content_addressed_id(tier2_payload)
        await _insert(conn, t2_id, tier2_payload,
                      sign_payload(tier2_payload, suppliers["ec-ca-only"]["priv"]),
                      "ec-ca-only", True)

        tier3_payload = {
            "supplier_id": "ec-mixed",
            "product_name": "EC-17c: Tier 3 — Rolled Steel Plate",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [t2_id],
            "materials": [{"name": "Protective coating", "cost_cad": 40.0, "country": "CA"}],
            "labour": {"cost_cad": 200.0, "country": "CA"},
            "transformation_description": "Hot-rolling and coating of steel plate",
            "quantity_produced": 100.0, "unit": "kg",
        }
        t3_id = content_addressed_id(tier3_payload)
        await _insert(conn, t3_id, tier3_payload,
                      sign_payload(tier3_payload, suppliers["ec-mixed"]["priv"]),
                      "ec-mixed", True)

        tier4_payload = {
            "supplier_id": "ec-assembler",
            "product_name": "EC-17: 4-Tier Deep Chain — Machined Structural Part",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "BC"},
            "inputs": [t3_id],
            "materials": [{"name": "Cutting fluid", "cost_cad": 30.0, "country": "CA"}],
            "labour": {"cost_cad": 400.0, "country": "CA"},
            "transformation_description": "CNC machining of steel plate into structural part",
            "quantity_produced": 10.0, "unit": "unit",
        }
        t4_id = content_addressed_id(tier4_payload)
        await _insert(conn, t4_id, tier4_payload,
                      sign_payload(tier4_payload, suppliers["ec-assembler"]["priv"]),
                      "ec-assembler", True)
        results["ec17_deep_4tier_chain"] = t4_id

    return {
        "edge_cases": results,
        "descriptions": {
            "ec01_all_canadian_100pct":        "100% Canadian costs, CA location → Product of Canada",
            "ec02_all_foreign_0pct":           "0% Canadian costs, US location → Not Qualified (valid chain)",
            "ec03_exactly_98pct":              "Exactly 98.0% Canadian → Product of Canada",
            "ec04_below_98pct":                "97.9% Canadian → Made in Canada (just misses Product of Canada)",
            "ec05_exactly_51pct":              "Exactly 51.0% Canadian → Made in Canada",
            "ec06_below_51pct":                "50.9% Canadian → Not Qualified (just misses Made in Canada)",
            "ec07_not_in_canada_last_transform": "High Canadian costs but final assembly in US → Not Qualified",
            "ec08_tampered_payload":           "Payload mutated post-signing → invalid_signature anomaly",
            "ec09_unknown_signer":             "Signer not in registry → unknown_signer anomaly",
            "ec10_quantity_fraud":             "Downstream claims 10 units, upstream produced 2 → quantity_fraud",
            "ec11_missing_reference":          "Input references non-existent attestation ID → missing_reference",
            "ec12_cycle_root":                 "Artificially injected A→B→C→A cycle → cycle anomaly",
            "ec13_diamond_dag":                "A→B, A→C, both B&C→D — D costs counted exactly once",
            "ec14_missing_location_country":   "location.country absent → missing_field anomaly",
            "ec15_zero_cost":                  "No materials, no labour — division-by-zero guard",
            "ec16x_reuse_product_x":           "Product X — legitimate first use of shared batch",
            "ec16y_reuse_product_y":           "Product Y — cross-chain reuse of same batch (double-spend)",
            "ec17_deep_4tier_chain":           "4-tier deep DAG traversal — all Canadian, valid",
        },
    }
