---
name: event-day-adapt
description: >
  Adapt the supply-chain provenance backend to the real hackathon spec when it
  drops on event day. Use when the user provides the official attestation schema,
  supplier registry, reference signing/verifying library, sample chains with
  expected outputs, or the self-test harness — i.e. anything that pins down what
  was previously assumed. Drives the schema/canonical-JSON/anomaly-name adaptation
  in the correct order and self-tests against sample data before submission.
---

# Event-Day Adaptation Runbook

The system in this repo was built against a **primer**, not a spec. Every field
name, the canonical-JSON rule, the anomaly type strings, and the
substantial-transformation rule are **assumptions** that the event-day spec will
either confirm or override. This skill turns those assumptions into a fast,
ordered adaptation so nothing is missed under time pressure.

**Working dir:** `/Users/athifshaffy/Documents/Repo/supply-chain-app`
**Scored component:** the FastAPI backend only. The harness runs the backend over
a held-out suite scoring (1) Canadian-content correctness, (2) anomaly-detection
accuracy, (3) edge-case handling. The two UIs are demo-only — do not let UI work
block backend correctness.

## The Golden Rule (do not break this)

Signatures and IDs are computed over the **raw wire-format dict**, never over
`model_dump()`. Pydantic coerces `int -> float` (`1` -> `1.0`), which changes the
canonical bytes and breaks every signature. The raw payload lives in
`request.scope["_raw_payload"]` (set by `middleware.py`). Keep it that way.

## Step 0 — Triage what just arrived

Before editing anything, read whatever the user provided and answer these. Write
the answers down (a scratch list) before touching code — they drive every step.

1. **Canonical JSON** — exact serialization the reference lib uses. Sorted keys?
   Whitespace? `ensure_ascii`? Float formatting? Number canonicalization? Field
   exclusions (is `signature` inside or outside the signed bytes)?
2. **ID scheme** — is the attestation ID `SHA-256(canonical)`? Truncated? Hex vs
   base64? A different hash? A field the spec supplies directly?
3. **Key & signature encoding** — Ed25519 still? Public keys hex/base64/PEM? How
   many bytes? Signature hex/base64?
4. **Payload field names** — map every field to ours (see Step 2 table).
5. **Cost model** — currency? Are a node's declared costs NEW value-add only, or
   do they restate consumed-input costs? (decides the double-count risk)
6. **Substantial transformation** — how does the spec mark it? Boolean field?
   Enum? Derived from having inputs? (our code currently just uses the root)
7. **Anomaly output contract** — exact type strings the harness expects, and the
   response shape (list of dicts? strings? per-attestation?).
8. **Submission/scoring interface** — what endpoint(s) the harness calls and the
   exact JSON it expects back.

## Step 1 — Canonical JSON parity FIRST (everything depends on it)

If our canonical form differs from the reference library by one byte, **every
signature fails and every ID mismatches** — you will score zero on integrity and
break the whole DAG. Do this before anything else.

