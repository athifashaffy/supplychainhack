from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ChainStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Designation(str, Enum):
    PRODUCT_OF_CANADA = "PRODUCT_OF_CANADA"
    MADE_IN_CANADA = "MADE_IN_CANADA"
    NONE = "NONE"


class Reason(str, Enum):
    MALFORMED = "MALFORMED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    UNKNOWN_ISSUER = "UNKNOWN_ISSUER"
    HASH_MISMATCH = "HASH_MISMATCH"
    BROKEN_LINK = "BROKEN_LINK"
    CYCLE = "CYCLE"
    DUPLICATE_HASH = "DUPLICATE_HASH"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    MASS_BALANCE = "MASS_BALANCE"
    TEMPORAL_INVERSION = "TEMPORAL_INVERSION"
    ADVISORY_ANOMALY = "ADVISORY_ANOMALY"


class Output(BaseModel):
    product_id: str
    quantity: int = Field(ge=0)
    unit: str = "unit"


class InputRef(BaseModel):
    attestation_hash: str
    quantity_used: int = Field(ge=0)


class Attestation(BaseModel):
    # In the real event, adapters should map the official schema into this model.
    hash: str
    supplier_id: str
    output: Output
    inputs: List[InputRef] = Field(default_factory=list)

    materials_cents: int = Field(ge=0)
    labour_cents: int = Field(ge=0)
    work_country: str
    is_substantial_transformation: bool = False
    timestamp: str = ""

    # Mock fields for pre-event development.
    # Replace with real canonicalization + Ed25519 verification in adapters.py.
    signature_valid: bool = True
    signer_known: bool = True


class Anomaly(BaseModel):
    reason: Reason
    attestation_hash: str
    detail: str
    advisory: bool = False


class Node(BaseModel):
    attestation: Attestation
    input_hashes: List[str] = Field(default_factory=list)
    consumer_hashes: List[str] = Field(default_factory=list)


class VerificationRequest(BaseModel):
    root_hash: str
    attestations: List[Attestation]


class VerificationResult(BaseModel):
    chain_status: ChainStatus
    designation: Optional[Designation]
    canadian_pct: Optional[float]
    total_cost_cents: Optional[int]
    canadian_cost_cents: Optional[int]
    cost_by_country: Dict[str, int]
    anomalies: List[Anomaly]
