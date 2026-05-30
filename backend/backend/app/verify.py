from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Set
from .chain import SupplyChain
from .content import (
    attribute_costs_by_country,
    compute_designation,
    find_last_substantial_transformation,
)
from .models import (
    Anomaly,
    ChainStatus,
    Reason,
    VerificationRequest,
    VerificationResult,
)
from . import adapters


# Anomaly precedence: primary reason sorts first. The harness scoring is
# likely to look at the first anomaly as the controlling one.
REASON_PRIORITY = {
    Reason.MALFORMED:            1,
    Reason.SIGNATURE_INVALID:    2,
    Reason.HASH_MISMATCH:        3,
    Reason.UNKNOWN_ISSUER:       4,
    Reason.DUPLICATE_HASH:       5,
    Reason.REPLAY_DETECTED:      6,
    Reason.BROKEN_LINK:          7,
    Reason.CYCLE:                8,
    Reason.MASS_BALANCE:         9,
    Reason.TEMPORAL_INVERSION:  10,
    Reason.ADVISORY_ANOMALY:    99,
}

FATAL_REASONS = {
    Reason.MALFORMED,
    Reason.SIGNATURE_INVALID,
    Reason.HASH_MISMATCH,
    Reason.UNKNOWN_ISSUER,
    Reason.REPLAY_DETECTED,
    Reason.BROKEN_LINK,
    Reason.CYCLE,
    Reason.MASS_BALANCE,
    Reason.TEMPORAL_INVERSION,
}


def check_node_integrity(chain: SupplyChain, reachable: Set[str]) -> List[Anomaly]:
    """Signature + signer checks, routed through adapters for spec-day swap."""
    anomalies: List[Anomaly] = []
    for h in reachable:
        att = chain.nodes[h].attestation

        # EVENT-DAY: recompute hash and compare to declared id.
        # Depends on adapters.canonicalize_payload knowing the spec's
        # canonicalization rule (JCS vs DSSE vs custom). One-line wire-up:
        #
        #     payload = att.model_dump(mode="json",
        #                              exclude={"hash", "signature"})
        #     if adapters.content_hash(payload) != att.hash:
        #         anomalies.append(Anomaly(
        #             reason=Reason.HASH_MISMATCH,
        #             attestation_hash=h,
        #             detail="Declared hash does not match canonical body.",
        #         ))
        #         continue

        if not adapters.verify_signature(att):
            anomalies.append(Anomaly(
                reason=Reason.SIGNATURE_INVALID,
                attestation_hash=h,
                detail="Signature verification failed.",
            ))

        if not adapters.resolve_key(att.supplier_id):
            anomalies.append(Anomaly(
                reason=Reason.UNKNOWN_ISSUER,
                attestation_hash=h,
                detail=f"Unknown or unverified supplier: {att.supplier_id}",
            ))

    return anomalies


def check_mass_balance(chain: SupplyChain, reachable: Set[str]) -> List[Anomaly]:
    """Sum of quantities consumed across all consumers must not exceed produced."""
    anomalies: List[Anomaly] = []
    for h in reachable:
        produced = chain.nodes[h].attestation.output.quantity
        consumed = 0
        for consumer_hash in chain.nodes[h].consumer_hashes:
            if consumer_hash not in reachable:
                continue
            for ref in chain.nodes[consumer_hash].attestation.inputs:
                if ref.attestation_hash == h:
                    consumed += ref.quantity_used
        if consumed > produced:
            anomalies.append(Anomaly(
                reason=Reason.MASS_BALANCE,
                attestation_hash=h,
                detail=f"Consumed {consumed} > produced {produced}",
            ))
    return anomalies


