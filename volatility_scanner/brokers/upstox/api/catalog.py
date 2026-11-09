"""Cached endpoint catalogue compiled from Upstox's published OpenAPI contract."""
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import math

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_URI = 'urn:upstox:openapi'
METHODS = {'get', 'post', 'put', 'delete', 'patch'}
DISCONTINUED = {'getMarketDataFeed', 'getMarketDataFeedAuthorize'}
RequestValidator = validators.extend(Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine('number',
        lambda _, value: type(value) in (int, float) and math.isfinite(value)).redefine('integer',
        lambda _, value: type(value) is int))
PUBLIC_AUTH_PATHS = {'/v2/login/authorization/dialog', '/v2/login/authorization/token',
                     '/v2/totp-login/authorization/token', '/v3/login/auth/token/request/{client_id}'}
SANDBOX_PATHS = {'/v2/order/place', '/v2/order/modify', '/v2/order/cancel', '/v2/order/multi/place',
                 '/v3/order/place', '/v3/order/modify', '/v3/order/cancel'}
GROUPS = {
 'Order': 'trading', 'IPO': 'ipo', 'Portfolio': 'portfolio', 'Mutual Fund': 'mutual_funds',
 'PAYMENTS': 'payments', 'Login': 'authentication', 'User': 'account', 'Fundamentals': 'fundamentals',
 'History': 'history', 'History V3': 'history', 'Expired Instrument': 'history',
 'Market Quote': 'market', 'Market Quote V3': 'market', 'Options': 'market',
 'Market Holidays and Timings': 'analytics', 'Charge': 'charges', 'News': 'news',
 'Instruments': 'instruments', 'Websocket': 'streams', 'Trade Profit And Loss': 'reports',
 'Post Trade': 'reports', 'events-controller-v-2': 'events',
}


def references(value):
    if isinstance(value, dict):
        return {key: SPEC_URI + item if key == '$ref' and item.startswith('#/') else references(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [references(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Endpoint:
    name: str
    method: str
    path: str
    group: str
    secured: bool
    deprecated: bool
    validator: object
    media_type: str | None
    parameters: tuple

    @property
    def mutation(self):
        return self.method not in ('GET', 'HEAD') and self.path != '/v2/charges/margin'

    @property
    def sandbox(self):
        return self.path in SANDBOX_PATHS


class Catalogue:
    def __init__(self):
        resource = files('volatility_scanner.brokers.upstox').joinpath('specs/openapi.json')
        spec = json.loads(resource.read_text(encoding='utf-8'))
        self.registry = Registry().with_resource(SPEC_URI, Resource.from_contents(spec, default_specification=DRAFT202012))
        self.operations = {}
        for path, item in spec['paths'].items():
            for method, operation in item.items():
                if method in METHODS:
                    self._add(path, method, operation, item.get('parameters', []))
        # These documented V3 routes are absent from the published Swagger snapshot.
        for suffix, name in (('', 'getMarketDataFeedV3'), ('/authorize', 'getMarketDataFeedAuthorizeV3')):
            self._add('/v3/feed/market-data-feed' + suffix, 'get',
                      dict(operationId=name, tags=['Websocket'], security=[{'OAUTH2': []}]), [])

    def _add(self, path, method, operation, common):
        parameters = tuple(common + operation.get('parameters', []))
        if operation['operationId'] == 'authorize':
            parameters += ({'name': 'response_type', 'in': 'query', 'required': True,
                            'schema': {'type': 'string', 'enum': ['code']}},)
        sections = {name: {'type': 'object', 'properties': {}, 'additionalProperties': False}
                    for name in ('path', 'query', 'headers')}
        for parameter in parameters:
            location = parameter['in']
            if location not in ('path', 'query', 'header'):
                continue
            section = sections['headers' if location == 'header' else location]
            name = parameter['name'].lower() if location == 'header' else parameter['name']
            section['properties'][name] = references(parameter.get('schema', {}))
            if parameter.get('required'):
                section.setdefault('required', []).append(name)
        if '/order/' in path:
            sections['headers']['properties']['x-algo-name'] = {'type': 'string', 'minLength': 1}
        required = ['path', 'query', 'headers']
        content = operation.get('requestBody', {}).get('content', {})
        media = next(iter(content), None)
        if media:
            sections['body'] = references(content[media].get('schema', {}))
            if operation['requestBody'].get('required'):
                required.append('body')
        schema = {'type': 'object', 'properties': sections, 'required': required, 'additionalProperties': False}
        name = operation['operationId']
        self.operations[name] = Endpoint(name, method.upper(), path,
            GROUPS.get(operation.get('tags', [''])[0], 'other'), path not in PUBLIC_AUTH_PATHS,
            bool(operation.get('deprecated')), RequestValidator(schema, registry=self.registry), media, parameters)

    def describe(self, group=None):
        return [dict(name=e.name, method=e.method, path=e.path, group=e.group,
                     sandbox=e.sandbox, mutation=e.mutation, deprecated=e.deprecated,
                     discontinued=e.name in DISCONTINUED)
                for e in self.operations.values() if group is None or e.group == group]


@lru_cache(maxsize=1)
def catalogue():
    return Catalogue()
