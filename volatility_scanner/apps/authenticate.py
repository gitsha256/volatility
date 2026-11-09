"""Interactive FYERS login. Run locally; secrets are never printed."""
from __future__ import annotations

import getpass
import hashlib
import os
import secrets
import urllib.parse
import webbrowser

from dotenv import dotenv_values

from volatility_scanner.market.data import ENV_FILE
from volatility_scanner.brokers.fyers.auth import AuthError, ensure_access_token, request_tokens, save_tokens


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


def exchange_code(client_id: str, app_secret: str, code: str) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": hashlib.sha256(f"{client_id}:{app_secret}".encode()).hexdigest(),
        "code": code,
    }
    return request_tokens("validate-authcode", payload)


def main(*, refresh: bool = False, totp: bool = False) -> None:
    values = dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig")
    client_id = os.getenv("FYERS_CLIENT_ID", values.get("FYERS_CLIENT_ID"))
    if not client_id:
        raise ValueError("Set FYERS_CLIENT_ID in .env first.")
    if refresh or totp:
        ensure_access_token(client_id, os.getenv("FYERS_ACCESS_TOKEN", values.get("FYERS_ACCESS_TOKEN")), ENV_FILE,
                            force=True, auth_mode="totp" if totp else None)
        print("Access token renewed and saved. Dashboard reconnects automatically.")
        return
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
    tokens = exchange_code(client_id, app_secret, code)
    save_tokens(ENV_FILE, tokens)
    print("Tokens saved to .env. Dashboard reconnects automatically.")
    print("To opt into TOTP login, configure FYERS_AUTH_MODE=totp and the TOTP setup fields in .env.")


if __name__ == "__main__":
    try:
        import argparse
        parser = argparse.ArgumentParser(description="Sign in to FYERS or renew a saved token.")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--refresh", action="store_true", help="Renew using the configured authentication mode.")
        group.add_argument("--totp", action="store_true", help="Sign in using saved TOTP credentials without a browser.")
        args = parser.parse_args()
        main(refresh=args.refresh, totp=args.totp)
    except (ValueError, OSError, AuthError) as error:
        # OSError text may include sensitive request details; do not print it.
        print(str(error) if isinstance(error, (ValueError, AuthError)) else "Could not read or save local configuration.")
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print("Authentication cancelled. Existing token unchanged.")
        raise SystemExit(1)
