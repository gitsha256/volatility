"""Inspect or call a documented Upstox operation. Sandbox is the default profile."""
import argparse
import json
from pathlib import Path

from volatility_scanner.brokers.upstox.api.catalog import catalogue
from volatility_scanner.brokers.upstox.api.client import APIClient, APIError, RequestError
from volatility_scanner.security.tokens import AuthError

SENSITIVE = {'accesstoken','refreshtoken','extendedtoken','token','clientsecret','apisecret','apikey','pin','totp',
             'xapikey','authorizedredirecturi'}


def redact(value):
    if isinstance(value, dict):
        return {k: '[redacted]' if k.lower().replace('_','').replace('-','') in SENSITIVE else redact(v)
                for k,v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--list', nargs='?', const='', metavar='GROUP')
    action.add_argument('--describe', metavar='OPERATION')
    action.add_argument('--call', metavar='OPERATION')
    parser.add_argument('--profile', choices=('sandbox','algo','market'), default='sandbox')
    parser.add_argument('--request', type=Path, help='JSON containing path/query/headers/body')
    parser.add_argument('--allow-live-mutations', action='store_true')
    args = parser.parse_args()
    if args.list is not None:
        print(json.dumps(catalogue().describe(args.list or None),indent=2))
        return
    if args.describe:
        endpoint = catalogue().operations.get(args.describe)
        if endpoint is None:
            parser.error('Unknown operation')
        print(json.dumps(dict(name=endpoint.name,method=endpoint.method,path=endpoint.path,
                             group=endpoint.group,sandbox=endpoint.sandbox,
                             input_schema=endpoint.validator.schema),indent=2))
        return
    request = json.loads(args.request.read_text()) if args.request else {}
    with APIClient(args.profile,allow_live_mutations=args.allow_live_mutations) as client:
        print(json.dumps(redact(client.call(args.call,**request)),indent=2))


if __name__ == '__main__':
    try:
        main()
    except (APIError,RequestError,AuthError) as error:
        print(error)
        raise SystemExit(1)
