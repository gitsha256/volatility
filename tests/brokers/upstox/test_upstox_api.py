import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from volatility_scanner.brokers.upstox.api.catalog import catalogue, SPEC_URI
from volatility_scanner.brokers.upstox.api.client import APIClient, AsyncAPIClient, RequestError, APIError
from volatility_scanner.brokers.upstox.api.credentials import Credentials, credentials
from volatility_scanner.brokers.upstox.streams.portfolio import PortfolioStream, validate_socket_url

ORDER = dict(quantity=1,product='D',validity='DAY',price=1,tag='verification',
             instrument_token='NSE_EQ|INE002A01018',order_type='LIMIT',transaction_type='BUY',
             disclosed_quantity=0,trigger_price=0,is_amo=False)


def client(profile='sandbox'):
    session=Mock()
    session.request.return_value=Mock(status_code=200,json=lambda:{'status':'success','data':{}})
    return APIClient(credential=Credentials(profile,'secret-token','secret-key','secret-api'),session=session),session


class TestCatalogue(unittest.TestCase):
    def test_all_published_operations_and_v3_streams_are_available(self):
        cat=catalogue()
        self.assertEqual(len(cat.operations),105)
        self.assertIs(cat,catalogue())
        self.assertEqual(len({e.name for e in cat.operations.values()}),105)
        for endpoint in cat.operations.values():
            endpoint.validator.check_schema(endpoint.validator.schema)
            self.assertTrue(endpoint.path.startswith(('/v2/','/v3/')))
        self.assertEqual(len([e for e in cat.operations.values() if e.sandbox]),7)
        self.assertIn('getMarketDataFeedAuthorizeV3',cat.operations)

    def test_every_endpoint_prepares_or_validates_without_network(self):
        api,session=client('algo')
        api.allow_live_mutations=True
        for endpoint in api.catalogue.operations.values():
            with self.subTest(endpoint=endpoint.name):
                try:
                    prepared=api.prepare(endpoint.name)
                    self.assertIn(prepared[1].split('/')[2],('api.upstox.com','api-hft.upstox.com'))
                except RequestError:
                    pass
        session.request.assert_not_called()
        api.close()


class TestClient(unittest.TestCase):
    def test_sandbox_never_routes_to_live_and_reuses_connection(self):
        api,session=client()
        api.trading.placeOrder(body=ORDER)
        api.trading.placeOrder_1(body=ORDER)
        for call in session.request.call_args_list:
            self.assertTrue(call.args[1].startswith('https://api-sandbox.upstox.com/'))
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertTrue(call.kwargs['verify'])
        self.assertEqual(session.request.call_count,2)
        api.close()
        session.close.assert_called_once()

    def test_unsupported_sandbox_and_live_mutations_are_local_errors(self):
        api,session=client()
        for name in ('getProfile','exitPositions','getMarketDataFeedAuthorizeV3'):
            with self.assertRaises(RequestError):api.call(name)
        session.request.assert_not_called()
        api.close()
        api,session=client('algo')
        with self.assertRaises(RequestError):api.trading.placeOrder(body=ORDER)
        session.request.assert_not_called()
        api.close()

    def test_live_explicit_order_routes_to_hft(self):
        api,session=client('algo')
        api.trading.placeOrder(body=ORDER,allow_live_mutation=True)
        self.assertEqual(session.request.call_args.args[1],'https://api-hft.upstox.com/v3/order/place')
        api.close()

    def test_invalid_body_and_headers_never_expose_secrets(self):
        api,session=client()
        with self.assertRaises(RequestError) as error:
            api.trading.placeOrder(body=dict(ORDER,quantity='secret-token'))
        self.assertNotIn('secret-token',str(error.exception))
        with self.assertRaises(RequestError):api.call('placeOrder',body=ORDER,headers={'Authorization':'evil'})
        session.request.assert_not_called()
        api.close()

    def test_timed_out_order_is_not_retried(self):
        api,session=client()
        session.request.side_effect=RuntimeError('secret-token')
        with self.assertRaises(APIError) as error:api.trading.placeOrder(body=ORDER)
        self.assertNotIn('secret-token',str(error.exception))
        session.request.assert_called_once()
        api.close()

    def test_path_parameters_are_encoded_and_unknown_query_rejected(self):
        api,_=client('algo')
        endpoint,url,_,_=api.prepare('getMutualFundOrder',path={'order_id':'a/b'})
        self.assertTrue(url.endswith('/a%2Fb'))
        with self.assertRaises(RequestError):api.prepare('getProfile',query={'secret':'value'})
        api.close()

    def test_only_documented_error_codes_are_returned(self):
        api,session=client()
        session.request.return_value=Mock(status_code=400,json=lambda:{'status':'error','errors':[
            {'errorCode':'UDAPI1001','message':'secret-token'},{'errorCode':'secret-token'}]})
        with self.assertRaises(APIError) as error:api.call('placeOrder',body=ORDER)
        self.assertEqual(error.exception.codes,('UDAPI1001',))
        self.assertNotIn('secret-token',str(error.exception))
        api.close()

    def test_credential_profiles_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'.env'
            path.write_text('UPSTOX_ACCESS_TOKEN=market\nUPSTOX_ALGO_ACCESS_TOKEN=algo\nUPSTOX_SANDBOX_ACCESS_TOKEN=sandbox\n')
            with patch.dict('os.environ',{},clear=True):
                for profile in ('market','algo','sandbox'):
                    value=credentials(profile,path)
                    self.assertEqual(value.access_token,profile)
                    self.assertNotIn(value.access_token,repr(value).replace(profile,''))

    def test_discontinued_market_v2_is_explicitly_unavailable(self):
        api,session=client('market')
        with self.assertRaisesRegex(RequestError,'discontinued'):api.streams.getMarketDataFeed()
        session.request.assert_not_called()
        api.close()


