import json
import hashlib
import requests
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

import serial
import time

def simulate_hardware_attestation():
    print("--- INITIATING SECURE BOOT SEQUENCE ---")
    
    # 1. HARDWARE ROOT OF TRUST: "Burned-in" silicon key
    # This 32-byte seed ensures the hardware always boots with the same identity
    seed = b"STM32_NUCLEO_F446ZE_SECURE_SEED!" 
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    public_key = private_key.public_key()
    
    public_hex = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    ).hex()
    print(f"[HW] Ed25519 Public Key Initialized: {public_hex}")

    # 2. SENSOR ACQUISITION
    current_time = datetime.now(timezone.utc).isoformat()
    
    drone_part_data = {
        "supplier_id": "SUPPLIER_EDGE_02", # Changed to 02 for our permanent hardware
        "product_name": "High-Speed Optical Interconnect",
        "timestamp": current_time,
        "location": {
            "country": "CA",
            "province": "ON"
        },
        "inputs": [], 
        "materials": [
            {
                "name": "Optical Fibers",
                "cost_cad": 120.50,
                "country": "CA",
                "quantity": 10.0,
                "unit": "meters"
            }
        ],
        "labour": {
            "cost_cad": 45.00,
            "country": "CA",
            "hours": 1.5
        },
        "transformation_description": "Assembled and tested optical interconnects",
        "quantity_produced": 1.0,
        "unit": "unit"
    }

    # 3. DETERMINISTIC SERIALIZATION
    canonical_payload = json.dumps(drone_part_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    
    # 4. CONTENT-ADDRESSED ID
    attestation_id = hashlib.sha256(canonical_payload).hexdigest()
    print(f"[HW] Generated SHA-256 Attestation ID: {attestation_id}")

    # 5. BARE-METAL SIGNING
    signature = private_key.sign(canonical_payload).hex()
    print(f"[HW] Payload signed securely. Signature: {signature[:16]}...")

    # 6. TRANSMISSION TO FASTAPI BACKEND 
    network_envelope = {
        "payload": drone_part_data,
        "signature": signature,
        "signer_id": "SUPPLIER_EDGE_02"
    }

    print("\n[HW] Transmitting over network to /api/attest...")
    
    try:
        response = requests.post("http://localhost:8000/api/attest", json=network_envelope)
        print(f"[SERVER RESPONSE] Status: {response.status_code}")
        print(f"[SERVER BODY] {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        print(f"[NETWORK ERROR] Could not reach backend: {e}")

#if __name__ == "__main__":
 #   simulate_hardware_attestation()

if __name__ == "__main__":
    # CHANGE 'COM3' TO YOUR ACTUAL STM32 COM PORT
    STM32_PORT = "COM3" 
    BAUD_RATE = 115200

    print(f"[*] Waiting for hardware interrupt on {STM32_PORT}...")
    
    try:
        # Open the serial connection to the STM32
        ser = serial.Serial(STM32_PORT, BAUD_RATE, timeout=1)
        
        while True:
            if ser.in_waiting > 0:
                # Read the incoming data from the USB cable
                line = ser.readline().decode('utf-8').strip()
                
                if line == "SIGN":
                    print("\n[>>>] HARDWARE INTERRUPT RECEIVED! BUTTON PRESSED.")
                    simulate_hardware_attestation()
                    print(f"\n[*] Waiting for next hardware interrupt on {STM32_PORT}...")
                    
    except serial.SerialException:
        print(f"[!] Error: Could not open {STM32_PORT}. Is the board plugged in and the port correct?")
    except KeyboardInterrupt:
        print("\n[*] Exiting Secure Hardware Monitor.")