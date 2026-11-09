import unittest
from unittest.mock import Mock, patch

from volatility_scanner.brokers.upstox.account.ip_whitelist import update_static_ips
from volatility_scanner.brokers.upstox.api.client import APIClient, APIError, RequestError
from volatility_scanner.brokers.upstox.api.credentials import Credentials
from volatility_scanner.security.tokens import AuthError


def make_client(current, *, enabled=True):
    session = Mock()
    session.request.side_effect = [
        Mock(status_code=200, json=lambda: dict(status='success', data=current)),
        Mock(status_code=200, json=lambda: dict(status='success', data={'access_tokens_invalidated': True})),
    ]
    return APIClient(credential=Credentials('algo', 'old'), session=session,
                     allow_live_mutations=enabled), session


class TestIPWhitelist(unittest.TestCase):
    def test_preserves_secondary_and_renews_credential_once(self):
        api, session = make_client({'primary_ip': '1.1.1.1', 'secondary_ip': '8.8.8.8'})
        with api, patch('volatility_scanner.brokers.upstox.account.ip_whitelist.ensure_upstox_token',
                        return_value='new') as renew:
            result = update_static_ips(api, '9.9.9.9')
            self.assertTrue(result['changed'])
            self.assertEqual(api.credential.access_token, 'new')
            renew.assert_called_once_with(force=True, profile='algo')
        call = session.request.call_args_list[1]
        self.assertEqual(call.args, ('PUT', 'https://api.upstox.com/v2/user/ip'))
        self.assertEqual(call.kwargs['json'], {'primary_ip': '9.9.9.9', 'secondary_ip': '8.8.8.8'})

    def test_equivalent_ipv6_is_noop_without_mutation_permission(self):
        api, session = make_client({'primary_ip': '2001:db8::1'}, enabled=False)
        with api, patch('volatility_scanner.brokers.upstox.account.ip_whitelist.ensure_upstox_token') as renew:
            self.assertFalse(update_static_ips(api, '2001:0db8:0:0:0:0:0:1')['changed'])
            renew.assert_not_called()
        self.assertEqual(session.request.call_count, 1)

    def test_changed_ip_requires_explicit_mutation_permission(self):
        api, session = make_client({'primary_ip': '1.1.1.1'}, enabled=False)
        with api, self.assertRaises(RequestError):
            update_static_ips(api, '9.9.9.9')
        self.assertEqual(session.request.call_count, 1)

    def test_validation_covers_generic_and_workflow_calls(self):
        api, session = make_client({})
        invalid = [{}, {'primary_ip': 'hostname'}, {'primary_ip': '1.1.1.1/32'},
                   {'primary_ip': 'fe80::1%eth0'}, {'primary_ip': 123},
                   {'primary_ip': '1.1.1.1', 'secondary_ip': '1.1.1.1'},
                   {'primary_ip': '1.1.1.1', 'unexpected': True}]
        with api:
            for body in invalid:
                with self.subTest(body=body), self.assertRaises(RequestError):
                    api.call('updateUserIp', body=body)
            with self.assertRaises(ValueError):
                update_static_ips(api, 'bad')
        session.request.assert_not_called()

    def test_preserved_secondary_cannot_collide_with_new_primary(self):
        api, session = make_client({'primary_ip': '1.1.1.1', 'secondary_ip': '8.8.8.8'})
        with api, self.assertRaises(ValueError):
            update_static_ips(api, '8.8.8.8')
        self.assertEqual(session.request.call_count, 1)

    def test_update_failure_never_retries_or_renews(self):
        api, session = make_client({'primary_ip': '1.1.1.1'})
        session.request.side_effect = [Mock(status_code=200, json=lambda: dict(status='success', data={})),
                                       TimeoutError('network failure')]
        with api, patch('volatility_scanner.brokers.upstox.account.ip_whitelist.ensure_upstox_token') as renew:
            with self.assertRaises(APIError) as caught:
                update_static_ips(api, '9.9.9.9')
            self.assertTrue(caught.exception.outcome_unknown)
            renew.assert_not_called()
        self.assertEqual(session.request.call_count, 2)

    def test_renewal_failure_reports_successful_ip_change(self):
        api, _ = make_client({'primary_ip': '1.1.1.1'})
        with api, patch('volatility_scanner.brokers.upstox.account.ip_whitelist.ensure_upstox_token',
                        side_effect=AuthError('failed')):
            result = update_static_ips(api, '9.9.9.9')
        self.assertTrue(result['changed'])
        self.assertIn('failed', result['token_renewal'])

    def test_sandbox_does_not_reach_network(self):
        session = Mock()
        with APIClient(credential=Credentials('sandbox', 'token'), session=session) as api:
            with self.assertRaises(RequestError):
                update_static_ips(api, '1.1.1.1')
        session.request.assert_not_called()
