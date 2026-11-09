"""Persistent, schema-validated access to the complete bundled Upstox API catalogue."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import re
import math
import secrets
from urllib.parse import quote, urlencode, urlsplit, parse_qs

from curl_cffi import requests, CurlHttpVersion

from volatility_scanner.security.tokens import AuthError
from .catalog import catalogue, DISCONTINUED
from .credentials import credentials
from .rate_limits import budget

_MISSING = object()


class APIError(RuntimeError):
    def __init__(self, operation, status, codes=(), *, outcome_unknown=False):
        self.operation, self.status, self.codes = operation, status, tuple(codes)
        self.outcome_unknown = outcome_unknown
        super().__init__(f'Upstox {operation} failed (HTTP {status}' +
                         (', ' + ', '.join(codes) if codes else '') + ')' +
                         ('; outcome unknown, reconcile before retrying' if outcome_unknown else ''))


class RequestError(ValueError):
    pass


class Group:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def __getattr__(self, operation):
        endpoint = self.client.catalogue.operations.get(operation)
        if endpoint is None or endpoint.group != self.name:
            raise AttributeError(operation)
        method = partial(self.client.call, operation)
        setattr(self, operation, method)
        return method


class APIClient:
    def __init__(self, profile=None, *, env_file=None, allow_live_mutations=False,
                 timeout=20, session=None, credential=None):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Timeout must be a finite positive number')
        if credential is not None and profile is not None and credential.profile != profile:
            raise RequestError('Credential profile does not match the selected environment')
        selected = profile or 'sandbox'
        self.credential = credential or (credentials(selected, env_file) if env_file else credentials(selected))
        self.profile = self.credential.profile
        if self.profile not in ('sandbox', 'algo', 'market'):
            raise ValueError('Unknown credential profile')
        self.allow_live_mutations = allow_live_mutations
        self.timeout = timeout
        self.catalogue = catalogue()
        self.session = session if session is not None else requests.Session(http_version=CurlHttpVersion.V2TLS)
        self.closed = False
        for name in {e.group for e in self.catalogue.operations.values()}:
            setattr(self, name, Group(self, name))

    def describe(self, group=None):
        return self.catalogue.describe(group)

    def market_stream(self, **kwargs):
        from ..streams.factories import MarketStream
        return MarketStream(self, **kwargs)

    def portfolio_stream(self, **kwargs):
        from ..streams.portfolio import PortfolioStream
        return PortfolioStream(self, **kwargs)

    def authorization_url(self, redirect_uri=None, state=None):
        if not self.credential.api_key or not (redirect_uri or self.credential.redirect_uri):
            raise AuthError('API key and registered redirect URI are required')
        self._oauth_state = state or secrets.token_urlsafe(32)
        self._oauth_redirect = redirect_uri or self.credential.redirect_uri
        query = dict(client_id=self.credential.api_key,
                     redirect_uri=self._oauth_redirect, response_type='code', state=self._oauth_state)
        _, url, options, _ = self.prepare('authorize', query=query)
        return url + '?' + urlencode(options['params'])

    def exchange_code(self, code, redirect_uri=None):
        return self.call('token', body=dict(code=code, client_id=self.credential.api_key,
            client_secret=self.credential.api_secret, redirect_uri=redirect_uri or self.credential.redirect_uri,
            grant_type='authorization_code'), allow_live_mutation=True)

    def exchange_redirect(self, callback_url):
        if not getattr(self, '_oauth_state', None):
            raise RequestError('Generate an authorization URL before exchanging its callback')
        expected, received = urlsplit(self._oauth_redirect), urlsplit(callback_url)
        if (expected.scheme, expected.netloc, expected.path) != (received.scheme, received.netloc, received.path):
            raise RequestError('OAuth callback does not match the registered redirect URI')
        values = parse_qs(received.query, keep_blank_values=True)
        if (len(values.get('state', [])) != 1 or len(values.get('code', [])) != 1 or
                not secrets.compare_digest(values['state'][0], self._oauth_state) or not values['code'][0]):
            raise RequestError('OAuth callback state or code is invalid')
        self._oauth_state = None
        return self.exchange_code(values['code'][0], self._oauth_redirect)

    def prepare(self, operation, *, path=None, query=None, headers=None, body=_MISSING, allow_live_mutation=False):
        if self.closed:
            raise RuntimeError('API client is closed')
        endpoint = self.catalogue.operations.get(operation)
        if endpoint is None:
            raise RequestError('Unknown Upstox operation')
        if operation in DISCONTINUED:
            raise RequestError('Market feed V2 is discontinued; use getMarketDataFeedAuthorizeV3')
        sandbox = self.profile == 'sandbox'
        if sandbox and not endpoint.sandbox:
            raise RequestError(operation + ' is not supported by the documented sandbox')
        if not sandbox and endpoint.mutation and not (self.allow_live_mutations or allow_live_mutation):
            raise RequestError('Live mutations require an explicit allow_live_mutation flag')
        values = dict(path=path or {}, query=query or {}, headers={k.lower(): v for k, v in (headers or {}).items()})
        if operation == 'generateTokenViaTotpAndPin':
            values['headers'].setdefault('x-api-key', self.credential.api_key)
        if body is not _MISSING:
            values['body'] = body
        error = next(endpoint.validator.iter_errors(values), None)
        if error:
            location = '.'.join(str(part) for part in error.absolute_path) or 'request'
            # Validation messages can embed supplied PINs/tokens. Return schema location only.
            raise RequestError(f'{operation}: invalid {location} ({error.validator})')
        if operation == 'updateUserIp':
            from ..account.ip_whitelist import validate_ips
            try:
                validate_ips(body)
            except ValueError as error:
                raise RequestError(str(error)) from None
        resource_path = endpoint.path
        for name, value in values['path'].items():
            resource_path = resource_path.replace('{' + name + '}', quote(str(value), safe=''))
        if sandbox:
            host = 'https://api-sandbox.upstox.com'
        elif any(part in endpoint.path for part in ('/order/place', '/order/modify', '/order/cancel')):
            host = 'https://api-hft.upstox.com'
        else:
            host = 'https://api.upstox.com'
        request_headers = {'Accept': 'application/json', **values['headers']}
        if endpoint.secured:
            if not self.credential.access_token:
                raise AuthError('Access token is required for ' + operation)
            request_headers['Authorization'] = 'Bearer ' + self.credential.access_token
        if 'x-api-key' in request_headers and request_headers['x-api-key'] != self.credential.api_key:
            raise RequestError('x-api-key must match the selected credential profile')
        options = dict(params=values['query'], headers=request_headers, timeout=self.timeout,
                       allow_redirects=False, verify=True)
        if body is not _MISSING:
            if endpoint.media_type == 'application/x-www-form-urlencoded':
                options['data'] = body
            else:
                options['json'] = body
        if endpoint.media_type:
            request_headers['Content-Type'] = endpoint.media_type
        category = ('totp' if 'totp-login' in endpoint.path else
                    'payout' if endpoint.mutation and '/payout' in endpoint.path else
                    'payments_read' if '/user/payments/' in endpoint.path else
                    'ipo' if endpoint.mutation and '/ipos/' in endpoint.path else
                    'orders' if endpoint.mutation and '/order/' in endpoint.path else 'standard')
        return endpoint, host + resource_path, options, budget('sandbox' if sandbox else 'live', category,
                                                            (endpoint.method, endpoint.path))

    @staticmethod
    def decode(endpoint, response):
        if endpoint.group == 'streams' and response.status_code == 302:
            from ..streams.portfolio import validate_socket_url
            return {'status': 'redirect', 'data': {
                'authorized_redirect_uri': validate_socket_url(response.headers.get('Location', ''))}}
        try:
            payload = response.json()
        except Exception:
            raise APIError(endpoint.name, response.status_code,
                           outcome_unknown=endpoint.mutation and response.status_code >= 500) from None
        if not 200 <= response.status_code < 300 or (isinstance(payload, dict) and payload.get('status') == 'error'):
            errors = payload.get('errors', []) if isinstance(payload, dict) else []
            codes = [str(e.get('errorCode', e.get('error_code', ''))) for e in errors if isinstance(e, dict)]
            codes = [code for code in codes if re.fullmatch(r'UDAPI[0-9]+', code)]
            raise APIError(endpoint.name, response.status_code, codes,
                           outcome_unknown=endpoint.mutation and response.status_code >= 500)
        return payload

    def call(self, operation, **kwargs):
        endpoint, url, options, gate = self.prepare(operation, **kwargs)
        if operation == 'authorize':
            return {'authorization_url': url + '?' + urlencode(options['params'])}
        gate.acquire()
        try:
            response = self.session.request(endpoint.method, url, **options)
        except Exception:
            # No implicit retries: an order may have succeeded before a timeout.
            raise APIError(operation, 0, outcome_unknown=endpoint.mutation) from None
        return self.decode(endpoint, response)

    def call_many(self, calls, *, concurrency=4):
        if not 1 <= concurrency <= 8:
            raise ValueError('Concurrency must be between 1 and 8')
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(self.call, operation, **kwargs) for operation, kwargs in calls]
            return [future.exception() or future.result() for future in futures]

    def close(self):
        if not self.closed:
            self.session.close()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AsyncAPIClient(APIClient):
    def __init__(self, *args, **kwargs):
        if kwargs.get('credential') is None:
            profile = (args[0] if args else kwargs.get('profile')) or 'sandbox'
            env_file = kwargs.get('env_file')
            kwargs['credential'] = credentials(profile, env_file) if env_file else credentials(profile)
        if 'session' not in kwargs:
            kwargs['session'] = requests.AsyncSession(max_clients=8, http_version=CurlHttpVersion.V2TLS)
        super().__init__(*args, **kwargs)

    async def call(self, operation, **kwargs):
        endpoint, url, options, gate = self.prepare(operation, **kwargs)
        if operation == 'authorize':
            return {'authorization_url': url + '?' + urlencode(options['params'])}
        await gate.async_acquire()
        try:
            response = await self.session.request(endpoint.method, url, **options)
        except Exception:
            raise APIError(operation, 0, outcome_unknown=endpoint.mutation) from None
        return self.decode(endpoint, response)

    async def call_many(self, calls, *, concurrency=4):
        if not 1 <= concurrency <= 8:
            raise ValueError('Concurrency must be between 1 and 8')
        semaphore = asyncio.Semaphore(concurrency)
        async def invoke(operation, kwargs):
            async with semaphore:
                return await self.call(operation, **kwargs)
        return await asyncio.gather(*(invoke(operation, kwargs) for operation, kwargs in calls), return_exceptions=True)

    async def close(self):
        if not self.closed:
            await self.session.close()
            self.closed = True

    def __enter__(self):
        raise TypeError('Use async with AsyncAPIClient')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
