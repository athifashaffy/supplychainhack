"""
Ed25519 Cryptographic Integrity Middleware.

This Starlette BaseHTTPMiddleware intercepts every POST /api/attest request
before it reaches the route handler and performs three pre-flight checks:

  1. Envelope coherence  — signer_id must match payload.supplier_id
  2. Registry lookup     — signer must be in the verified supplier registry
  3. Signature verify    — Ed25519 signature over canonical JSON must be valid

Any failure raises IntegrityViolationError, which the registered exception
handler converts to an HTTP 422 response.  The original request body is
re-injected into the ASGI receive channel so the downstream FastAPI route
handler reads an intact, unmodified Request.

Why middleware rather than a Depends():
  A middleware runs unconditionally before the route is selected, making it
  impossible to bypass by calling a different handler.  A Depends() is
  opt-in per route and can be accidentally omitted.  For a security
  enforcement point, middleware is the correct abstraction.
"""

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from crypto import verify_signature, content_addressed_id
from exceptions import IntegrityViolationError

logger = logging.getLogger(__name__)

# Only intercept this path+method combination.
_INTERCEPTED_PATH = "/api/attest"


class AttestationIntegrityMiddleware(BaseHTTPMiddleware):
    """
    Pre-route cryptographic verification middleware.

    Registered in main.py via:
        app.add_middleware(AttestationIntegrityMiddleware)

    The app's DB pool must be stored on app.state.pool before requests arrive
    (done in the startup event handler in main.py).
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "POST" or request.url.path != _INTERCEPTED_PATH:
            return await call_next(request)

        # ── 1. Read and parse body ────────────────────────────────────────────
        body_bytes = await request.body()
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JSONResponse({"detail": f"Malformed JSON: {exc}"}, status_code=400)

        # ── 2. Cryptographic pre-flight ───────────────────────────────────────
        try:
            await _verify_attestation_body(request, body)
        except IntegrityViolationError as exc:
            logger.warning(
                "IntegrityViolationError on POST /api/attest — %s", exc.detail
            )
            return JSONResponse(exc.to_dict(), status_code=422)

        # ── 3. Store raw payload dict in scope so api_attest can use it ──────
        #    We must sign/verify against the wire-format payload, not the
        #    Pydantic model_dump() version, which coerces int→float (e.g.
        #    cost_cad:1 → 1.0) producing a different canonical JSON and
        #    therefore a different ID and signature digest.
        request.scope["_raw_payload"] = body.get("payload", {})

        # ── 4. Re-inject body so the route handler can still read it ──────────
        #    Starlette's BaseHTTPMiddleware consumes the receive channel; we must
        #    replace it with a coroutine that replays the original bytes.
        async def _replay_body():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request = Request(request.scope, _replay_body)
        return await call_next(request)


async def _verify_attestation_body(request: Request, body: dict) -> None:
    """
    Perform the three pre-flight checks against the parsed envelope.

    Raises IntegrityViolationError with a descriptive message on any failure.
    Must be called only on POST /api/attest bodies.
    """
    from registry import get_supplier  # late import to avoid circular deps

    # Grab pool from app state (populated during startup event)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        # Should never happen in production; fail fast rather than silently skip.
        raise IntegrityViolationError(
            "Database pool not available — server not fully initialized"
        )

    payload_dict: dict | None = body.get("payload")
    signature_hex: str | None = body.get("signature")
    signer_id: str | None = body.get("signer_id")

    # ── Check A: envelope completeness ────────────────────────────────────────
    missing = [f for f, v in [
        ("payload", payload_dict),
        ("signature", signature_hex),
        ("signer_id", signer_id),
    ] if not v]
    if missing:
        raise IntegrityViolationError(
            f"Missing required envelope fields: {', '.join(missing)}"
        )

    # ── Check B: signer_id matches payload.supplier_id ────────────────────────
    payload_supplier_id = payload_dict.get("supplier_id")  # type: ignore[union-attr]
    if signer_id != payload_supplier_id:
        raise IntegrityViolationError(
            f"Envelope signer_id '{signer_id}' does not match "
            f"payload.supplier_id '{payload_supplier_id}' — possible impersonation"
        )

    # ── Check C: signer must be in the verified registry ─────────────────────
    supplier = await get_supplier(pool, signer_id)
    if supplier is None:
        att_id = content_addressed_id(payload_dict)  # type: ignore[arg-type]
        raise IntegrityViolationError(
            f"Signer '{signer_id}' is not in the verified supplier registry",
            attestation_id=att_id,
        )

    # ── Check D: Ed25519 signature validity ───────────────────────────────────
    pub_hex: str = supplier["public_key_hex"]
    if not verify_signature(payload_dict, signature_hex, pub_hex):  # type: ignore[arg-type]
        att_id = content_addressed_id(payload_dict)  # type: ignore[arg-type]
        raise IntegrityViolationError(
            f"Ed25519 signature verification failed for signer '{signer_id}' — "
            "payload was likely tampered with after signing",
            attestation_id=att_id,
        )
