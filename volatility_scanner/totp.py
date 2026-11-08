"""Opt-in FYERS TOTP login using internal, unsupported login endpoints."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import parse_qs, urlsplit

from curl_cffi import CurlHttpVersion, requests

from .auth import AuthError, request_tokens


def generate_totp(secret: str, timestamp: float | None = None) -> str:
    try:
        normalized = secret.strip().upper().replace(" ", "").rstrip("=")
        if not normalized or len(normalized) > 256:
            raise ValueError
        key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
        if len(key) < 10:
            raise ValueError
        counter = int((time.time() if timestamp is None else timestamp) // 30)
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 15
        value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
        return f"{value % 1000000:06d}"
    except (ValueError, TypeError, struct.error):
        raise AuthError("FYERS_TOTP_SECRET must contain the Base32 setup secret, not a six-digit code or QR URL.") from None


def _post(session, url: str, payload: dict, stage: str, *, bearer: str | None = None, status: int = 200) -> dict:
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    try:
        response = session.post(url, json=payload, headers=headers, timeout=15, allow_redirects=False)
        result = response.json()
    except (requests.RequestsError, ValueError):
        raise AuthError(f"FYERS TOTP login failed during {stage}. Check connection or use browser sign-in.") from None
    if response.status_code == 429:
        raise AuthError("FYERS rate-limited login (HTTP 429). Automatic attempts are stopped to avoid extending "
                        "the limit. Wait before retrying authenticate.py --totp or use browser sign-in.", retryable=False)
    if (response.status_code != status or not isinstance(result, dict) or result.get("s") == "error"
            or (isinstance(result.get("code"), (int, float)) and result["code"] < 0)):
        raise AuthError(f"FYERS TOTP login rejected during {stage}. Check credentials, TOTP setup and system clock; "
                        "use authenticate.py for browser sign-in if FYERS requires it.", retryable=False)
    return result


def _required(result: dict, key: str, stage: str) -> str:
    value = result.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuthError(f"FYERS TOTP login returned an invalid response during {stage}.")
    return value


def login_totp(client_id: str, app_secret: str, username: str, pin: str, secret: str, redirect_uri: str) -> dict:
    username = username.strip()
    if not username or len(username) > 64:
        raise AuthError("FYERS_USERNAME must contain your FYERS account login ID.")
    redirect = urlsplit(redirect_uri)
    app_id, separator, app_type = client_id.rpartition("-")
    if not separator or not app_id or not app_type.isdigit():
        raise AuthError("FYERS_CLIENT_ID must include its app type suffix, such as -100.")
    if redirect.scheme != "https" or not redirect.hostname or redirect.fragment or redirect.username:
        raise AuthError("FYERS_REDIRECT_URI must match your registered HTTPS redirect URI.")
    if len(pin) != 4 or not pin.isascii() or not pin.isdigit():
        raise AuthError("FYERS_PIN must contain exactly four digits, including leading zeros.")
    # Validate before contacting FYERS; never repeatedly submit malformed credentials.
    generate_totp(secret)
    state = secrets.token_urlsafe(32)
    base = "https://api-t2.fyers.in/vagator/v2"
    with requests.Session(http_version=CurlHttpVersion.V3) as session:
        result = _post(session, base + "/send_login_otp_v2", {
            "fy_id": base64.b64encode(username.encode()).decode(), "app_id": "2",
        }, "login initiation")
        request_key = _required(result, "request_key", "login initiation")
        remaining = 30 - time.time() % 30
        if remaining < 5:
            time.sleep(remaining + .1)
        result = _post(session, base + "/verify_otp", {
            "request_key": request_key, "otp": generate_totp(secret),
        }, "TOTP verification")
        request_key = _required(result, "request_key", "TOTP verification")
        result = _post(session, base + "/verify_pin_v2", {
            "request_key": request_key, "identity_type": "pin",
            "identifier": base64.b64encode(pin.encode()).decode(),
        }, "PIN verification")
        data = result.get("data")
        bearer = _required(data if isinstance(data, dict) else {}, "access_token", "PIN verification")
        result = _post(session, "https://api-t1.fyers.in/api/v3/token", {
            "fyers_id": username, "app_id": app_id, "appType": app_type,
            "redirect_uri": redirect_uri, "response_type": "code", "state": state,
            "nonce": secrets.token_urlsafe(16), "scope": "", "code_challenge": "", "create_cookie": True,
        }, "authorization", bearer=bearer, status=308)
        returned = urlsplit(_required(result, "Url", "authorization"))
        if (returned.scheme, returned.netloc, returned.path) != (redirect.scheme, redirect.netloc, redirect.path):
            raise AuthError("FYERS TOTP login returned an unexpected redirect URI.")
        query = parse_qs(returned.query)
        if query.get("state") != [state] or len(query.get("auth_code", [])) != 1:
            raise AuthError("FYERS TOTP login state or authorization code is invalid.")
        return request_tokens("validate-authcode", {
            "grant_type": "authorization_code",
            "appIdHash": hashlib.sha256(f"{client_id}:{app_secret}".encode()).hexdigest(),
            "code": query["auth_code"][0],
        })
