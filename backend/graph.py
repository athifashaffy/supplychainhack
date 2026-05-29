"""
DAG traversal for attestation chains.

Exports two traversal modes:

  traverse_dag / collect_chain  (strict)
      Raise CycleError or MissingReferenceError on any structural defect.
      Used by the API when the root attestation must be fully verifiable.

  traverse_dag_graceful / collect_chain_graceful  (lenient)
      Continue past missing references — accumulate them as anomaly dicts
      and keep traversing reachable branches.  Only cycles are fatal (they
      represent an impossible physical reality and cannot be safely skipped).
      Used when partial provenance data is still useful to the caller.
"""

import asyncpg
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_att(row: dict) -> dict:
    """Ensure payload is a dict (asyncpg may return JSONB as str)."""
    if isinstance(row.get("payload"), str):
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
    return row


class CycleError(Exception):
    def __init__(self, node_id: str):
        self.node_id = node_id
        super().__init__(f"Cycle detected at attestation: {node_id}")


class MissingReferenceError(Exception):
    def __init__(self, ref_id: str, referenced_by: str):
        self.ref_id = ref_id
        self.referenced_by = referenced_by
        super().__init__(f"Missing reference: {ref_id} (referenced by {referenced_by})")


async def get_attestation(pool: asyncpg.Pool, att_id: str) -> Optional[dict]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM attestations WHERE id = $1", att_id
        )
        return _parse_att(dict(row)) if row else None


async def traverse_dag(
    pool: asyncpg.Pool,
    root_id: str,
    visited: Optional[set] = None,
    path: Optional[set] = None,
) -> list[dict]:
    """
    DFS traversal from root attestation to all upstream inputs.
    Returns list of all attestation records in the chain (including root).
    Raises CycleError, MissingReferenceError on structural anomalies.
    """
    if visited is None:
        visited = set()
    if path is None:
        path = set()

    if root_id in path:
        raise CycleError(root_id)

    if root_id in visited:
        return []  # already processed, no duplicates

    path.add(root_id)
    att = await get_attestation(pool, root_id)
    if att is None:
        raise MissingReferenceError(root_id, "traversal root")

    results = [att]
    inputs: list[str] = att["payload"].get("inputs", [])

    for input_id in inputs:
        child_att = await get_attestation(pool, input_id)
        if child_att is None:
            raise MissingReferenceError(input_id, root_id)
        sub = await traverse_dag(pool, input_id, visited, path)
        results.extend(sub)

    path.discard(root_id)
    visited.add(root_id)
    return results


async def collect_chain(pool: asyncpg.Pool, root_id: str) -> tuple[list[dict], list[dict]]:
    """
    Strict mode: returns (chain_nodes, anomalies).
    Any cycle or missing reference causes traversal to abort and the anomaly
    to be the only entry in the anomalies list.
    Anomalies are dicts with {type, attestation_id, detail}.
    """
    anomalies = []
    try:
        nodes = await traverse_dag(pool, root_id)
        return nodes, anomalies
    except CycleError as e:
        anomalies.append({
            "type": "cycle",
            "attestation_id": e.node_id,
            "detail": f"Cycle detected at node {e.node_id}",
        })
        return [], anomalies
    except MissingReferenceError as e:
        anomalies.append({
            "type": "missing_reference",
            "attestation_id": e.ref_id,
            "detail": f"Attestation {e.ref_id} referenced by {e.referenced_by} not found",
        })
        return [], anomalies


# ── Graceful traversal (lenient mode) ─────────────────────────────────────────

async def traverse_dag_graceful(
    pool: asyncpg.Pool,
    root_id: str,
    visited: Optional[set] = None,
    path: Optional[set] = None,
    anomalies: Optional[list] = None,
) -> list[dict]:
    """
    Lenient DFS traversal: continues past missing references instead of aborting.

    - Missing reference: appended to anomalies list, branch is skipped
    - Cycle: appended to anomalies list, cycle edge is not followed
    - Diamond DAGs handled correctly via the visited set (same as strict mode)

    Returns all reachable attestation records.
    """
    if visited is None:
        visited = set()
    if path is None:
        path = set()
    if anomalies is None:
        anomalies = []

    if root_id in path:
        logger.warning("Graceful traversal: cycle detected at %s", root_id[:12])
        anomalies.append({
            "type": "cycle",
            "attestation_id": root_id,
            "detail": (
                f"Cycle detected — attestation {root_id[:16]}... references "
                "an ancestor in its own supply chain (impossible ordering)"
            ),
        })
        return []

    if root_id in visited:
        return []

    path.add(root_id)

    att = await get_attestation(pool, root_id)
    if att is None:
        logger.warning("Graceful traversal: missing reference %s — branch skipped", root_id[:12])
        anomalies.append({
            "type": "missing_reference",
            "attestation_id": root_id,
            "detail": (
                f"Referenced attestation {root_id[:16]}... not found — "
                "this branch of the supply chain cannot be verified"
            ),
        })
        path.discard(root_id)
        return []

    results = [att]

    for input_id in att["payload"].get("inputs", []):
        sub = await traverse_dag_graceful(pool, input_id, visited, path, anomalies)
        results.extend(sub)

    path.discard(root_id)
    visited.add(root_id)
    return results


async def collect_chain_graceful(
    pool: asyncpg.Pool,
    root_id: str,
) -> tuple[list[dict], list[dict]]:
    """
    Lenient mode: returns (chain_nodes, anomalies).

    Unlike collect_chain(), this never returns an empty node list due to a
    missing reference — it returns whatever portion of the chain is reachable,
    plus anomaly records for every skipped branch.

    Callers receive partial provenance data with clearly flagged gaps,
    rather than a complete failure.  This is appropriate for purchaser-facing
    reports where any verified portion is still useful.
    """
    anomalies: list[dict] = []

    if not await get_attestation(pool, root_id):
        anomalies.append({
            "type": "missing_reference",
            "attestation_id": root_id,
            "detail": f"Root attestation {root_id} not found",
        })
        return [], anomalies

    nodes = await traverse_dag_graceful(pool, root_id, anomalies=anomalies)
    return nodes, anomalies
