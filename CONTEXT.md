# Supply Chain Provenance — Full System Context

**Hackathon:** Red Team DefTech — Verified Canadian Supply Chains
**Date:** May 30, 2026 | Bayview Yards, Ottawa | $20,000 prize pool
**GitHub:** github.com/athifashaffy/supplychainhack

---

## What We Built

A cryptographic provenance system for verifying "Made in Canada" and "Product of Canada" claims in the Canadian drone sector. Every supplier contribution is recorded as an Ed25519-signed attestation. Attestations reference upstream attestations, forming a tamper-evident DAG (directed acyclic graph) from raw materials to finished product.

**Three components, packaged as Docker Compose:**
1. **Verification backend** — FastAPI (Python 3.12) — this is what the harness scores
2. **Supplier UI** — vanilla HTML/JS, Ed25519 signing in-browser via SubtleCrypto
3. **Purchaser UI** — vanilla HTML/JS, ZXing QR scanning, provenance report display

---

## Canadian Content Rules (Competition Bureau)

| Designation | Canadian Direct Costs | Last Substantial Transformation |
|---|---|---|
| **Product of Canada** | ≥ 98% of total | Must occur in Canada |
| **Made in Canada** | ≥ 51% of total | Must occur in Canada |
| **Not Qualified** | < 51% OR last transformation outside Canada | — |

- "Total direct costs" = materials + labour across ALL tiers of the supply chain DAG
- "Substantial transformation" = a step that changes the form/nature of inputs (components → subsystem, not just labelling)
- Costs must not be double-counted when the same upstream attestation is reachable via multiple paths (diamond DAGs)

---

## Architecture

```
docker-compose.yml
├── db        — PostgreSQL 16 (port 5432)
├── backend   — FastAPI on port 8000
└── frontend  — nginx on port 3000
```

### Database Schema (db.py)

```sql
suppliers (
    supplier_id    TEXT PRIMARY KEY,
    name           TEXT,
    country        TEXT,   -- ISO 3166-1 alpha-2: "CA", "US", etc.
    province       TEXT,   -- "ON", "BC", etc.
    public_key_hex TEXT,   -- 64-char hex = 32 raw bytes Ed25519 public key
    created_at     TIMESTAMPTZ
)

attestations (
    id         TEXT PRIMARY KEY,   -- SHA-256(canonical JSON of payload)
    payload    JSONB,
    signature  TEXT,               -- hex-encoded Ed25519 signature
    signer_id  TEXT,               -- supplier_id of signer
    sig_valid  BOOLEAN,
    created_at TIMESTAMPTZ
)

attestation_inputs (
    attestation_id TEXT REFERENCES attestations(id),
    input_id       TEXT,
    PRIMARY KEY (attestation_id, input_id)
)
```

---

## Attestation Schema (models.py)

```python
AttestationPayload:
    supplier_id: str                      # required
    product_name: str                     # required
    timestamp: str                        # required, ISO 8601
    location: Location                    # required
        country: str                      # required, e.g. "CA"
        province: Optional[str]           # e.g. "ON"
    inputs: list[str]                     # upstream attestation IDs consumed
    materials: list[MaterialCost]
        name: str
        cost_cad: float
        country: str                      # where material originated
        quantity: Optional[float]
        unit: Optional[str]
    labour: Optional[LabourCost]
        cost_cad: float
        country: str                      # where labour was performed
        hours: Optional[float]
    transformation_description: Optional[str]   # describes the manufacturing step
    quantity_produced: Optional[float]
    unit: Optional[str]

AttestationSubmit (full envelope):
    payload: AttestationPayload
    signature: str       # hex Ed25519 signature over canonical JSON of payload
    signer_id: str       # must match payload.supplier_id
```

---

## Cryptographic Architecture (crypto.py)

### Canonical JSON
```python
json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
```
- Keys sorted alphabetically at every level
- No whitespace
- ASCII-safe
- **CRITICAL:** Never use `model_dump()` for signing or ID computation — Pydantic coerces `int → float` (e.g. `cost_cad: 1` → `1.0`), making `json.dumps(1.0)` → `"1.0"` which differs from the wire-format `"1"` the browser or harness signed. Always use the raw wire-format dict.

### Content-Addressed ID
```python
attestation_id = SHA-256(canonical_json(payload))
```
Any mutation to the payload produces a different ID. The ID is computed from the exact payload as submitted (wire format), stored in DB, and used for all downstream references.

### Ed25519 (cryptography library)
```python
# Sign
private_key.sign(canonical_json(payload))  # returns 64-byte signature

# Verify
public_key.verify(signature_bytes, canonical_json(payload))  # raises InvalidSignature on failure
```

### Key Storage
- Public keys: 32 raw bytes → 64 hex chars, stored in `suppliers.public_key_hex`
- Private keys: never stored server-side, held only by suppliers

