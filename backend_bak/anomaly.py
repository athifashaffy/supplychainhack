"""
Anomaly detection for attestation chains.

Checks:
  1. Signature validity (per-attestation)
  2. Unknown signer (not in registry)
  3. Missing required fields
  4. Negative costs (material or labour cost_cad < 0)
  5. Quantity fraud (consumed > upstream produced) — via QuantityLedger (UTXO model)
  6. Timestamp ordering (upstream timestamp must not be later than downstream)
  7. Replay/reuse (same attestation in multiple product chains)
  8. Cycle / missing reference (handled in graph.py, passed in as pre-detected)
"""

from __future__ import annotations
from datetime import datetime, timezone
from crypto import verify_signature
from ledger import QuantityLedger


REQUIRED_FIELDS = ["supplier_id", "product_name", "timestamp", "location"]


def _parse_ts(ts: str) -> datetime | None:
    """Parse ISO 8601 timestamp, return None on failure."""
    if not ts:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def check_missing_fields(att_id: str, payload: dict) -> list[dict]:
    anomalies = []
    for field in REQUIRED_FIELDS:
        if field not in payload or payload[field] is None:
            anomalies.append({
                "type": "missing_field",
                "attestation_id": att_id,
                "detail": f"Required field '{field}' is missing or null",
            })
    loc = payload.get("location") or {}
    if not loc.get("country"):
        anomalies.append({
            "type": "missing_field",
            "attestation_id": att_id,
            "detail": "location.country is missing",
        })
    return anomalies


def check_negative_costs(att_id: str, payload: dict) -> list[dict]:
    """Flag any material or labour cost_cad that is negative."""
    anomalies = []
    for mat in payload.get("materials") or []:
        cost = mat.get("cost_cad")
        if cost is not None and float(cost) < 0:
            anomalies.append({
                "type": "invalid_cost",
                "attestation_id": att_id,
                "detail": (
                    f"Material '{mat.get('name', '?')}' has negative cost_cad "
                    f"({cost}) — possible cost manipulation"
                ),
            })
    labour = payload.get("labour") or {}
    if labour:
        cost = labour.get("cost_cad")
        if cost is not None and float(cost) < 0:
            anomalies.append({
                "type": "invalid_cost",
                "attestation_id": att_id,
                "detail": f"Labour has negative cost_cad ({cost}) — possible cost manipulation",
            })
    return anomalies


def check_timestamp_ordering(chain_nodes: list[dict]) -> list[dict]:
    """
    Detect impossible timestamp orderings: an upstream (input) attestation
    whose timestamp is strictly later than the downstream attestation that
    consumed it. A supplier cannot have produced something before it existed.
    """
    anomalies = []
    id_to_ts: dict[str, datetime | None] = {}

    for node in chain_nodes:
        ts_str = node["payload"].get("timestamp", "")
        id_to_ts[node["id"]] = _parse_ts(ts_str)

    for node in chain_nodes:
        downstream_ts = id_to_ts.get(node["id"])
        if downstream_ts is None:
            continue
        for input_id in node["payload"].get("inputs") or []:
            upstream_ts = id_to_ts.get(input_id)
            if upstream_ts is None:
                continue
            if upstream_ts > downstream_ts:
                anomalies.append({
                    "type": "timestamp_ordering",
                    "attestation_id": node["id"],
                    "detail": (
                        f"Upstream attestation {input_id[:12]}... has timestamp "
                        f"{upstream_ts.isoformat()} which is later than downstream "
                        f"attestation timestamp {downstream_ts.isoformat()} — "
                        "impossible physical ordering"
                    ),
                })
    return anomalies


def check_signature(att_id: str, payload: dict, signature: str, public_key_hex: str) -> list[dict]:
    if not verify_signature(payload, signature, public_key_hex):
        return [{
            "type": "invalid_signature",
            "attestation_id": att_id,
            "detail": "Ed25519 signature verification failed — payload may have been tampered",
        }]
    return []



async def check_cross_chain_reuse(chain_nodes: list[dict], pool) -> list[dict]:
    """
    Detect replay/reuse: any attestation in this chain also consumed by a
    different chain (same input_id referenced by two distinct parent attestations
    that are NOT both in this chain).
    """
    import asyncpg
    chain_ids = {n["id"] for n in chain_nodes}
    anomalies = []

    async with pool.acquire() as conn:
        for node in chain_nodes:
            node_id = node["id"]
            # Find all attestations that list node_id as an input
            rows = await conn.fetch(
                "SELECT attestation_id FROM attestation_inputs WHERE input_id = $1",
                node_id,
            )
            consumers = {r["attestation_id"] for r in rows}
            # If any consumer is outside our current chain → double-spend
            outside = consumers - chain_ids
            if outside:
                anomalies.append({
                    "type": "cross_chain_reuse",
                    "attestation_id": node_id,
                    "detail": (
                        f"Attestation {node_id[:12]}... is also consumed by "
                        f"{len(outside)} other chain(s): "
                        + ", ".join(x[:12] + "..." for x in outside)
                    ),
                })

    return anomalies


async def run_all_checks(chain_nodes: list[dict], pool, structural_anomalies: list[dict]) -> list[dict]:
    """Run all anomaly checks on a resolved chain."""
    from registry import get_supplier

    anomalies = list(structural_anomalies)  # carry in cycle/missing-ref anomalies

    for record in chain_nodes:
        att_id = record["id"]
        payload = record["payload"]
        signature = record["signature"]
        signer_id = record["signer_id"]

        # 1. Missing fields
        anomalies.extend(check_missing_fields(att_id, payload))

        # 2. Negative costs
        anomalies.extend(check_negative_costs(att_id, payload))

        # 3. Check signer in registry
        supplier = await get_supplier(pool, signer_id)
        if supplier is None:
            anomalies.append({
                "type": "unknown_signer",
                "attestation_id": att_id,
                "detail": f"Signer '{signer_id}' not found in supplier registry",
            })
            continue  # can't verify signature without public key

        # 4. Signature validity
        anomalies.extend(
            check_signature(att_id, payload, signature, supplier["public_key_hex"])
        )

    # 5. Quantity fraud across DAG — UTXO-style ledger (correct multi-input accounting)
    ledger = QuantityLedger()
    ledger.ingest_chain(chain_nodes)
    anomalies.extend(ledger.violations())

    # 6. Timestamp ordering — upstream must not post-date its downstream consumer
    anomalies.extend(check_timestamp_ordering(chain_nodes))

    # 7. Cross-chain reuse (replay / double-spend)
    anomalies.extend(await check_cross_chain_reuse(chain_nodes, pool))

    return anomalies
