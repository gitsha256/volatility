"""FYERS token renewal. JWT expiry is a scheduling hint, never authentication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from dotenv import dotenv_values
from volatility_scanner.security.tokens import AuthError, token_expiry, save_tokens, _lock


_failed_refresh: dict[str, tuple[float, str]] = {}


def request_tokens(endpoint: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"https://api-t1.fyers.in/api/v3/{endpoint}",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            result = json.load(error)
        except (ValueError, OSError):
            raise AuthError("FYERS token request failed. Check connection and authentication configuration.") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise AuthError("FYERS token request failed. Check connection and authentication configuration.") from None
    if (isinstance(result, dict) and result.get("code") == -16
            and "disabled" in str(result.get("message", "")).lower()):
        raise AuthError("FYERS has disabled the refresh-token API. Automatic renewal is unavailable; "
                        "run authenticate.py to sign in through your browser.")
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("s") != "ok" or not isinstance(token, str) or not token.strip():
        raise AuthError("FYERS rejected token renewal. Check FYERS_PIN or run authenticate.py to sign in again.")
    return result


def ensure_access_token(client_id: str, access_token: str | None, env_file: Path, *, force: bool = False,
                        auth_mode: str | None = None) -> str:
    with _lock:
        values = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
        def setting(key):
            return os.getenv(key, values.get(key))

        current = setting("FYERS_ACCESS_TOKEN") or access_token
        expiry = token_expiry(current)
        # Another caller may already have replaced the rejected token.
        if current and (expiry is None or expiry > time.time() + 60) and (not force or current != access_token):
            return current
        refresh = setting("FYERS_REFRESH_TOKEN")
        secret = setting("FYERS_APP_SECRET")
        pin = setting("FYERS_PIN")
        mode = (auth_mode or setting("FYERS_AUTH_MODE") or "refresh").strip().lower()
        if mode not in ("refresh", "totp", "browser"):
            raise AuthError("FYERS_AUTH_MODE must be browser, totp or refresh.")
        if mode == "browser":
            raise AuthError("FYERS sign-in required. Run authenticate.py to log in through your browser.")
        username = setting("FYERS_USERNAME")
        totp_secret = setting("FYERS_TOTP_SECRET")
        redirect_uri = setting("FYERS_REDIRECT_URI") or "https://trade.fyers.in/api-login/redirect-uri/index.html"
        if mode == "totp":
            missing = [key for key in ("FYERS_USERNAME", "FYERS_TOTP_SECRET", "FYERS_APP_SECRET", "FYERS_PIN") if not setting(key)]
            if missing:
                raise AuthError("TOTP login needs " + ", ".join(missing) + " in .env.")
        if mode == "refresh" and not all((refresh, secret, pin)):
            raise AuthError("Automatic renewal needs FYERS_REFRESH_TOKEN, FYERS_APP_SECRET and FYERS_PIN. "
                            "Set your PIN in .env and run authenticate.py once to save both tokens.")
        refresh_expiry = token_expiry(refresh)
        if mode == "refresh" and refresh_expiry is not None and refresh_expiry <= time.time():
            raise AuthError("FYERS refresh token expired. Run authenticate.py to sign in again.")
        identity = hashlib.sha256(f"{mode}:{client_id}:{secret}:{refresh}:{pin}:{username}:{totp_secret}:{redirect_uri}".encode()).hexdigest()
        failure = _failed_refresh.get(identity)
        if failure and time.time() < failure[0]:
            raise AuthError(failure[1])
        try:
            if mode == "totp":
                from volatility_scanner.brokers.fyers.totp import login_totp
                result = login_totp(client_id, secret, username, pin, totp_secret, redirect_uri)
            else:
                result = request_tokens("validate-refresh-token", {
                    "grant_type": "refresh_token",
                    "appIdHash": hashlib.sha256(f"{client_id}:{secret}".encode()).hexdigest(),
                    "refresh_token": refresh, "pin": pin,
                })
            expiry = token_expiry(result["access_token"])
            if expiry is not None and expiry <= time.time() + 60:
                raise AuthError("FYERS returned an already-expiring token. Retry authentication later.")
            save_tokens(env_file, result)
        except AuthError as error:
            _failed_refresh[identity] = (time.time() + 60 if error.retryable else float("inf"), str(error))
            raise
        _failed_refresh.pop(identity, None)
        return result["access_token"]