---

## API Endpoints (main.py)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/attest` | Ingest + verify attestation (middleware-gated) |
| `GET` | `/api/attest/{id}` | Fetch single attestation |
| `GET` | `/api/attestations` | List recent attestations (last 100) |
| `GET` | `/api/verify/{id}` | Verify single attestation signature + signer |
| `GET` | `/api/product/{id}` | Full provenance report (content %, designation, anomalies) |
| `GET` | `/api/suppliers` | List suppliers |
| `POST` | `/api/suppliers` | Register supplier with public key |
| `GET` | `/api/qr/{id}` | QR code PNG pointing to purchaser page |
| `POST` | `/api/seed` | Load demo fixture data |
| `POST` | `/api/seed/edge-cases` | Load adversarial edge case data |
| `GET` | `/health` | Health check |

### POST /api/attest flow
1. `AttestationIntegrityMiddleware` intercepts BEFORE the route:
   - Checks envelope completeness (payload, signature, signer_id present)
   - Checks `signer_id == payload.supplier_id`
   - Looks up signer in supplier registry
   - Verifies Ed25519 signature
   - Rejects with HTTP 422 if any check fails
   - Stores raw payload dict in `request.scope["_raw_payload"]`
   - Replays body so Pydantic can still parse it
2. Route handler reads `_raw_payload` from scope (not `model_dump()`)
3. Computes content-addressed ID from raw payload
4. Re-verifies signature for `sig_valid` DB column
5. Inserts into `attestations` and `attestation_inputs`

### GET /api/product/{id} flow
1. `collect_chain_graceful()` — DFS traversal, continues past missing references
2. `compute_content(chain_nodes)` — Canadian content calculation
3. `run_all_checks(chain_nodes, pool, structural_anomalies)` — all anomaly checks
4. Returns full report with designation, percentage, anomaly list, chain summary

---

## DAG Traversal (graph.py)

### Two modes

**Strict (`collect_chain`)** — used by `/api/verify`:
- Raises `CycleError` or `MissingReferenceError` on any defect
- Returns empty node list on failure

**Graceful (`collect_chain_graceful`)** — used by `/api/product`:
- Continues past missing references (accumulates anomaly, skips branch)
- Only cycles abort a branch (not the whole traversal)
- Returns all reachable nodes + list of structural anomaly dicts

### DFS order
`chain_nodes` from traversal is **pre-order DFS**: `chain_nodes[0]` = root (final product), leaves are last. This matters for determining "last substantial transformation" — the root-most qualifying node is the last transformation.

### Cycle detection
Uses a `path` set (current DFS path) distinct from `visited` set (completed nodes). If a node is in `path` during DFS → cycle. If in `visited` → already processed (valid for diamond DAGs).

### Diamond DAG handling
The `visited` set prevents processing the same node twice even when reachable via multiple paths. This is what prevents double-counting in diamond-shaped supply chains.

---

## Canadian Content Computation (canadian_content.py)

```python
def compute_content(chain_nodes):
    # 1. Find last substantial transformation node
    #    - First node in chain_nodes (pre-order DFS) that has inputs AND transformation_description
    #    - Fallback: first node with any inputs
    #    - last_transformation_in_canada = is_canadian(that_node.location.country)

    # 2. For each node (deduplicated via seen_ids):
    #    - Sum materials costs by country
    #    - Sum labour costs by country

    # 3. pct = canadian_cost / total_cost * 100

    # 4. Apply designation rules:
    #    if last_transformation_in_canada and pct >= 98.0 → "Product of Canada"
    #    if last_transformation_in_canada and pct >= 51.0 → "Made in Canada"
    #    else → "Not Qualified"
```

**Canada codes recognised:** `"CA"`, `"CAN"`, `"CANADA"` (case-insensitive)

**Double-counting prevention:** `seen_ids` set — each attestation's costs counted exactly once regardless of how many paths lead to it in a diamond DAG.

---

## Anomaly Detection (anomaly.py)

All checks flow through `run_all_checks(chain_nodes, pool, structural_anomalies)`. Returns list of anomaly dicts, each with `{type, attestation_id, detail}`.

### Anomaly types and detection

| Type | Detection method |
|---|---|
| `cycle` | DFS path set — node appears in its own ancestor path |
| `missing_reference` | Input ID not found in DB during traversal |
| `missing_field` | Required fields absent: supplier_id, product_name, timestamp, location, location.country |
| `invalid_cost` | Any `cost_cad < 0` in materials or labour |
| `unknown_signer` | `signer_id` not found in supplier registry |
| `invalid_signature` | Ed25519 verification fails for known signer |
| `quantity_inconsistency` | UTXO ledger: total consumed across all consumers > produced |
| `timestamp_ordering` | Upstream attestation timestamp strictly later than downstream consumer |
| `cross_chain_reuse` | Same attestation consumed by attestations outside current chain |

