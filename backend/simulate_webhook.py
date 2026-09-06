import sys
import requests
import json
import hmac
import hashlib
import base64
from datetime import datetime
from datetime import datetime, timezone

LOCAL_WEBHOOK_URL = "http://127.0.0.1:8000/api/payments/webhook/"
# Needs to match SELCOM_API_SECRET in your settings
MOCK_API_SECRET = "MOCK_API_SECRET"

def send_signed_mock_webhook(transaction_uuid, outcome="SUCCESS"):
    """
    Simulates a payload hitting your DRF backend exactly like Selcom would, including the cryptographic signature.
    """
    result_code = "000" if outcome.upper() == "SUCCESS" else "001"
    timestamp = datetime.utcnow().isoformat()
    timestamp = datetime.now(timezone.utc).isoformat()
    
    payload = {
        "order_id": transaction_uuid,
        "resultcode": result_code,
        "amount": 1000,
        "msisdn": "255754000000",
        "transid": "MOCK-SELCOM-TX-99999"
    }

    # Replicate structural matching string
    json_bytes = json.dumps(payload).encode('utf-8')
    message_to_sign = f"timestamp={timestamp}&body={json_bytes.decode('utf-8')}"
    
    computed_hmac = hmac.new(
        MOCK_API_SECRET.encode('utf-8'), 
        msg=message_to_sign.encode('utf-8'), 
        digestmod=hashlib.sha256
    ).digest()
    
    mock_digest = base64.b64encode(computed_hmac).decode('utf-8')
    
    headers = {
        "Content-Type": "application/json",
        "Digest": mock_digest,
        "Timestamp": timestamp
    }
    
    print(f"Sending signed mock payload to {LOCAL_WEBHOOK_URL}...")
    try:
        response = requests.post(LOCAL_WEBHOOK_URL, data=json_bytes, headers=headers)
        print(f"Response Status Code: {response.status_code}")
        print(f"Response Content: {response.json()}")
    except requests.exceptions.ConnectionError:
        print("❌ Error: Could not connect to Django server. Is runserver active on port 8000?")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("💡 Usage: python simulate_webhook.py <TRANSACTION_UUID> [SUCCESS/FAIL]")
        sys.exit(1)
        
    tx_uuid = sys.argv[1]
    status_outcome = sys.argv[2] if len(sys.argv) > 2 else "SUCCESS"
    send_signed_mock_webhook(tx_uuid, status_outcome)

