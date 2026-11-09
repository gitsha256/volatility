import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from dotenv import dotenv_values

from volatility_scanner.security.tokens import AuthError
from volatility_scanner.brokers.upstox.auth import ensure_upstox_token


class TestUpstoxAuth(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env'
        self.original = ('FYERS_ACCESS_TOKEN=keep-fyers\nUPSTOX_ACCESS_TOKEN=old-upstox\n'
                         'UPSTOX_API_KEY=private-key\nUPSTOX_PIN=001234\n'
                         'UPSTOX_TOTP_SECRET=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ\n')
        self.path.write_text(self.original)
        env = patch.dict('os.environ', {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_totp_login_saves_only_upstox_token(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {'access_token': 'new-upstox'}
        self.assertEqual(ensure_upstox_token(self.path, force=True), 'new-upstox')
        saved = dotenv_values(self.path)
        self.assertEqual(saved['FYERS_ACCESS_TOKEN'], 'keep-fyers')
        self.assertEqual(saved['UPSTOX_ACCESS_TOKEN'], 'new-upstox')
        self.assertEqual(post.call_args.kwargs['json']['pin'], '001234')
        self.assertEqual(len(post.call_args.kwargs['json']['totp']), 6)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['headers']['x-api-key'], 'private-key')

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_rejection_preserves_token_without_exposing_response(self, post):
        post.return_value = Mock(status_code=401)
        post.return_value.json.return_value = {'message': 'private-key'}
        with self.assertRaises(AuthError) as caught:
            ensure_upstox_token(self.path, force=True)
        self.assertNotIn('private-key', str(caught.exception))
        self.assertEqual(self.path.read_text(), self.original)
        post.assert_called_once()

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_reuses_current_token(self, post):
        self.assertEqual(ensure_upstox_token(self.path), 'old-upstox')
        post.assert_not_called()

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_invalid_pin_prevents_network(self, post):
        self.path.write_text(self.original.replace('001234', '1234'))
        with self.assertRaises(AuthError):
            ensure_upstox_token(self.path, force=True)
        post.assert_not_called()

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_matching_app_profiles_renew_together_without_touching_sandbox(self, post):
        self.path.write_text(self.original + '\nUPSTOX_ALGO_API_KEY=private-key\n'
                             'UPSTOX_ALGO_ACCESS_TOKEN=old-algo\nUPSTOX_SANDBOX_ACCESS_TOKEN=keep-sandbox\n')
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={'access_token':'renewed'}
        ensure_upstox_token(self.path,force=True,profile='algo')
        values=dotenv_values(self.path)
        self.assertEqual(values['UPSTOX_ACCESS_TOKEN'],'renewed')
        self.assertEqual(values['UPSTOX_ALGO_ACCESS_TOKEN'],'renewed')
        self.assertEqual(values['UPSTOX_SANDBOX_ACCESS_TOKEN'],'keep-sandbox')

    @patch('volatility_scanner.brokers.upstox.auth.requests.post')
    def test_distinct_app_profiles_are_not_synchronized(self, post):
        self.path.write_text(self.original + '\nUPSTOX_ALGO_API_KEY=another-key\nUPSTOX_ALGO_ACCESS_TOKEN=old-algo\n')
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={'access_token':'renewed-algo'}
        ensure_upstox_token(self.path,force=True,profile='algo')
        values=dotenv_values(self.path)
        self.assertEqual(values['UPSTOX_ACCESS_TOKEN'],'old-upstox')
        self.assertEqual(values['UPSTOX_ALGO_ACCESS_TOKEN'],'renewed-algo')