### Run order in run_all_checks()
1. `check_missing_fields()` per node
2. `check_negative_costs()` per node
3. Registry lookup (unknown_signer) per node
4. `check_signature()` per node (skipped if unknown signer)
5. `QuantityLedger.violations()` (whole chain)
6. `check_timestamp_ordering()` (whole chain)
7. `check_cross_chain_reuse()` (whole chain, async DB query)

---

## Quantity Ledger (ledger.py)

UTXO-style double-spend prevention.

```python
ledger = QuantityLedger()
ledger.ingest_chain(chain_nodes)   # records production and consumption
violations = ledger.violations()   # returns anomaly dicts for overconsumption
```

**How it works:**
- Each node with `quantity_produced` → production record
- Each node with inputs + materials with quantities → consumption records
- Consumption is distributed proportionally across all declared inputs
  - e.g. node with 3 inputs claiming 90 units total → 30 units attributed per input
- Violation = sum of all consumers' attributed consumption > upstream's `quantity_produced`

**Limitation:** Proportional distribution is an approximation. If all consumption actually came from one specific input, the per-input attribution may under-count and miss the violation. The correct fix requires explicit per-material provenance tracking in the attestation schema (not currently in our schema).

---

## Middleware (middleware.py)

`AttestationIntegrityMiddleware` runs on every `POST /api/attest`:

```
Check A: envelope has payload + signature + signer_id
Check B: signer_id == payload.supplier_id (impersonation check)
Check C: signer_id exists in supplier registry
Check D: Ed25519 signature verifies against registry public key
```

On any failure → HTTP 422 with structured `IntegrityViolationError` body.

On success → stores raw parsed payload in `request.scope["_raw_payload"]` so the route handler uses it (not Pydantic model_dump()) for ID computation and final sig_valid check.

---

## Seed Data (seed.py)

Demo chain: Aurora-X Surveillance Drone

```
Aurora-X Drone (sup-002, BC)
├── Carbon Fibre Frame (sup-001, ON)       — raw material, CA
└── Avionics Module (sup-003, QC)
    ├── Flight Controller Chips (sup-004, US)   — raw material, US
    └── Brushless Motor Assembly (sup-005, AB)  — raw material, CA
```

**Suppliers:**
- `sup-001` Boreal Composites Ltd. (CA/ON)
- `sup-002` Pacific Drone Systems Inc. (CA/BC)
- `sup-003` Montréal Avionics SENC (CA/QC)
- `sup-004` US Semiconductor Corp (US)
- `sup-005` Northern Propulsion Labs (CA/AB)

**Expected result:** ~85-90% Canadian, "Made in Canada" (US chips bring it below 98%)

---

## Edge Case Seed Data (seed_edge_cases.py)

17 scenarios covering every harness test category:

| ID | Scenario | Expected |
|---|---|---|
| ec01 | 100% Canadian | Product of Canada, 0 anomalies |
| ec02 | 0% Canadian | Not Qualified, 0 anomalies |
| ec03 | Exactly 98.0% | Product of Canada |
| ec04 | 97.9% | Made in Canada (just misses 98%) |
| ec05 | Exactly 51.0% | Made in Canada |
| ec06 | 50.9% | Not Qualified (just misses 51%) |
| ec07 | 100% CA costs, US final assembly | Not Qualified (last transformation rule) |
| ec08 | Tampered payload | `invalid_signature` anomaly |
| ec09 | Unregistered signer | `unknown_signer` anomaly |
| ec10 | Downstream claims 10 units, upstream produced 2 | `quantity_inconsistency` |
| ec11 | Input references non-existent attestation | `missing_reference` |
| ec12 | Artificially injected A→B→C→A cycle | `cycle` anomaly |
| ec13 | Diamond DAG (D shared by B and C, both consumed by A) | D counted once, 0 anomalies |
| ec14 | Missing `location.country` | `missing_field` |
| ec15 | Zero cost (no materials, no labour) | 0%, no crash |
| ec16x | Product X uses batch (legitimate) | `cross_chain_reuse` flagged when also viewed from Y |
| ec16y | Product Y reuses same batch | `cross_chain_reuse` anomaly |
| ec17 | 4-tier deep chain (ore→ingot→plate→part) | High %, Product of Canada |

---

## Frontend (supplier.html)

### Ed25519 in browser (SubtleCrypto API)
- Requires Chrome 128+ / Firefox 130+
- `crypto.subtle.generateKey({name: 'Ed25519'}, true, ['sign', 'verify'])`
- PKCS8 export has 16-byte header; raw private key = last 32 bytes
- Signs canonical JSON using `toCanonical()` — recursive key-sort implementation

