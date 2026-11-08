"""Interactive FYERS login. Run locally; secrets are never printed."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from dotenv import dotenv_values, set_key

from volatility_scanner.data import ENV_FILE


def auth_code_from_redirect(url: str, redirect_uri: str, state: str) -> str:
    actual = urllib.parse.urlsplit(url.strip())
    expected = urllib.parse.urlsplit(redirect_uri)
    if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise ValueError("Returned URL does not match the registered redirect URI.")
    query = urllib.parse.parse_qs(actual.query)
    if query.get("state") != [state]:
        raise ValueError("Login state mismatch. Restart authentication.")
    codes = query.get("auth_code", [])
    if len(codes) != 1 or not codes[0]:
        raise ValueError("Login did not return an authorization code.")
    return codes[0]


def exchange_code(client_id: str, app_secret: str, code: str) -> str:
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": hashlib.sha256(f"{client_id}:{app_secret}".encode()).hexdigest(),
        "code": code,
    }
    request = urllib.request.Request(
        "https://api-t1.fyers.in/api/v3/validate-authcode",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f"FYERS rejected token exchange (HTTP {error.code}). Check app details and retry login.") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ValueError("Could not obtain a valid response from FYERS. Retry login.") from None
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("s") != "ok" or not isinstance(token, str) or not token.strip():
        raise ValueError("FYERS did not issue an access token. Check app details and retry login.")
    return token


def main() -> None:
    values = dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig")
    client_id = os.getenv("FYERS_CLIENT_ID", values.get("FYERS_CLIENT_ID"))
    if not client_id:
        raise ValueError("Set FYERS_CLIENT_ID in .env first.")
    redirect_uri = os.getenv("FYERS_REDIRECT_URI", values.get("FYERS_REDIRECT_URI")) or input(
        "Registered redirect URI [https://trade.fyers.in/api-login/redirect-uri/index.html]: "
    ).strip() or "https://trade.fyers.in/api-login/redirect-uri/index.html"
    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValueError("Use your registered HTTPS redirect URI without a fragment.")
    app_secret = (
        os.getenv("FYERS_APP_SECRET", values.get("FYERS_APP_SECRET"))
        or getpass.getpass("FYERS App Secret (hidden, not saved): ")
    ).strip()
    if not app_secret:
        raise ValueError("App Secret is required.")
    state = secrets.token_urlsafe(32)
    login_url = "https://api-t1.fyers.in/api/v3/generate-authcode?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
    })
    print("Open this URL in your browser and sign in to FYERS:\n" + login_url)
    webbrowser.open(login_url)
    returned_url = getpass.getpass("Paste the full redirected URL here (hidden): ")
    code = auth_code_from_redirect(returned_url, redirect_uri, state)
    token = exchange_code(client_id, app_secret, code)
    set_key(ENV_FILE, "FYERS_ACCESS_TOKEN", token, encoding="utf-8-sig")
    print("Access token saved to .env. Restart the dashboard or rerun the scan.")
    if "FYERS_ACCESS_TOKEN" in os.environ:
        print("Remove the old FYERS_ACCESS_TOKEN environment variable; it overrides .env.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        # OSError text may include sensitive request details; do not print it.
        print(str(error) if isinstance(error, ValueError) else "Could not read or save local configuration.")
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print("Authentication cancelled. Existing token unchanged.")
        raise SystemExit(1)
