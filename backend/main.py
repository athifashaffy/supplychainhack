"""
Supply Chain Provenance — FastAPI backend
TAN Hackathon: Verified Canadian Supply Chains
"""

import json
import io
import asyncpg
from graph import _parse_att as _pa
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

import qrcode

from db import get_pool, init_schema
from models import AttestationSubmit, SupplierCreate, VerificationResult, ProvenanceReport
from crypto import content_addressed_id, verify_signature
from registry import get_supplier, list_suppliers, register_supplier
from graph import collect_chain
from canadian_content import compute_content
from anomaly import run_all_checks
from exceptions import IntegrityViolationError
from middleware import AttestationIntegrityMiddleware

app = FastAPI(title="TAN Supply Chain Provenance", version="1.0.0")

# ── Middleware (order matters: CORS first, then integrity check) ───────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cryptographic integrity enforcement on POST /api/attest.
# Must be added AFTER CORSMiddleware so CORS preflight OPTIONS pass through.
app.add_middleware(AttestationIntegrityMiddleware)


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(IntegrityViolationError)
async def integrity_violation_handler(request: Request, exc: IntegrityViolationError):
    """Convert IntegrityViolationError → HTTP 422 with structured body."""
    return JSONResponse(exc.to_dict(), status_code=422)


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    pool = await get_pool()
    # Store on app.state so the integrity middleware can access it
    app.state.pool = pool
    await init_schema(pool)


# ── Dependency ────────────────────────────────────────────────────────────────

async def db() -> asyncpg.Pool:
    return await get_pool()


# ── Suppliers ─────────────────────────────────────────────────────────────────

@app.get("/api/suppliers")
async def api_list_suppliers(pool=Depends(db)):
    return await list_suppliers(pool)


@app.post("/api/suppliers", status_code=201)
async def api_register_supplier(body: SupplierCreate, pool=Depends(db)):
    return await register_supplier(
        pool,
        supplier_id=body.supplier_id,
        name=body.name,
        country=body.country,
        province=body.province,
        public_key_hex=body.public_key_hex,
    )


# ── Attestations ──────────────────────────────────────────────────────────────

@app.post("/api/attest", status_code=201)
async def api_attest(body: AttestationSubmit, pool=Depends(db)):
    payload_dict = body.payload.model_dump()

    # Compute content-addressed ID
    att_id = content_addressed_id(payload_dict)

    # Signature validity (AttestationIntegrityMiddleware has already verified
    # this for known signers before we reach here; we re-run to populate the
    # sig_valid column correctly in the DB record).
    supplier = await get_supplier(pool, body.signer_id)
    sig_valid = (
        verify_signature(payload_dict, body.signature, supplier["public_key_hex"])
        if supplier else False
    )

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO attestations (id, payload, signature, signer_id, sig_valid)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
        """, att_id, json.dumps(payload_dict), body.signature, body.signer_id, sig_valid)

        # Store input edges
        for input_id in (body.payload.inputs or []):
            await conn.execute("""
                INSERT INTO attestation_inputs (attestation_id, input_id)
                VALUES ($1, $2) ON CONFLICT DO NOTHING
            """, att_id, input_id)

    return {
        "attestation_id": att_id,
        "signature_valid": sig_valid,
        "message": "Attestation recorded" + ("" if sig_valid else " (WARNING: invalid signature)"),
    }


@app.get("/api/attest/{att_id}")
async def api_get_attestation(att_id: str, pool=Depends(db)):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attestations WHERE id = $1", att_id)
    if row is None:
        raise HTTPException(404, "Attestation not found")
    return _pa(dict(row))


@app.get("/api/attestations")
async def api_list_attestations(pool=Depends(db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, signer_id, sig_valid, created_at, payload->>'product_name' AS product_name "
            "FROM attestations ORDER BY created_at DESC LIMIT 100"
        )
    return [dict(r) for r in rows]


# ── Verification ──────────────────────────────────────────────────────────────

@app.get("/api/verify/{att_id}", response_model=VerificationResult)
async def api_verify(att_id: str, pool=Depends(db)):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attestations WHERE id = $1", att_id)
    if row is None:
        raise HTTPException(404, "Attestation not found")

    record = _pa(dict(row))
    payload = record["payload"]
    signer_id = record["signer_id"]
    signature = record["signature"]

    supplier = await get_supplier(pool, signer_id)
    known_signer = supplier is not None
    valid_sig = (
        verify_signature(payload, signature, supplier["public_key_hex"])
        if known_signer else False
    )

    anomalies_list = []
    if not known_signer:
        anomalies_list.append(f"Unknown signer: {signer_id}")
    if not valid_sig:
        anomalies_list.append("Invalid Ed25519 signature")

    return VerificationResult(
        attestation_id=att_id,
        valid_signature=valid_sig,
        known_signer=known_signer,
        payload=payload,
        anomalies=anomalies_list,
    )


# ── Provenance Report ─────────────────────────────────────────────────────────

@app.get("/api/product/{att_id}")
async def api_product(att_id: str, pool=Depends(db)):
    # 1. Traverse DAG
    chain_nodes, structural_anomalies = await collect_chain(pool, att_id)

    dag_valid = len(structural_anomalies) == 0

    if not chain_nodes:
        # Try single node if chain collection failed
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM attestations WHERE id = $1", att_id)
        if row is None:
            raise HTTPException(404, "Attestation not found")
        chain_nodes = [_pa(dict(row))]

    # 2. Compute Canadian content
    content = compute_content(chain_nodes)

    # 3. Run anomaly checks
    all_anomalies = await run_all_checks(chain_nodes, pool, structural_anomalies)

    # 4. Build supplier chain summary
    supplier_chain = []
    for node in chain_nodes:
        p = node["payload"]
        supplier_chain.append({
            "attestation_id": node["id"],
            "product_name": p.get("product_name", ""),
            "supplier_id": node["signer_id"],
            "location": p.get("location", {}),
            "inputs": p.get("inputs", []),
            "sig_valid": node["sig_valid"],
        })

    root_payload = chain_nodes[0]["payload"] if chain_nodes else {}

    return {
        "product_attestation_id": att_id,
        "product_name": root_payload.get("product_name", "Unknown"),
        "supplier_chain": supplier_chain,
        "total_cost_cad": content["total_cost_cad"],
        "canadian_cost_cad": content["canadian_cost_cad"],
        "canadian_content_pct": content["canadian_content_pct"],
        "designation": content["designation"],
        "last_transformation_in_canada": content["last_transformation_in_canada"],
        "cost_breakdown": content["cost_breakdown"],
        "anomalies": all_anomalies,
        "dag_valid": dag_valid,
        "chain_length": len(chain_nodes),
    }


# ── QR Code ───────────────────────────────────────────────────────────────────

@app.get("/api/qr/{att_id}")
async def api_qr(att_id: str):
    import os
    frontend = os.environ.get("FRONTEND_URL", "https://thealphanova.com/provenance")
    url = f"{frontend}/purchaser.html?id={att_id}"
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#003087", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# ── Seed ──────────────────────────────────────────────────────────────────────

@app.post("/api/seed")
async def api_seed(pool=Depends(db)):
    from seed import seed
    result = await seed(pool)
    return result


@app.post("/api/seed/edge-cases")
async def api_seed_edge_cases(pool=Depends(db)):
    from seed_edge_cases import seed_edge_cases
    result = await seed_edge_cases(pool)
    return result


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
