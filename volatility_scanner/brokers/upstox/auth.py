"""Upstox authentication through the documented TOTP token endpoint."""
import os
import re
import threading
import time
from volatility_scanner.config import ENV_FILE
from pathlib import Path

from curl_cffi import requests
from dotenv import dotenv_values

from volatility_scanner.security.tokens import AuthError, save_tokens, token_expiry
from volatility_scanner.security.totp import generate_totp

_lock = threading.Lock()


def ensure_upstox_token(env_file=ENV_FILE, *, force=False, profile="market"):
    if profile not in ("market", "algo"):
        raise AuthError("Sandbox tokens are generated in the Upstox developer console")
    prefix = "UPSTOX_ALGO" if profile == "algo" else "UPSTOX"
    with _lock:
        values = dotenv_values(env_file, interpolate=False, encoding='utf-8-sig')
        def setting(name):
            profile_name = prefix + name.removeprefix('UPSTOX')
            value = os.getenv(profile_name, values.get(profile_name))
            if not value and name in ('UPSTOX_PIN', 'UPSTOX_TOTP_SECRET'):
                value = os.getenv(name, values.get(name))
            return value or ''
        current = setting('UPSTOX_ACCESS_TOKEN')
        expiry = token_expiry(current)
        if current and not force and (expiry is None or expiry > time.time() + 60):
            return current
        required = ('UPSTOX_API_KEY', 'UPSTOX_PIN', 'UPSTOX_TOTP_SECRET')
        missing = [key for key in required if not setting(key)]
        if missing:
            raise AuthError('Upstox TOTP login needs ' + ', '.join(missing) + ' in .env.')
        pin = setting('UPSTOX_PIN')
        if not re.fullmatch(r'[0-9]{6}', pin):
            raise AuthError('UPSTOX_PIN must be exactly six digits.')
        secret = setting('UPSTOX_TOTP_SECRET')
        try:
            generate_totp(secret)
        except AuthError:
            raise AuthError('UPSTOX_TOTP_SECRET must be the Base32 setup secret, not a current code.') from None
        remaining = 30 - time.time() % 30
        if remaining < 3:
            time.sleep(remaining + 0.1)
        try:
            response = requests.post(
                'https://api.upstox.com/v2/totp-login/authorization/token',
                headers={'Accept': 'application/json', 'x-api-key': setting('UPSTOX_API_KEY')},
                json={'pin': pin, 'totp': generate_totp(secret)},
                timeout=20, allow_redirects=False,
            )
            if response.status_code != 200:
                raise AuthError(f'Upstox TOTP login rejected (HTTP {response.status_code}); '
                                'check API key, PIN, enabled TOTP and system clock. No automatic retry.',
                                retryable=False)
            result = response.json()
        except AuthError:
            raise
        except Exception:
            raise AuthError('Upstox token request failed; existing token preserved.') from None
        token = result.get('access_token') if isinstance(result, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise AuthError('Upstox did not return a valid access token; existing token preserved.')
        other = 'UPSTOX' if prefix == 'UPSTOX_ALGO' else 'UPSTOX_ALGO'
        other_key = os.getenv(other + '_API_KEY', values.get(other + '_API_KEY'))
        aliases = (other,) if other_key and other_key == setting('UPSTOX_API_KEY') else ()
        save_tokens(Path(env_file), {'access_token': token}, broker=prefix, aliases=aliases)
        return token
