"""Generate and save an Upstox access token using configured TOTP credentials."""
from volatility_scanner.security.tokens import AuthError
from volatility_scanner.brokers.upstox.auth import ensure_upstox_token

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('market', 'algo'), default='market')
    args = parser.parse_args()
    try:
        ensure_upstox_token(force=True, profile=args.profile)
        print('Upstox TOTP login succeeded. Access token saved to .env.')
    except AuthError as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('Authentication cancelled.')
        raise SystemExit(1)