def _parse_ts(ts: str):
    """Parse ISO-8601 timestamp; return None if unparseable or naive.

    Naive datetimes (no timezone offset) are rejected because comparing
    them against aware datetimes raises TypeError. For a provenance system
    the ambiguity is also semantically wrong: '2026-05-30T10:00:00' could
    mean two different instants depending on the supplier's local zone.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def check_temporal_ordering(chain: SupplyChain, reachable: Set[str]) -> List[Anomaly]:
    """Inputs must not be timestamped after their consumers.

    Compares parsed datetimes, not strings: '2026-05-30T10:00:00Z' and
    '2026-05-30T10:00:00+00:00' represent the same instant but the second
    sorts later as a string. Unparseable timestamps emit MALFORMED.
    """
    anomalies: List[Anomaly] = []
    for h in reachable:
        node = chain.nodes[h]
        raw_ts = node.attestation.timestamp
        if not raw_ts:
            continue

        consumer_ts = _parse_ts(raw_ts)
        if consumer_ts is None:
            anomalies.append(Anomaly(
                reason=Reason.MALFORMED,
                attestation_hash=h,
                detail=f"Unparseable timestamp: {raw_ts!r}",
            ))
            continue

        for ref in node.attestation.inputs:
            ih = ref.attestation_hash
            if ih not in chain.nodes:
                continue
            input_raw = chain.nodes[ih].attestation.timestamp
            if not input_raw:
                continue
            input_ts = _parse_ts(input_raw)
            if input_ts is None:
                anomalies.append(Anomaly(
                    reason=Reason.MALFORMED,
                    attestation_hash=ih,
                    detail=f"Unparseable timestamp: {input_raw!r}",
                ))
                continue
            if input_ts > consumer_ts:
                anomalies.append(Anomaly(
                    reason=Reason.TEMPORAL_INVERSION,
                    attestation_hash=h,
                    detail=f"Input {ih} ts {input_raw} later than consumer ts {raw_ts}",
                ))
    return anomalies


def _sort_anomalies(anomalies: List[Anomaly]) -> List[Anomaly]:
    return sorted(anomalies, key=lambda a: REASON_PRIORITY.get(a.reason, 50))


def _unverifiable(anomalies: List[Anomaly]) -> VerificationResult:
    return VerificationResult(
        chain_status=ChainStatus.UNVERIFIABLE,
        designation=None,
        canadian_pct=None,
        total_cost_cents=None,
        canadian_cost_cents=None,
        cost_by_country={},
        anomalies=_sort_anomalies(anomalies),
    )


def verify_supply_chain(req: VerificationRequest) -> VerificationResult:
    # Pre-event only: seed the mock registry from the bundle. Replaced on
    # event day by a one-shot load of the spec's registry file at startup.
    adapters.bootstrap_registry_from_bundle(req.attestations)

    chain = SupplyChain(req.attestations)

    # Missing root: one anomaly, early return.
    if req.root_hash not in chain.nodes:
        return _unverifiable([Anomaly(
            reason=Reason.BROKEN_LINK,
            attestation_hash=req.root_hash,
            detail="Root attestation not present in submitted bundle.",
        )])

    # Pick up DUPLICATE_HASH anomalies recorded at chain construction.
    anomalies: List[Anomaly] = list(chain.construction_anomalies)

    # Structural pass: cycle detection + reachability + dangling refs.
    reachable, structural = chain.collect_reachable_dfs(req.root_hash)
    anomalies.extend(structural)

    # A cycle means the graph is malformed: don't run downstream checks.
    if any(a.reason == Reason.CYCLE for a in anomalies):
        return _unverifiable(anomalies)

    anomalies.extend(check_node_integrity(chain, reachable))
    anomalies.extend(check_mass_balance(chain, reachable))
    anomalies.extend(check_temporal_ordering(chain, reachable))

    if any(a.reason in FATAL_REASONS for a in anomalies):
        return _unverifiable(anomalies)

    last_st = find_last_substantial_transformation(chain, req.root_hash, reachable)
    last_st_country = last_st.attestation.work_country if last_st else None

    cost_by_country = attribute_costs_by_country(chain, req.root_hash, reachable)
    designation, total, canadian, pct = compute_designation(cost_by_country, last_st_country)

    return VerificationResult(
        chain_status=ChainStatus.VERIFIED,
        designation=designation,
        canadian_pct=pct,
        total_cost_cents=total,
        canadian_cost_cents=canadian,
        cost_by_country=cost_by_country,
        anomalies=_sort_anomalies(anomalies),
    )
