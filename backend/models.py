from pydantic import BaseModel, field_validator
from typing import Optional
import re


class Location(BaseModel):
    country: str  # ISO 3166-1 alpha-2, e.g. "CA", "US"
    province: Optional[str] = None  # e.g. "ON", "BC"


class MaterialCost(BaseModel):
    name: str
    cost_cad: float
    country: str  # where material originated
    quantity: Optional[float] = None
    unit: Optional[str] = None


class LabourCost(BaseModel):
    cost_cad: float
    country: str  # where labour was performed
    hours: Optional[float] = None


class AttestationPayload(BaseModel):
    """The Statement that gets signed."""
    supplier_id: str
    product_name: str
    timestamp: str  # ISO 8601
    location: Location
    inputs: list[str] = []  # upstream attestation IDs consumed
    materials: list[MaterialCost] = []
    labour: Optional[LabourCost] = None
    transformation_description: Optional[str] = None
    quantity_produced: Optional[float] = None
    unit: Optional[str] = None


class AttestationSubmit(BaseModel):
    """Full submission envelope."""
    payload: AttestationPayload
    signature: str   # hex-encoded Ed25519 signature over canonical JSON of payload
    signer_id: str   # must match payload.supplier_id


class SupplierCreate(BaseModel):
    supplier_id: str
    name: str
    country: str
    province: Optional[str] = None
    public_key_hex: str  # 32-byte Ed25519 public key as hex (64 chars)

    @field_validator("public_key_hex")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("public_key_hex must be 64 hex characters (32 bytes)")
        return v


class VerificationResult(BaseModel):
    attestation_id: str
    valid_signature: bool
    known_signer: bool
    payload: Optional[dict] = None
    anomalies: list[str] = []


class ProvenanceReport(BaseModel):
    product_attestation_id: str
    product_name: str
    supplier_chain: list[dict]
    total_cost_cad: float
    canadian_cost_cad: float
    canadian_content_pct: float
    designation: str  # "Product of Canada", "Made in Canada", "Not Qualified", "Unknown"
    last_transformation_in_canada: bool
    anomalies: list[dict]
    dag_valid: bool
