from __future__ import annotations

from typing import Dict, List, Set, Tuple
from .models import Attestation, Node, Anomaly, Reason


class SupplyChain:
    def __init__(self, attestations: List[Attestation]):
        self.nodes: Dict[str, Node] = {}
        # Duplicate-hash anomalies recorded at construction time. First
        # insertion wins; subsequent ones with the same id are flagged but
        # not allowed to silently overwrite. Verifier reads this after the
        # constructor returns and merges it into the anomaly list.
        self.construction_anomalies: List[Anomaly] = []

        for att in attestations:
            if att.hash in self.nodes:
                # Compare canonical body shapes to distinguish two cases:
                #   identical body  -> redundant copy, silently drop
                #   different body  -> HASH_MISMATCH (an attestation claiming
                #                      a known id with different content)
                existing = self.nodes[att.hash].attestation.model_dump(mode="json")
                incoming = att.model_dump(mode="json")
                if existing == incoming:
                    continue
                self.construction_anomalies.append(Anomaly(
                    reason=Reason.HASH_MISMATCH,
                    attestation_hash=att.hash,
                    detail=(
                        f"Hash {att.hash} appears with different bodies in "
                        f"the bundle; content-addressing violated."
                    ),
                ))
                continue
            self.nodes[att.hash] = Node(
                attestation=att,
                input_hashes=[i.attestation_hash for i in att.inputs],
                consumer_hashes=[],
            )

        # Build reverse edges.
        for node in self.nodes.values():
            for input_hash in node.input_hashes:
                if input_hash in self.nodes:
                    self.nodes[input_hash].consumer_hashes.append(node.attestation.hash)

    def get(self, h: str) -> Node | None:
        return self.nodes.get(h)

    def collect_reachable_dfs(self, root_hash: str) -> Tuple[Set[str], List[Anomaly]]:
        """
        DFS from the product root backward through input references.
        Detects cycles using visiting/visited sets.
        Returns (reachable subgraph, structural anomalies).

        Note: a missing root is handled by the caller, not by this method.
        That way the caller gets exactly one BROKEN_LINK for a missing root,
        not one from here and another from the caller's check.
        """
        reachable: Set[str] = set()
        anomalies: List[Anomaly] = []
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def dfs(h: str, path: List[str]):
            if h not in self.nodes:
                # Only fires for child inputs, never the root (caller pre-checks).
                parent = path[-1] if path else h
                anomalies.append(
                    Anomaly(
                        reason=Reason.BROKEN_LINK,
                        attestation_hash=parent,
                        detail=f"Missing input attestation: {h}",
                    )
                )
                return

            if h in visiting:
                cycle_path = " -> ".join(path + [h])
                anomalies.append(
                    Anomaly(
                        reason=Reason.CYCLE,
                        attestation_hash=h,
                        detail=f"Cycle detected: {cycle_path}",
                    )
                )
                return

            if h in visited:
                return

            visiting.add(h)
            reachable.add(h)

            for child_hash in self.nodes[h].input_hashes:
                dfs(child_hash, path + [h])

            visiting.remove(h)
            visited.add(h)

        if root_hash in self.nodes:
            dfs(root_hash, [])

        return reachable, anomalies

    def topological_root_to_leaves(
        self, root_hash: str, reachable: Set[str]
    ) -> List[str]:
        """
        Order where consumers appear before their inputs.

        Implemented as post-order DFS reversed: post-order from root visits
        each leaf before its consumer; reversing gives root first, leaves
        last. Restricted to the reachable subgraph.

        Required for correct flow propagation in cost attribution: a node's
        flow must be finalized (all consumers contributed) before we use it.
        Assumes the caller has already detected cycles.
        """
        visited: Set[str] = set()
        post: List[str] = []

        def dfs(h: str):
            if h in visited or h not in reachable or h not in self.nodes:
                return
            visited.add(h)
            for input_hash in self.nodes[h].input_hashes:
                dfs(input_hash)
            post.append(h)

        dfs(root_hash)
        post.reverse()
        return post
