"""
Typed exception hierarchy for the Supply Chain Provenance system.

These are raised internally and mapped to HTTP responses by the
FastAPI exception handlers registered in main.py.
"""


class ProvisioningError(Exception):
    """Base class for all supply-chain domain errors."""


# ── Cryptographic integrity ───────────────────────────────────────────────────

class IntegrityViolationError(ProvisioningError):
    """
    Raised when an inbound attestation fails cryptographic verification.

    Possible causes:
      - Payload was mutated after signing (signature mismatch)
      - Signer is not in the verified supplier registry
      - Envelope fields (signer_id, payload.supplier_id) are inconsistent

    Maps to HTTP 422 Unprocessable Entity.
    """

    def __init__(self, detail: str, attestation_id: str | None = None):
        self.detail = detail
        self.attestation_id = attestation_id
        super().__init__(detail)

    def to_dict(self) -> dict:
        return {
            "error": "IntegrityViolationError",
            "detail": self.detail,
            "attestation_id": self.attestation_id,
        }


# ── DAG structural anomalies ──────────────────────────────────────────────────

class StructuralAnomalyError(ProvisioningError):
    """
    Raised when DAG traversal encounters a structural defect.

    Structural defects include:
      - Cycles: a supplier lists a downstream node as its own input
      - Missing references: an input attestation ID does not exist in the store
      - Impossible orderings: timestamps contradict the declared dependency graph

    These do not abort the entire analysis — collect_chain_graceful() continues
    traversal on unaffected branches and accumulates these as anomaly records.
    """

    def __init__(self, anomaly_type: str, node_id: str, detail: str):
        self.anomaly_type = anomaly_type
        self.node_id = node_id
        self.detail = detail
        super().__init__(detail)

    def to_anomaly_dict(self) -> dict:
        return {
            "type": self.anomaly_type,
            "attestation_id": self.node_id,
            "detail": self.detail,
        }


# ── Quantity / double-spend ───────────────────────────────────────────────────

class QuantityInconsistencyError(ProvisioningError):
    """
    Raised when one or more downstream nodes collectively claim to consume
    more of a material batch than the upstream node declared it produced.

    This is analogous to a UTXO double-spend: the upstream attestation is the
    "unspent output" and each downstream reference is a "spend".  If the total
    spend exceeds the available output, the chain is fraudulent.

    Attributes:
        upstream_id:  attestation ID of the producer node
        produced:     quantity the upstream node declared it produced
        consumed:     total quantity claimed by all downstream consumers
        consumers:    list of attestation IDs making the excess claim
    """

    def __init__(
        self,
        upstream_id: str,
        produced: float,
        consumed: float,
        consumers: list[str],
    ):
        self.upstream_id = upstream_id
        self.produced = produced
        self.consumed = consumed
        self.consumers = consumers
        super().__init__(
            f"Quantity inconsistency: {upstream_id[:12]}... produced {produced} "
            f"but {consumed} was consumed by {len(consumers)} node(s)"
        )

    def to_anomaly_dict(self) -> dict:
        return {
            "type": "quantity_inconsistency",
            "attestation_id": self.upstream_id,
            "detail": (
                f"Produced {self.produced} units, but downstream nodes claim "
                f"{self.consumed} units consumed (excess: {self.consumed - self.produced:.2f})"
            ),
            "produced": self.produced,
            "consumed": self.consumed,
            "consumers": [c[:16] + "..." for c in self.consumers],
        }


# ── Replay / contextual reuse ─────────────────────────────────────────────────

class ReplayViolationError(ProvisioningError):
    """
    Raised when an attestation that belongs to one product's supply chain
    is detected as an input in a different product's chain.

    Example: Drone A's aluminum batch attestation injected into Drone B's chain
    to fraudulently inflate Drone B's Canadian content percentage.

    Attributes:
        attestation_id: the replayed attestation
        original_chain: root product ID of the chain the attestation belongs to
        injected_into:  root product ID of the chain attempting the replay
    """

    def __init__(
        self,
        attestation_id: str,
        original_chain: str | None,
        injected_into: str,
    ):
        self.attestation_id = attestation_id
        self.original_chain = original_chain
        self.injected_into = injected_into
        super().__init__(
            f"Replay: attestation {attestation_id[:12]}... already belongs to chain "
            f"{(original_chain or 'unknown')[:12]}..., injected into {injected_into[:12]}..."
        )

    def to_anomaly_dict(self) -> dict:
        return {
            "type": "cross_chain_reuse",
            "attestation_id": self.attestation_id,
            "detail": (
                f"Attestation {self.attestation_id[:16]}... is being replayed across "
                f"product chains — possible lineage fraud"
            ),
            "original_chain": self.original_chain,
            "injected_into": self.injected_into,
        }
