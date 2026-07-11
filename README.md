# Cryptographic Supply Chain Provenance

**Red Team DefTech — Verified Canadian Supply Chains Hackathon**
Bayview Yards, Ottawa | May 30, 2026 | $20,000 prize pool

A cryptographic provenance system for verifying "Made in Canada" and "Product of Canada" claims in the Canadian drone sector supply chain.

---
## 🏆 Project Demo
* [View Presentation (Canva)](https://canva.link/qz9mnsctb8o23qj)

## What It Does

Every supplier contribution is recorded as an Ed25519-signed attestation. Attestations reference upstream attestations, forming a tamper-evident DAG (directed acyclic graph) from raw materials to finished product. The system walks the chain, verifies every signature, computes Canadian content percentages per Competition Bureau rules, and detects anomalies.

**Key questions answered:**
1. Does this product qualify as *Made in Canada* or *Product of Canada*?
2. What percentage of the product's value is Canadian?
3. Can the provenance record be independently verified end-to-end?

---

## Canadian Content Rules (Competition Bureau)

| Designation | Canadian Direct Costs | Last Substantial Transformation |
|---|---|---|
| **Product of Canada** | ≥ 98% | Must occur in Canada |
| **Made in Canada** | ≥ 51% | Must occur in Canada |

---

## Architecture

```
docker-compose.yml
├── db        — PostgreSQL 16 (attestation + supplier registry store)
├── backend   — FastAPI (Python 3.12) — verification engine
└── frontend  — nginx serving static HTML/CSS/JS
```

### Key Backend Modules

| File | Purpose |
|---|---|
| `crypto.py` | Ed25519 sign/verify, canonical JSON, SHA-256 content-addressed IDs |
| `graph.py` | DAG traversal (strict + graceful), cycle detection, DFS |
| `canadian_content.py` | Cost aggregation by country across DAG tiers, designation logic |
| `anomaly.py` | All anomaly detection (signature, signer, missing fields, quantity, replay) |
| `ledger.py` | UTXO-style quantity ledger — detects double-spend across inputs |
| `middleware.py` | Ed25519 pre-flight middleware on `POST /api/attest` |
| `registry.py` | Supplier identity store (Postgres-backed) |
| `models.py` | Pydantic models for attestations, suppliers, reports |

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/attest` | Ingest and cryptographically verify a new attestation |
| `GET` | `/api/verify/{id}` | Verify a specific attestation |
| `GET` | `/api/product/{id}` | Full provenance report: content %, designation, anomalies |
| `GET` | `/api/suppliers` | List registered suppliers |
| `POST` | `/api/suppliers` | Register a new supplier with Ed25519 public key |
| `GET` | `/api/qr/{id}` | Generate QR code PNG for a product attestation |
| `POST` | `/api/seed` | Load demo fixture data |
| `POST` | `/api/seed/edge-cases` | Load adversarial edge case data |
| `GET` | `/health` | Health check |

### Anomaly Detection

| Anomaly | Detection |
|---|---|
| Tampered payload | Middleware re-verifies Ed25519 sig on every ingest |
| Unknown signer | Registry lookup — rejects if `signer_id` not found |
| Invalid signature | Per-attestation sig check across full chain |
| Missing required fields | Schema validation — structured error, no crash |
| Quantity fraud | UTXO ledger — total consumed across all consumers vs produced |
| Cross-chain replay | `attestation_inputs` table — detects same attestation in multiple chains |
| DAG cycle | DFS with path set — cycle = impossible physical ordering |
| Missing reference | Input ID not found in attestation store |

---

## Quick Start

### Prerequisites
- Docker + Docker Compose

### Run Locally

```bash
git clone https://github.com/athifashaffy/supplychainhack.git
cd supplychainhack
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

### Load Demo Data

```bash
curl -X POST http://localhost:8000/api/seed
```

Or click **Seed Demo Data** on the dashboard.

### Custom API URL (for external access / tunnel)

```bash
API_BASE_URL=https://your-tunnel-url.trycloudflare.com docker compose up --build
```

The frontend container substitutes `API_BASE_URL` into `config.js` at start — no rebuild needed.

---

## Cryptographic Design

- **Signature scheme:** Ed25519 via Python `cryptography` library
- **Attestation ID:** `SHA-256(canonical_json(payload))` — content-addressed; any mutation changes the ID
- **Canonical serialization:** `json.dumps(payload, sort_keys=True, separators=(',', ':'))` — deterministic
- **Signing scope:** the payload only (not the envelope), consistent with in-toto / SLSA patterns
- **Trust anchor:** supplier registry (Postgres) maps `supplier_id → Ed25519 public key`
- **Middleware enforcement:** `POST /api/attest` is gated by `AttestationIntegrityMiddleware` which verifies signer identity + signature before the route handler runs — cannot be bypassed

---

## Running Tests

```bash
# Install test dependencies
pip install playwright pytest pytest-asyncio
playwright install chromium

# Run E2E tests (requires docker compose up)
pytest tests/test_e2e.py -v
```

---

## Submission Format

Single Docker Compose repository. Start with:
```bash
docker compose up --build
```

Backend available at port 8000, frontend at port 3000.