class TestAsyncClient(unittest.IsolatedAsyncioTestCase):
    async def test_async_calls_share_async_session_and_close(self):
        session=Mock(request=AsyncMock(return_value=Mock(status_code=200,json=lambda:{'status':'success'})),close=AsyncMock())
        async with AsyncAPIClient(credential=Credentials('sandbox','token'),session=session) as api:
            await api.trading.placeOrder(body=ORDER)
            await api.call_many([('placeOrder',{'body':ORDER}),('placeOrder_1',{'body':ORDER})],concurrency=2)
        self.assertEqual(session.request.await_count,3)
        session.close.assert_awaited_once()


class TestPortfolio(unittest.TestCase):
    def test_signed_socket_url_rejects_untrusted_hosts(self):
        for url in ('ws://feeds.upstox.com/x','wss://upstox.com.evil/x','wss://user:pass@feeds.upstox.com/x','wss://feeds.upstox.com:444/x'):
            with self.assertRaises(RequestError):validate_socket_url(url)
        self.assertEqual(validate_socket_url('wss://feeds.upstox.com/x'),'wss://feeds.upstox.com/x')

    def test_sandbox_streams_cannot_connect_to_live(self):
        api,_=client()
        with self.assertRaises(RequestError):api.market_stream()
        with self.assertRaises(RequestError):api.portfolio_stream()
        api.close()

    def test_events_decode_in_order_without_executing_orders(self):
        api,_=client('algo')
        seen=[]
        stream=PortfolioStream(api,on_message=seen.append)
        import threading
        thread=threading.Thread(target=stream._decode)
        thread.start()
        stream._queue.put(json.dumps({'update_type':'order','order_id':'1'}))
        stream._queue.put(json.dumps({'update_type':'position'}))
        stream._queue.put(None)
        thread.join()
        self.assertEqual([x['update_type'] for x in seen],['order','position'])
        api.session.request.assert_not_called()
        api.close()

class TestDocumentationCoverage(unittest.TestCase):
    def test_every_reference_page_maps_to_valid_operations(self):
        from importlib.resources import files
        inventory=json.loads(files('volatility_scanner.brokers.upstox').joinpath('specs/doc_inventory.json').read_text())
        self.assertEqual(len(inventory['coverage']),100)
        described={name for names in inventory['coverage'].values() for name in names}
        self.assertEqual(set(catalogue().operations)-described,{'publishEvent'})
        self.assertFalse(described-set(catalogue().operations))

    def test_bearer_auth_is_not_lost_from_incomplete_swagger_security_fields(self):
        operations=catalogue().operations
        for name in ('getHistoricalCandleData','getHistoricalCandleData_1','getHolidays','getProfile'):
            self.assertTrue(operations[name].secured,name)
        for name in ('authorize','token','generateTokenViaTotpAndPin','initTokenRequestForIndieUser'):
            self.assertFalse(operations[name].secured,name)

    def test_every_internal_schema_reference_resolves(self):
        from importlib.resources import files
        spec=json.loads(files('volatility_scanner.brokers.upstox').joinpath('specs/openapi.json').read_text())
        refs=[]
        def visit(node):
            if isinstance(node,dict):
                if '$ref' in node:refs.append(node['$ref'])
                for value in node.values():visit(value)
            elif isinstance(node,list):
                for value in node:visit(value)
        visit(spec)
        self.assertGreater(len(refs),100)
        for reference in refs:
            self.assertTrue(reference.startswith('#/'))
            current=spec
            for part in reference[2:].split('/'):current=current[part.replace('~1','/').replace('~0','~')]


