"""
Seed data: realistic Canadian drone supply chain with Ed25519 keys.
Suppliers span CA (ON, BC, QC) and US for mixed Canadian content scenarios.
"""

import asyncpg
from crypto import generate_keypair, sign_payload, content_addressed_id
from registry import register_supplier
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Generate keypairs for seed suppliers (deterministic-ish for demo)
SUPPLIERS = [
    {
        "supplier_id": "sup-001",
        "name": "Boreal Composites Ltd.",
        "country": "CA",
        "province": "ON",
    },
    {
        "supplier_id": "sup-002",
        "name": "Pacific Drone Systems Inc.",
        "country": "CA",
        "province": "BC",
    },
    {
        "supplier_id": "sup-003",
        "name": "Montréal Avionics SENC",
        "country": "CA",
        "province": "QC",
    },
    {
        "supplier_id": "sup-004",
        "name": "US Semiconductor Corp",
        "country": "US",
        "province": None,
    },
    {
        "supplier_id": "sup-005",
        "name": "Northern Propulsion Labs",
        "country": "CA",
        "province": "AB",
    },
]


async def seed(pool: asyncpg.Pool) -> dict:
    """Register suppliers and create a sample attestation chain. Returns keypairs."""
    keypairs: dict[str, tuple[str, str]] = {}

    # Register suppliers + store keypairs
    for sup in SUPPLIERS:
        priv_hex, pub_hex = generate_keypair()
        keypairs[sup["supplier_id"]] = (priv_hex, pub_hex)
        await register_supplier(
            pool,
            supplier_id=sup["supplier_id"],
            name=sup["name"],
            country=sup["country"],
            province=sup["province"],
            public_key_hex=pub_hex,
        )

    # Build attestation chain: raw materials -> subassembly -> final product
    async with pool.acquire() as conn:
        # --- Layer 1: Raw material attestations ---

        # Carbon fibre frame (Canadian)
        carbon_payload = {
            "supplier_id": "sup-001",
            "product_name": "Carbon Fibre Drone Frame",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "ON"},
            "inputs": [],
            "materials": [
                {"name": "Raw carbon fibre", "cost_cad": 800.0, "country": "CA", "quantity": 5.0, "unit": "kg"},
                {"name": "Epoxy resin", "cost_cad": 120.0, "country": "CA", "quantity": 2.0, "unit": "L"},
            ],
            "labour": {"cost_cad": 650.0, "country": "CA", "hours": 8.0},
            "transformation_description": "Lay-up and cure carbon fibre composite frame",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        carbon_id = content_addressed_id(carbon_payload)
        carbon_sig = sign_payload(carbon_payload, keypairs["sup-001"][0])

        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
        """, carbon_id, str(carbon_payload).replace("'", '"'), carbon_sig, "sup-001", True)

        import json
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
        """, carbon_id, json.dumps(carbon_payload), carbon_sig, "sup-001", True)

        # Flight controller chips (US-sourced)
        chips_payload = {
            "supplier_id": "sup-004",
            "product_name": "Flight Controller Microchips",
            "timestamp": now_iso(),
            "location": {"country": "US", "province": None},
            "inputs": [],
            "materials": [
                {"name": "Silicon wafers", "cost_cad": 400.0, "country": "US", "quantity": 10.0, "unit": "unit"},
            ],
            "labour": {"cost_cad": 300.0, "country": "US", "hours": 5.0},
            "transformation_description": "Fab and test flight controller ASICs",
            "quantity_produced": 4.0,
            "unit": "unit",
        }
        chips_id = content_addressed_id(chips_payload)
        chips_sig = sign_payload(chips_payload, keypairs["sup-004"][0])
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
        """, chips_id, json.dumps(chips_payload), chips_sig, "sup-004", True)

        # Propulsion motors (Canadian)
        motor_payload = {
            "supplier_id": "sup-005",
            "product_name": "Brushless Motor Assembly",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "AB"},
            "inputs": [],
            "materials": [
                {"name": "Copper windings", "cost_cad": 180.0, "country": "CA", "quantity": 0.5, "unit": "kg"},
                {"name": "Neodymium magnets", "cost_cad": 90.0, "country": "CA", "quantity": 0.1, "unit": "kg"},
            ],
            "labour": {"cost_cad": 240.0, "country": "CA", "hours": 3.0},
            "transformation_description": "Wind, assemble and test BLDC motors",
            "quantity_produced": 4.0,
            "unit": "unit",
        }
        motor_id = content_addressed_id(motor_payload)
        motor_sig = sign_payload(motor_payload, keypairs["sup-005"][0])
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
        """, motor_id, json.dumps(motor_payload), motor_sig, "sup-005", True)

        # --- Layer 2: Avionics subassembly (Montreal) ---
        avionics_payload = {
            "supplier_id": "sup-003",
            "product_name": "Drone Avionics Module",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "QC"},
            "inputs": [chips_id, motor_id],
            "materials": [
                {"name": "PCB substrate", "cost_cad": 95.0, "country": "CA"},
                {"name": "Connectors and passives", "cost_cad": 45.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 520.0, "country": "CA", "hours": 6.5},
            "transformation_description": "Integrate chips and sensors onto avionics PCB; mount motors",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        avionics_id = content_addressed_id(avionics_payload)
        avionics_sig = sign_payload(avionics_payload, keypairs["sup-003"][0])
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
        """, avionics_id, json.dumps(avionics_payload), avionics_sig, "sup-003", True)

        # --- Layer 3: Final drone assembly (Pacific Drone Systems, BC) ---
        drone_payload = {
            "supplier_id": "sup-002",
            "product_name": "Aurora-X Surveillance Drone",
            "timestamp": now_iso(),
            "location": {"country": "CA", "province": "BC"},
            "inputs": [carbon_id, avionics_id],
            "materials": [
                {"name": "Landing gear hardware", "cost_cad": 60.0, "country": "CA"},
                {"name": "Cable harness", "cost_cad": 40.0, "country": "CA"},
            ],
            "labour": {"cost_cad": 980.0, "country": "CA", "hours": 12.0},
            "transformation_description": "Final integration, calibration and QA of complete drone system",
            "quantity_produced": 1.0,
            "unit": "unit",
        }
        drone_id = content_addressed_id(drone_payload)
        drone_sig = sign_payload(drone_payload, keypairs["sup-002"][0])
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
        """, drone_id, json.dumps(drone_payload), drone_sig, "sup-002", True)

    return {
        "suppliers": [s["supplier_id"] for s in SUPPLIERS],
        "attestation_ids": {
            "carbon_frame": carbon_id,
            "flight_chips": chips_id,
            "motors": motor_id,
            "avionics": avionics_id,
            "final_drone": drone_id,
        },
        "keypairs": {sid: {"private_hex": kp[0], "public_hex": kp[1]} for sid, kp in keypairs.items()},
    }
