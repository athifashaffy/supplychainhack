from __future__ import annotations

from fractions import Fraction
from typing import Dict, Set
from .chain import SupplyChain
from .models import Designation
from . import adapters


def own_cost_cents(node) -> int:
    """Routed through adapters.get_costs: on event day the cost-flow rule
    (which fields, what weighting, what unit conversion) is whatever the
    spec defines, and that change lands in adapters.get_costs, not here."""
    materials, labour = adapters.get_costs(node.attestation)
    return materials + labour


def find_last_substantial_transformation(chain: SupplyChain, root_hash: str, reachable: Set[str]):
    """
    Mock pre-event rule: nearest-to-root substantial transformation wins.
    Swap when the real spec defines substantial transformation.
    """
    queue = [root_hash]
    seen = set()

    while queue:
        h = queue.pop(0)
        if h in seen or h not in chain.nodes:
            continue
        seen.add(h)
        node = chain.nodes[h]
        if h in reachable and node.attestation.is_substantial_transformation:
            return node
        queue.extend(node.input_hashes)

    return None


def attribute_costs_by_country(
    chain: SupplyChain, root_hash: str, reachable: Set[str]
) -> Dict[str, int]:
    """
    Cost attribution with partial consumption, in topological order.

      flow(root) = 1
      flow(input) += flow(consumer) * quantity_used / quantity_produced
      attributed_cost(node) = own_cost(node) * flow(node)

    Topological order ensures each node's flow is finalized before it is used
    to propagate to its inputs. This is what fixes the diamond-DAG bug where a
    shared upstream node receives contributions from two consumers but only
    one of them propagates further.
    """
    # Root-first topological order, restricted to the reachable subgraph.
    order = chain.topological_root_to_leaves(root_hash, reachable)

    flow: Dict[str, Fraction] = {root_hash: Fraction(1, 1)}

    for h in order:
        if h not in chain.nodes:
            continue
        f = flow.get(h, Fraction(0, 1))
        for ref in chain.nodes[h].attestation.inputs:
            ih = ref.attestation_hash
            if ih not in chain.nodes:
                continue
            produced = chain.nodes[ih].attestation.output.quantity
            if produced <= 0:
                continue
            ratio = Fraction(ref.quantity_used, produced)
            flow[ih] = flow.get(ih, Fraction(0, 1)) + f * ratio

    by_country: Dict[str, int] = {}
    for h in reachable:
        if h not in chain.nodes:
            continue
        node = chain.nodes[h]
        country = node.attestation.work_country
        attributed = round(own_cost_cents(node) * flow.get(h, Fraction(0, 1)))
        by_country[country] = by_country.get(country, 0) + attributed

    return by_country


def compute_designation(cost_by_country: Dict[str, int], last_st_country: str | None):
    total = sum(cost_by_country.values())
    canadian = cost_by_country.get("CA", 0)

    if total <= 0:
        return Designation.NONE, 0, 0, 0.0

    pct = canadian / total

    if last_st_country != "CA":
        return Designation.NONE, total, canadian, pct

    # Cross-multiplication avoids divide-then-compare rounding bugs.
    if canadian * 100 >= 98 * total:
        return Designation.PRODUCT_OF_CANADA, total, canadian, pct
    if canadian * 100 >= 51 * total:
        return Designation.MADE_IN_CANADA, total, canadian, pct

    return Designation.NONE, total, canadian, pct
