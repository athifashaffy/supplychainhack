# Ralph Agent Instructions — Supply Chain Provenance (Hackathon)

You are an autonomous coding agent working on a cryptographic supply chain provenance system for the Red Team DefTech Verified Canadian Supply Chains Hackathon (May 30, 2026).

## Project Context

- **Backend:** Python 3.12 + FastAPI + asyncpg + PostgreSQL
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **Crypto:** Ed25519 via `cryptography` library
- **Container:** Docker Compose (3 services: db, backend, frontend)
- **Working dir:** /Users/athifshaffy/Documents/Repo/supply-chain-app

Key backend modules:
- `backend/crypto.py` — Ed25519 sign/verify, canonical JSON (sort_keys, no whitespace), SHA-256 IDs
- `backend/graph.py` — DAG traversal (strict + graceful), cycle detection
- `backend/canadian_content.py` — 98%/51% Competition Bureau rules, cost aggregation
- `backend/anomaly.py` — all anomaly checks (sig, signer, fields, costs, timestamp, quantity, replay)
- `backend/ledger.py` — UTXO-style quantity ledger
- `backend/middleware.py` — Ed25519 pre-flight on POST /api/attest; stores raw payload in request.scope["_raw_payload"]
- `backend/models.py` — Pydantic models
- `backend/seed.py` — demo fixture data
- `backend/seed_edge_cases.py` — adversarial test data

## CRITICAL: Canonical JSON rule

The canonical JSON for signing/hashing is: `json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)`

**Never use model_dump() for signing or ID computation** — Pydantic coerces int→float which changes the canonical form. Always use the raw wire-format dict (from request.scope["_raw_payload"] or from DB JSON deserialization directly).

## Your Task

1. Read `prd.json` in the project root
2. Read `progress.txt` (check Codebase Patterns section first)
3. Ensure you are on branch `ralph/hackathon-prep` (create from main if needed)
4. Pick the **highest priority** story where `passes: false`
5. Implement that single story
6. Quality check: `cd backend && python -m py_compile *.py && echo "syntax OK"`
7. If checks pass, commit ALL changes: `feat: [Story ID] - [Story Title]`
8. Update `prd.json` to set `passes: true` for the completed story
9. Append progress to `progress.txt`

## Quality Check Command

```bash
cd /Users/athifshaffy/Documents/Repo/supply-chain-app/backend && python -m py_compile crypto.py graph.py canadian_content.py anomaly.py ledger.py middleware.py models.py registry.py db.py main.py seed.py seed_edge_cases.py && echo "All syntax OK"
```

No test suite exists yet — syntax check is the gate. If you write tests, add pytest as the quality check for future stories.

## Progress Report Format

APPEND to progress.txt (never replace):
```
## [Date/Time] - [Story ID]
- What was implemented
- Files changed
- **Learnings for future iterations:**
  - Patterns/gotchas discovered
---
```

## Codebase Patterns (seed these into progress.txt on first run)

- Attestation ID = SHA-256(canonical JSON of payload) — computed in crypto.py:content_addressed_id()
- Anomaly dicts always have: {type, attestation_id, detail} — some add extra fields
- chain_nodes from traverse_dag is pre-order DFS: index 0 = root (final product), leaves are last
- Ed25519 public keys stored as 64-char hex (32 raw bytes)
- asyncpg returns JSONB columns as strings sometimes — _parse_att() in graph.py handles this
- run_all_checks() in anomaly.py is the single entry point for all anomaly detection
- collect_chain_graceful() is used for /api/product (partial chains OK); collect_chain() is strict

## Stop Condition

After each story, if ALL stories in prd.json have `passes: true`, reply with:
<promise>COMPLETE</promise>

Otherwise end your response normally.

## Important

- ONE story per iteration
- Keep commits focused
- Do not break existing functionality
- The harness scores: (1) Canadian content correctness, (2) anomaly detection accuracy, (3) edge case handling