### Canonical JSON (JavaScript)
```javascript
function toCanonical(val) {
  if (val === null || typeof val !== 'object' || Array.isArray(val)) {
    return JSON.stringify(val);
  }
  const keys = Object.keys(val).sort();
  return '{' + keys.map(k => JSON.stringify(k) + ':' + toCanonical(val[k])).join(',') + '}';
}
```
Must produce identical output to the Python backend for the same payload.

### API URL
All three pages use:
```javascript
const API = window.API_BASE_URL || 'http://localhost:8000';
```
`window.API_BASE_URL` is set by `config.js`, which the frontend nginx container overwrites at start via `docker-entrypoint.sh` using the `API_BASE_URL` environment variable.

---

## Docker Compose

```yaml
services:
  db:      postgres:16-alpine, port 5432, volume pgdata
  backend: ./backend, port 8000, depends on db (healthcheck)
  frontend: ./frontend, port 3000, depends on backend
```

**Override API URL at runtime (no rebuild):**
```bash
API_BASE_URL=https://your-public-url.com docker compose up --build
```

---

## Known Design Decisions and Trade-offs

### Quantity ledger proportional distribution
The ledger distributes total consumed quantity evenly across all inputs. This is an approximation — if a node has 3 inputs but consumed all material from one, the per-input attribution may miss the fraud. The spec may provide a way to attribute per-material provenance explicitly; if so, the ledger logic in `ledger.py:_record_node()` should be updated.

### Substantial transformation heuristic
We identify the last substantial transformation as the root-most chain node that has `inputs` AND a non-empty `transformation_description`. The event-day spec may define this differently (boolean field, enum, etc.). The relevant code is in `canadian_content.py` around line 54.

### Schema may change on event day
On event day, the harness provides its own attestation schema and supplier registry. The system must be adapted to match. Key adaptation points:
- Field names in `models.py` (AttestationPayload)
- Required fields list in `anomaly.py:REQUIRED_FIELDS`
- Country codes in `canadian_content.py:CANADA_CODES`
- The `canonical_json()` function in `crypto.py` — must match the reference library exactly

### Cross-chain reuse detection
Flags any attestation consumed by nodes outside the current chain. This is correct for physical goods (a batch can only be used once). In the harness, legitimate diamond DAGs (same attestation reachable via two branches of the SAME product) are handled correctly because the traversal deduplicates via `visited` set before the reuse check runs.

---

## File Structure

```
supply-chain-app/
├── docker-compose.yml
├── README.md
├── CONTEXT.md                    ← this file
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   ← FastAPI app, all routes
│   ├── crypto.py                 ← Ed25519, canonical JSON, SHA-256 IDs
│   ├── graph.py                  ← DAG traversal (strict + graceful)
│   ├── canadian_content.py       ← content %, designation logic
│   ├── anomaly.py                ← all anomaly checks
│   ├── ledger.py                 ← UTXO quantity ledger
│   ├── middleware.py             ← Ed25519 pre-flight middleware
│   ├── models.py                 ← Pydantic schemas
│   ├── registry.py               ← supplier lookup/register
│   ├── db.py                     ← asyncpg pool, schema init
│   ├── exceptions.py             ← typed exception hierarchy
│   ├── seed.py                   ← demo fixture data
│   └── seed_edge_cases.py        ← 17 adversarial test scenarios
├── frontend/
│   ├── Dockerfile
│   ├── docker-entrypoint.sh      ← injects API_BASE_URL into config.js
│   ├── nginx.conf
│   └── public/
│       ├── index.html            ← dashboard
│       ├── supplier.html         ← supplier attestation form
│       ├── purchaser.html        ← QR scan + provenance report
│       ├── components.js         ← shared UI component library
│       ├── config.js             ← sets window.API_BASE_URL
│       └── style.css
└── tests/
    └── test_e2e.py               ← Playwright E2E tests (48 tests)
```

---

## Quick Start

```bash
git clone https://github.com/athifashaffy/supplychainhack.git
cd supplychainhack
docker compose up --build
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs

# Load demo data
curl -X POST http://localhost:8000/api/seed

# Load edge case data
curl -X POST http://localhost:8000/api/seed/edge-cases
```

---

## Scoring Dimensions

The automated harness scores three things:

1. **Correctness of Canadian content computation**
   - Percentage accurate to at least 1 decimal place
   - Designation matches (Product of Canada / Made in Canada / Not Qualified)
   - No double-counting on diamond DAGs

2. **Accuracy of anomaly detection**
   - All 8 anomaly types correctly identified
   - Anomaly type strings match what harness expects
   - No false positives on clean chains

3. **Edge case handling**
   - Graceful degradation on missing references (partial report, not crash)
   - Zero-cost chains don't divide by zero
   - Deeply nested chains (4+ tiers) traverse correctly
   - Threshold boundary values (exactly 51.0%, exactly 98.0%) classified correctly
