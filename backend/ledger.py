"""
Quantity Ledger — Double-Spend Prevention for attestation DAGs.

Tracks every declared production and consumption of material quantities
across the full supply chain graph.  Analogous to Bitcoin's UTXO model:

  - Each attestation that specifies quantity_produced is an "unspent output"
  - Each downstream attestation that references it as an input and claims
    materials with quantities is a "spend"
  - If the total spend across ALL consumers exceeds the declared output,
    a QuantityInconsistencyError is raised

Usage
-----
    ledger = QuantityLedger()
    ledger.ingest_chain(chain_nodes)   # call once with all DAG nodes
    violations = ledger.violations()   # list[dict] of anomaly dicts

The returned violation dicts are in the same format as all other anomaly dicts
in the system (type, attestation_id, detail, ...) and can be passed directly
into the anomaly list returned by run_all_checks().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from exceptions import QuantityInconsistencyError

logger = logging.getLogger(__name__)


@dataclass
class _ProductionRecord:
    """What a single attestation node declared it produced."""
    att_id: str
    quantity: float
    unit: str | None = None
    product_name: str | None = None


@dataclass
class _ConsumptionRecord:
    """What a downstream node claims to have consumed from a specific upstream."""
    consumer_att_id: str
    upstream_att_id: str
    quantity: float


class QuantityLedger:
    """
    Quantity accounting ledger for a resolved attestation chain.

    Thread-safety: not required — one ledger instance per request.
    """

    def __init__(self) -> None:
        # att_id → production record
        self._produced: dict[str, _ProductionRecord] = {}
        # upstream_att_id → list of consumption records
        self._consumed: dict[str, list[_ConsumptionRecord]] = {}

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_chain(self, chain_nodes: list[dict]) -> None:
        """
        Walk the resolved chain (output of traverse_dag) and record all
        production and consumption claims.

        Call this once before calling violations().
        """
        for node in chain_nodes:
            self._record_node(node)

    def _record_node(self, node: dict) -> None:
        att_id: str = node["id"]
        payload: dict = node["payload"]

        # ── 1. What did this node produce? ────────────────────────────────────
        qty_produced = payload.get("quantity_produced")
        if qty_produced is not None:
            try:
                qty_f = float(qty_produced)
            except (TypeError, ValueError):
                logger.warning(
                    "Ledger: invalid quantity_produced '%s' in %s — skipped",
                    qty_produced, att_id[:12],
                )
                qty_f = None

            if qty_f is not None and qty_f >= 0:
                self._produced[att_id] = _ProductionRecord(
                    att_id=att_id,
                    quantity=qty_f,
                    unit=payload.get("unit"),
                    product_name=payload.get("product_name"),
                )

        # ── 2. What did this node claim to consume? ───────────────────────────
        #
        # Consumption is inferred from the materials list.  We attribute the
        # total consumed quantity to the first listed input (simplified model).
        # A more precise model would require explicit per-material provenance
        # fields in the attestation schema.
        inputs: list[str] = payload.get("inputs") or []
        if not inputs:
            return

        materials: list[dict] = payload.get("materials") or []
        total_consumed = sum(
            float(m["quantity"])
            for m in materials
            if m.get("quantity") is not None
        )

        if total_consumed <= 0:
            return

        # Distribute consumed quantity proportionally across declared inputs.
        # Proportional split is conservative: it under-counts per-input demand,
        # so violations are only flagged when the aggregate is clearly exceeded.
        per_input = total_consumed / len(inputs)
        for upstream_id in inputs:
            record = _ConsumptionRecord(
                consumer_att_id=att_id,
                upstream_att_id=upstream_id,
                quantity=per_input,
            )
            self._consumed.setdefault(upstream_id, []).append(record)
            logger.debug(
                "Ledger: %s consumes %.2f from %s",
                att_id[:12], per_input, upstream_id[:12],
            )

    # ── Violation detection ───────────────────────────────────────────────────

    def violations(self) -> list[dict]:
        """
        Return a list of anomaly dicts for every upstream attestation where
        total consumed quantity exceeds declared produced quantity.

        Returns an empty list if no violations are found.
        """
        result: list[dict] = []

        for upstream_id, consumption_records in self._consumed.items():
            production = self._produced.get(upstream_id)
            if production is None:
                # Upstream doesn't declare a quantity — no ledger constraint
                continue

            total_demanded = sum(r.quantity for r in consumption_records)
            if total_demanded <= production.quantity:
                continue

            # Build the exception for structured metadata, then convert to dict
            consumers = list({r.consumer_att_id for r in consumption_records})
            exc = QuantityInconsistencyError(
                upstream_id=upstream_id,
                produced=production.quantity,
                consumed=total_demanded,
                consumers=consumers,
            )
            anomaly = exc.to_anomaly_dict()

            # Enrich with product name if available
            if production.product_name:
                anomaly["product_name"] = production.product_name
            if production.unit:
                anomaly["unit"] = production.unit

            logger.warning(
                "Ledger violation: %s produced %.2f but %.2f consumed",
                upstream_id[:12], production.quantity, total_demanded,
            )
            result.append(anomaly)

        return result

    # ── Introspection helpers ─────────────────────────────────────────────────

    def summary(self) -> dict:
        """Return a debug summary of the ledger state."""
        return {
            "production_nodes": len(self._produced),
            "consumption_relationships": sum(
                len(v) for v in self._consumed.values()
            ),
            "tracked_upstreams_with_consumers": len(self._consumed),
        }
