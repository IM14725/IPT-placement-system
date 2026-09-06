import requests
import base64
import hmac
import hashlib
from django.conf import settings

def send_selcom_wallet_pull(transaction_id, phone_number, amount):
    """
    Sends a Direct USSD push payload targeting Selcom's checkout workflow.
    """
    base_url = getattr(settings, 'SELCOM_BASE_URL', 'https://apitest.selcommobile.com')
    url = f"{base_url}/v1/checkout/create-order-minimal"
    
    api_key = getattr(settings, 'SELCOM_API_KEY', 'MOCK_API_KEY')
    api_secret = getattr(settings, 'SELCOM_API_SECRET', 'MOCK_API_SECRET')
    vendor_till = getattr(settings, 'SELCOM_VENDOR_TILL', 'MOCK_TILL')

    # Base64 Auth header format commonly used by Selcom
    auth_str = f"{api_key}:{api_secret}"
    encoded_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')

    headers = {
        "Authorization": f"Basic {encoded_auth}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Structure minimum parameters requested by Selcom checkout order endpoints
    payload = {
        "vendor": vendor_till,
        "order_id": str(transaction_id),
        "buyer_phone": phone_number,
        "amount": int(amount), # Selcom expects integers for TZS amounts
        "currency": "TZS",
        "no_of_items": 1,
        "no_redirection": True # Instructs Selcom to execute a background USSD push directly
    }

    try:
        if api_key == 'MOCK_API_KEY':
            # Auto-approve the initiation locally when keys are mocked
            return 200, {"result": "SUCCESS", "transid": "MOCK-INITIATED-123", "message": "Mock prompt initiated."}
            
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        return response.status_code, response.json()
    except requests.exceptions.RequestException as e:
        return 500, {"result": "FAIL", "message": str(e)}


def verify_selcom_webhook_signature(raw_request_body, incoming_digest, incoming_timestamp):
    """
    Validates if the incoming payload header digests match our local calculation.
    """
    if not incoming_digest or not incoming_timestamp:
        return False

    api_secret = getattr(settings, 'SELCOM_API_SECRET', 'MOCK_API_SECRET')

    # 1. Recreate the string base that Selcom hashed.
    message_to_sign = f"timestamp={incoming_timestamp}&body={raw_request_body.decode('utf-8')}"
    
    # 2. Compute HMAC-SHA256 hash using your API Secret key
    secret_bytes = api_secret.encode('utf-8')
    message_bytes = message_to_sign.encode('utf-8')
    
    computed_hmac = hmac.new(
        secret_bytes, 
        msg=message_bytes, 
        digestmod=hashlib.sha256
    ).digest()
    
    # 3. Encode to Base64
    computed_digest = base64.b64encode(computed_hmac).decode('utf-8')
    
    # 4. Use a time-constant string comparison to safely neutralize timing attacks
    return hmac.compare_digest(computed_digest, incoming_digest)

