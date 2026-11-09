"""FYERS token renewal. JWT expiry is a scheduling hint, never authentication."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.request

from dotenv import dotenv_values, set_key


class AuthError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


_lock = threading.RLock()
_failed_refresh: dict[str, tuple[float, str]] = {}


def token_expiry(token: str | None) -> float | None:
    try:
        if not token or len(token) > 65536:
            return None
        payload = token.split(".")[1]
        expiry = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp")
        if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) and math.isfinite(expiry):
            return float(expiry)
    except (ValueError, IndexError, AttributeError, TypeError):
        pass
    return None


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


def save_tokens(env_file: Path, tokens: dict, *, broker: str = "FYERS") -> None:
    """Replace token keys together, preserving other settings and existing file on failure."""
    if broker not in ("FYERS", "UPSTOX"):
        raise ValueError("Unsupported broker")
    with _lock:
        temporary = None
        try:
            text = env_file.read_text(encoding="utf-8-sig") if env_file.exists() else ""
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", dir=env_file.parent,
                                             prefix=".env.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            for name in ("access_token", "refresh_token"):
                value = tokens.get(name)
                if isinstance(value, str) and value.strip():
                    set_key(temporary, broker + "_" + name.upper(), value, encoding="utf-8-sig")
            os.replace(temporary, env_file)
        except OSError:
            raise AuthError("Could not save renewed tokens to .env. Existing configuration was preserved.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        for name in ("access_token", "refresh_token"):
            key = broker + "_" + name.upper()
            if key in os.environ and isinstance(tokens.get(name), str) and tokens[name].strip():
                os.environ[key] = tokens[name]


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
                from .totp import login_totp
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
