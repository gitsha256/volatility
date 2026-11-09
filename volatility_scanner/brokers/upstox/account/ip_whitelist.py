"""User-level static IP management through the documented token-authenticated API."""
from dataclasses import replace
import ipaddress

from volatility_scanner.brokers.upstox.auth import ensure_upstox_token
from volatility_scanner.security.tokens import AuthError


def normalize_ip(value):
    if not isinstance(value, str) or '%' in value:
        raise ValueError('Expected an IPv4 or IPv6 address without a scope or network prefix')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError('Expected a valid IPv4 or IPv6 address') from None


def validate_ips(body):
    if not isinstance(body, dict) or set(body) - {'primary_ip', 'secondary_ip'}:
        raise ValueError('Expected primary_ip and optional secondary_ip')
    primary = normalize_ip(body.get('primary_ip'))
    secondary = normalize_ip(body['secondary_ip']) if 'secondary_ip' in body else None
    if primary == secondary:
        raise ValueError('Primary and secondary IP must differ')


def update_static_ips(client, primary_ip, secondary_ip=None, *, env_file=None):
    """Preserve omitted secondary IP, skip unchanged writes, renew after successful PUT.

    The caller must enable live mutations on its APIClient. A PUT is never retried:
    updates invalidate tokens and consume Upstox's weekly change allowance.
    """
    primary_ip = normalize_ip(primary_ip)
    if secondary_ip is not None:
        secondary_ip = normalize_ip(secondary_ip)
    validate_ips(dict(primary_ip=primary_ip, **({'secondary_ip': secondary_ip} if secondary_ip else {})))
    current = client.account.getUserIps()['data']
    if not isinstance(current, dict):
        raise ValueError('Unexpected static IP configuration; update not sent')
    secondary = secondary_ip if secondary_ip is not None else current.get('secondary_ip')
    body = {'primary_ip': primary_ip}
    if secondary:
        body['secondary_ip'] = normalize_ip(secondary)
    validate_ips(body)
    if (current.get('primary_ip') and normalize_ip(current['primary_ip']) == primary_ip
            and (normalize_ip(current['secondary_ip']) if current.get('secondary_ip') else None)
            == body.get('secondary_ip')):
        return dict(status='success', changed=False, data=current)
    # Check routing/permissions before the single irreversible weekly update.
    result = client.account.updateUserIp(body=body)
    if result.get('status') != 'success':
        raise ValueError('IP update did not report success; reconcile before retrying')
    result = dict(result, changed=True)
    try:
        token = ensure_upstox_token(force=True, profile=client.profile,
                                   **({'env_file': env_file} if env_file is not None else {}))
        client.credential = replace(client.credential, access_token=token)
        result['token_renewal'] = 'success'
    except AuthError:
        result['token_renewal'] = 'failed; IPs were updated, renew credentials before further API calls'
    return result
