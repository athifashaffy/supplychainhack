from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import VerificationRequest, VerificationResult
from .verify import verify_supply_chain

app = FastAPI(title="Provenance Verification Baseline")


@app.exception_handler(RequestValidationError)
async def malformed_request_handler(_request: Request, exc: RequestValidationError):
    """
    Convert Pydantic schema failures into a structured MALFORMED anomaly.

    Without this, FastAPI returns HTTP 422 with its own error shape, which
    means adversarial test inputs (missing fields, wrong types) never reach
    the verifier and the harness sees an unexpected response shape. With
    this handler, every malformed bundle returns a 200 with a normal
    VerificationResult whose chain_status is UNVERIFIABLE.
    """
    errors = exc.errors()
    detail = errors[0]["msg"] if errors else "schema validation failed"
    loc = ".".join(str(p) for p in errors[0].get("loc", [])) if errors else ""
    return JSONResponse(
        status_code=200,
        content={
            "chain_status": "UNVERIFIABLE",
            "designation": None,
            "canadian_pct": None,
            "total_cost_cents": None,
            "canadian_cost_cents": None,
            "cost_by_country": {},
            "anomalies": [{
                "reason": "MALFORMED",
                "attestation_hash": "",
                "detail": f"{loc}: {detail}" if loc else detail,
                "advisory": False,
            }],
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/verify", response_model=VerificationResult)
def verify(req: VerificationRequest):
    return verify_supply_chain(req)
