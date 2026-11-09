"""Generate and save an Upstox access token using configured TOTP credentials."""
from volatility_scanner.auth import AuthError
from volatility_scanner.upstox_auth import ensure_upstox_token

if __name__ == '__main__':
    try:
        ensure_upstox_token(force=True)
        print('Upstox TOTP login succeeded. Access token saved to .env.')
    except AuthError as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('Authentication cancelled.')
        raise SystemExit(1)
