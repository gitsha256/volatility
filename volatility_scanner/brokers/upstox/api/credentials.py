"""Read credentials once; never include their values in object representations."""
from dataclasses import dataclass, field
import os
from dotenv import dotenv_values
from volatility_scanner.config import ENV_FILE
from volatility_scanner.security.tokens import AuthError


@dataclass(frozen=True, slots=True)
class Credentials:
    profile: str
    access_token: str = field(repr=False)
    api_key: str = field(default='', repr=False)
    api_secret: str = field(default='', repr=False)
    redirect_uri: str = ''


def credentials(profile='sandbox', env_file=ENV_FILE):
    if profile not in ('sandbox', 'algo', 'market'):
        raise ValueError('Profile must be sandbox, algo or market')
    values = dotenv_values(env_file, interpolate=False, encoding='utf-8-sig')
    prefix = {'sandbox': 'UPSTOX_SANDBOX', 'algo': 'UPSTOX_ALGO', 'market': 'UPSTOX'}[profile]
    def setting(name):
        return os.getenv(prefix + '_' + name, values.get(prefix + '_' + name)) or ''
    token = setting('ACCESS_TOKEN')
    if not token and (profile == "sandbox" or not setting("API_KEY")):
        raise AuthError(prefix + "_ACCESS_TOKEN is not configured")
    secret = setting('API_SECRET')
    if profile == 'market' and not secret:
        secret = os.getenv('UPSTOX_APP_SECRET', values.get('UPSTOX_APP_SECRET')) or values.get('UUPSTOX_APP_SECRET') or ''
    return Credentials(profile, token, setting('API_KEY'), secret, setting('REDIRECT_URI'))