class TestFailureAndBudgetSemantics(unittest.TestCase):
    def test_batch_preserves_partial_success_and_uncertain_order_outcome(self):
        api,session=client()
        session.request.side_effect=[RuntimeError('timeout'),Mock(status_code=200,json=lambda:{'status':'success'})]
        results=api.call_many([('placeOrder',{'body':ORDER}),('placeOrder_1',{'body':ORDER})],concurrency=1)
        self.assertIsInstance(results[0],APIError)
        self.assertTrue(results[0].outcome_unknown)
        self.assertEqual(results[1]['status'],'success')
        self.assertEqual(session.request.call_count,2)
        api.close()

    def test_order_budget_is_combined_and_standard_budget_is_per_api(self):
        from volatility_scanner.brokers.upstox.api.rate_limits import budget
        self.assertIs(budget('test','orders','place'),budget('test','orders','modify'))
        self.assertIsNot(budget('test','standard','profile'),budget('test','standard','quotes'))
        self.assertEqual([(sec,limit) for sec,limit,_ in budget('test','ipo').windows],[(1,1),(60,10),(1800,300)])

    def test_market_stream_rejects_invalid_subscription_before_starting_threads(self):
        api,_=client('market')
        with self.assertRaises(RequestError):api.market_stream(instrument_keys='NSE_INDEX|Nifty 50')
        with self.assertRaises(RequestError):api.market_stream(instrument_keys=['NSE_FO|'+str(i) for i in range(51)],mode='full_d30')
        api.close()

class TestEnvironmentBoundaries(unittest.TestCase):
    def test_explicit_sandbox_cannot_be_overridden_by_a_live_credential(self):
        with self.assertRaises(RequestError):
            APIClient('sandbox',credential=Credentials('algo','live-token'),allow_live_mutations=True)

    def test_live_permission_flag_never_enables_unsupported_sandbox_routes(self):
        api,session=client()
        with self.assertRaises(RequestError):
            api.call('exitPositions',allow_live_mutation=True)
        session.request.assert_not_called()
        api.close()

class TestNumericInputs(unittest.TestCase):
    def test_nonfinite_prices_are_rejected_without_serializing_twice(self):
        api,session=client()
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.assertRaises(RequestError):api.trading.placeOrder(body=dict(ORDER,price=value))
        session.request.assert_not_called()
        api.close()

class TestBrowserOAuth(unittest.TestCase):
    def test_callback_state_and_target_are_validated_before_token_exchange(self):
        api,_=client('algo')
        url=api.authorization_url('https://local.example/callback',state='expected-state')
        self.assertIn('response_type=code',url)
        for callback in ('https://evil.example/callback?state=expected-state&code=code',
                         'https://local.example/callback?state=wrong&code=code',
                         'https://local.example/callback?state=expected-state&code=a&code=b'):
            with self.assertRaises(RequestError):api.exchange_redirect(callback)
        api.session.request.assert_not_called()
        api.close()

    def test_valid_callback_uses_form_encoding_and_does_not_log_secrets(self):
        api,_=client('algo')
        api.authorization_url('https://local.example/callback',state='expected-state')
        api.exchange_redirect('https://local.example/callback?state=expected-state&code=single-use')
        request=api.session.request.call_args
        self.assertEqual(request.kwargs['data']['grant_type'],'authorization_code')
        self.assertEqual(request.kwargs['data']['client_secret'],'secret-api')
        self.assertNotIn('json',request.kwargs)
        self.assertNotIn('Authorization',request.kwargs['headers'])
        with self.assertRaises(RequestError):api.exchange_redirect('https://local.example/callback?state=expected-state&code=single-use')
        api.close()

class TestCLIPrivacy(unittest.TestCase):
    def test_tokens_and_signed_socket_urls_are_redacted_in_cli_output(self):
        from volatility_scanner.apps.upstox_api import redact
        value=redact({'access_token':'private','accessToken':'private',
                      'data':{'authorizedRedirectUri':'wss://feeds.upstox.com/?code=private'},'status':'success'})
        self.assertNotIn('private',json.dumps(value))
        self.assertEqual(value['status'],'success')