- File: `backend/crypto.py` -> `canonical_json()`. Current rule:
  `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
- Mirror in JS: `frontend/public/supplier.html` -> `toCanonical()` (must match
  the Python byte-for-byte, but this is demo-only — backend parity is what scores).
- **Verify, don't assume:** take a sample attestation from the provided data, run
  it through our `canonical_json()`, and confirm our `content_addressed_id()`
  reproduces the spec's stated ID and that the provided signature verifies with
  the provided public key. If either fails, fix `canonical_json()` until a known
  sample round-trips. Only then move on.

## Step 2 — Schema mapping via the adapter (do not rename everywhere)

Prefer one translation layer over renaming fields across 8 files. Build/extend
`backend/schema_adapter.py` with `normalize_payload(raw: dict) -> dict` that maps
the spec's fields to our internal names, then wire it in **before** canonical-JSON
computation in two places: `middleware.py::_verify_attestation_body` and
`main.py::api_attest` (before ID computation). Make it a no-op when no variant
keys are present so existing seed data still works.

CAUTION: normalization changes the bytes. Decide explicitly whether the spec
signs the **original** wire format or the **normalized** form. If the spec signs
the original, you must verify the signature on the *original* bytes and only
normalize for internal computation — do NOT verify over normalized bytes. Get
this wrong and Step 1 silently breaks.

Internal field names the rest of the code expects (map spec -> these):

| Internal (ours) | Used by | Likely spec variants to map |
|---|---|---|
| `supplier_id` | everything | `signer_id`, `worker_id`, `party`, `entity_id` |
| `product_name` | reports | `name`, `product`, `output` |
| `timestamp` | timestamp_ordering | `created_at`, `issued_at`, `date` |
| `location.country` | content, missing_field | `country`, `origin`, `geo.country` |
| `inputs: list[str]` | DAG, ledger | `consumes`, `parents`, `refs`; may be list-of-dicts `{id}` |
| `materials[].cost_cad` | content, costs | `cost`, `cost_usd`, `amount`, `value` |
| `materials[].country` | content | `origin`, `source_country` |
| `labour.cost_cad` | content, costs | `labor`, `wages`, `labour_cost` |
| `quantity_produced` | ledger | `output_qty`, `produced`, `qty_out` |
| `materials[].quantity` | ledger | `qty`, `amount`, `consumed_qty` |
| `transformation_description` | (legacy) transform | spec may use a boolean/enum instead |

## Step 3 — Canadian-content correctness (highest-scored axis)

File: `backend/canadian_content.py`.

- **Substantial transformation:** current code uses `chain_nodes[0]` (the queried
  root) and reads its `location.country`. If the spec defines it differently
  (a flag/enum, or "the root-most node that has inputs"), update the
  `last_transformation_node` selection. Re-read CONTEXT.md note here — it is stale
  and still describes the OLD heuristic.
- **Double-counting:** confirm a node's `materials` are NEW value-add, not the
  restated price of consumed inputs. If the spec restates input costs, switch the
  sum to count only `location`/value-add deltas, or you over-count and inflate %.
- **Currency:** if costs aren't CAD, normalize in the adapter (Step 2) or convert
  here. `CANADA_CODES = {"CA","CAN","CANADA"}` — extend if the spec uses other
  country encodings (e.g. `"124"` numeric, `"Canada"` full name already covered).
- **Thresholds:** keep `>=` (primer says "at least"). Designation must use the
  **unrounded** pct; only round for display. Verify exactly-98.0 and exactly-51.0
  sample cases land on the right side.
- **Diamond DAG:** `seen_ids` dedup must stay — each attestation counted once.

## Step 4 — Anomaly name + contract alignment (second-scored axis)

File: `backend/anomaly.py` (single entry point `run_all_checks`). Map our type
strings to whatever the harness expects, in one place, ideally a constant map:

| Ours | Map to harness string if different |
|---|---|
| `invalid_signature` | (tampered? integrity_violation?) |
| `unknown_signer` | (untrusted_signer? unregistered?) |
| `missing_field` | (incomplete? missing_data?) |
| `invalid_cost` | (negative_cost?) |
| `quantity_inconsistency` | (overconsumption? quantity_fraud?) |
| `cross_chain_reuse` | (replay? double_spend? reuse?) |
| `timestamp_ordering` | (temporal? impossible_order?) |
| `cycle` | (structural? loop?) |
| `missing_reference` | (broken_link? dangling_ref?) |

Also confirm the **response shape**: do we return `{type, attestation_id, detail}`
dicts, or does the harness want bare strings / a different key set? Match it
exactly. `REQUIRED_FIELDS` in `anomaly.py` must equal the spec's required-field
list. Watch for **false positives on clean chains** — the harness penalizes them;
e.g. ensure legitimate diamond DAGs don't trip `cross_chain_reuse` (they don't
today because consumers stay inside `chain_ids`).

## Step 5 — Load spec sample data and self-test

1. If the spec ships sample chains + expected outputs, load them (adapt
   `seed.py` / `seed_edge_cases.py` or a new loader) and diff our
   `GET /api/product/{id}` against expected: percentage to 1 decimal,
   designation string, and the anomaly set.
2. Run the provided self-test harness against our running backend. Fix
   mismatches in priority order: canonical-JSON/sig parity > content % >
   anomaly names/shape > edge cases.
3. Quality gate (no DB needed): `cd backend && python -m py_compile *.py && echo OK`.
   If pytest suites exist (`backend/tests/`), run them too.
4. Bring the stack up to smoke-test end to end:
   `API_BASE_URL=http://localhost:8000 docker compose up --build`.

## Step 6 — Submission interface

Wire whatever endpoint/format the harness calls. If it differs from
`GET /api/product/{id}`, add a thin route that maps our internal report dict to
the harness's expected JSON rather than rewriting internals. Re-run the self-test
harness one final time before submitting.

## Priority order under time pressure

1. Canonical-JSON / signature / ID parity (Step 1) — blocks everything.
2. Schema adapter wired without breaking signing (Step 2).
3. Canadian-content correctness incl. transformation + double-count (Step 3).
4. Anomaly names + response shape (Step 4).
5. Self-test green (Step 5).
6. Submission format (Step 6).
UI polish only after the backend self-tests pass.

## Known landmines (from building this)

- `model_dump()` for signing/ID = silent signature failures (int->float). Use raw.
- asyncpg returns JSONB as a string sometimes; `graph.py::_parse_att()` handles it
  — keep using it, don't `json.loads` ad hoc.
- `chain_nodes` is **pre-order DFS**: index 0 = root/final product, leaves last.
- Normalizing payloads after signing invalidates signatures — verify on the bytes
  the spec actually signed (Step 2 caution).
- CONTEXT.md's "last substantial transformation" section is stale vs the code.
