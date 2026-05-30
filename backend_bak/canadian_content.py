"""
Canadian content percentage computation.

Rules (Competition Bureau):
  Product of Canada  >= 98% of total direct costs + last substantial transformation in CA
  Made in Canada     >= 51% of total direct costs + last substantial transformation in CA

Costs are aggregated across ALL tiers of the DAG.
Only costs from leaf-level materials and labour are counted
(to avoid double-counting when intermediate attestations reference upstream costs).

Strategy: a node's direct costs are its OWN materials + labour ONLY.
          Upstream costs flow through via the DAG traversal.
"""

CANADA_CODES = {"CA", "CAN", "CANADA"}


def is_canadian(country: str) -> bool:
    return country.strip().upper() in CANADA_CODES


def compute_content(chain_nodes: list[dict]) -> dict:
    """
    Given a list of attestation records from traverse_dag, compute:
    - total_cost_cad
    - canadian_cost_cad
    - canadian_content_pct
    - designation
    - last_transformation_in_canada
    """
    seen_ids = set()
    total_cost = 0.0
    canadian_cost = 0.0
    last_transformation_in_canada = False

    if not chain_nodes:
        return {
            "total_cost_cad": 0.0,
            "canadian_cost_cad": 0.0,
            "canadian_content_pct": 0.0,
            "designation": "Unknown",
            "last_transformation_in_canada": False,
            "cost_breakdown": [],
        }

    breakdown = []
    # Identify the last substantial transformation node.
    # chain_nodes is pre-order DFS (root first), so earlier = closer to final product.
    # A "substantial transformation" changes the form/nature of inputs:
    #   - Node must have inputs (not a raw material)
    #   - Prefer nodes with an explicit transformation_description
    # The first qualifying node in chain order is the "last" transformation step.
    last_transformation_node = None
    for record in chain_nodes:
        payload = record["payload"]
        has_inputs = bool(payload.get("inputs"))
        has_description = bool((payload.get("transformation_description") or "").strip())
        if has_inputs and has_description:
            last_transformation_node = record
            break
    # Fallback: first node with any inputs (processing step, even without description)
    if last_transformation_node is None:
        for record in chain_nodes:
            if record["payload"].get("inputs"):
                last_transformation_node = record
                break

    if last_transformation_node is not None:
        loc = last_transformation_node["payload"].get("location", {})
        last_transformation_in_canada = is_canadian(loc.get("country", ""))

    for record in chain_nodes:
        att_id = record["id"]
        if att_id in seen_ids:
            continue
        seen_ids.add(att_id)

        payload = record["payload"]
        materials = payload.get("materials", [])
        labour = payload.get("labour") or {}
        location = payload.get("location", {})

        node_total = 0.0
        node_canadian = 0.0

        # Materials
        for mat in materials:
            cost = float(mat.get("cost_cad", 0))
            country = mat.get("country", "")
            node_total += cost
            if is_canadian(country):
                node_canadian += cost
            breakdown.append({
                "attestation_id": att_id,
                "type": "material",
                "name": mat.get("name", ""),
                "cost_cad": cost,
                "country": country,
                "is_canadian": is_canadian(country),
            })

        # Labour
        if labour:
            cost = float(labour.get("cost_cad", 0))
            country = labour.get("country", "")
            node_total += cost
            if is_canadian(country):
                node_canadian += cost
            breakdown.append({
                "attestation_id": att_id,
                "type": "labour",
                "name": "Labour",
                "cost_cad": cost,
                "country": country,
                "is_canadian": is_canadian(country),
            })

        total_cost += node_total
        canadian_cost += node_canadian

    pct = (canadian_cost / total_cost * 100) if total_cost > 0 else 0.0

    if last_transformation_in_canada and pct >= 98.0:
        designation = "Product of Canada"
    elif last_transformation_in_canada and pct >= 51.0:
        designation = "Made in Canada"
    else:
        designation = "Not Qualified"

    return {
        "total_cost_cad": round(total_cost, 2),
        "canadian_cost_cad": round(canadian_cost, 2),
        "canadian_content_pct": round(pct, 2),
        "designation": designation,
        "last_transformation_in_canada": last_transformation_in_canada,
        "cost_breakdown": breakdown,
    }
