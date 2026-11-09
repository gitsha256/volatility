import base64
import unittest
from unittest.mock import Mock, patch

from volatility_scanner.brokers.fyers.auth import AuthError
from volatility_scanner.brokers.fyers.totp import generate_totp, login_totp


SECRET = base64.b32encode(b"12345678901234567890").decode()
REDIRECT = "https://example.com/callback"


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class TestTOTP(unittest.TestCase):
    def setUp(self):
        self.session_patch = patch("volatility_scanner.brokers.fyers.totp.requests.Session")
        self.session = self.session_patch.start().return_value.__enter__.return_value
        self.session.post.side_effect = [
            response({"s": "ok", "request_key": "first"}),
            response({"s": "ok", "request_key": "second"}),
            response({"s": "ok", "data": {"access_token": "session-token"}}),
            response({"Url": REDIRECT + "?auth_code=auth-code&state=random-state"}, 308),
        ]
        self.clock = patch("volatility_scanner.brokers.fyers.totp.time.time", return_value=100)
        self.clock.start()
        self.random = patch("volatility_scanner.brokers.fyers.totp.secrets.token_urlsafe", return_value="random-state")
        self.random.start()
        self.exchange = patch("volatility_scanner.brokers.fyers.totp.request_tokens", return_value={"access_token": "new-token"})
        self.token_request = self.exchange.start()

    def tearDown(self):
        self.exchange.stop()
        self.random.stop()
        self.clock.stop()
        self.session_patch.stop()

    def login(self, **kwargs):
        params = dict(client_id="app-100", app_secret="private-app-secret", username="USER123",
                      pin="0012", secret=SECRET, redirect_uri=REDIRECT)
        return login_totp(**(params | kwargs))

    def test_totp_matches_rfc6238_sha1_vectors(self):
        for timestamp, expected in ((59, "287082"), (1111111109, "081804"),
                                    (1111111111, "050471"), (1234567890, "005924"),
                                    (2000000000, "279037"), (20000000000, "353130")):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(generate_totp(SECRET, timestamp), expected)

    def test_login_flow_keeps_secrets_out_of_errors_and_uses_state(self):
        self.assertEqual(self.login(), {"access_token": "new-token"})
        calls = self.session.post.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(base64.b64decode(calls[0].kwargs["json"]["fy_id"]).decode(), "USER123")
        self.assertEqual(base64.b64decode(calls[2].kwargs["json"]["identifier"]).decode(), "0012")
        self.assertEqual(calls[3].kwargs["headers"]["Authorization"], "Bearer session-token")
        self.assertEqual(calls[3].kwargs["json"]["state"], "random-state")
        self.assertNotIn("private-app-secret", str(calls))
        for call in calls:
            self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(self.token_request.call_args.args[0], "validate-authcode")
        self.assertEqual(self.token_request.call_args.args[1]["code"], "auth-code")

    def test_invalid_seed_or_pin_stops_before_network(self):
        for kwargs in ({"secret": "123456"}, {"secret": "otpauth://invalid"}, {"pin": "12"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AuthError):
                self.login(**kwargs)
        self.session.post.assert_not_called()

    def test_pin_rejection_stops_login_without_echoing_body(self):
        self.session.post.side_effect = [
            response({"request_key": "first"}), response({"request_key": "second"}),
            response({"s": "error", "message": "private-app-secret"}, 401),
        ]
        with self.assertRaises(AuthError) as error:
            self.login()
        self.assertFalse(error.exception.retryable)
        self.assertNotIn("private-app-secret", str(error.exception))
        self.assertEqual(self.session.post.call_count, 3)
        self.token_request.assert_not_called()

    def test_redirect_or_state_mismatch_prevents_token_exchange(self):
        for url in ("https://evil.example/callback?state=random-state&auth_code=code",
                    REDIRECT + "?state=wrong&auth_code=code"):
            with self.subTest(url=url):
                self.session.post.side_effect = [
                    response({"request_key": "one"}), response({"request_key": "two"}),
                    response({"data": {"access_token": "session"}}), response({"Url": url}, 308),
                ]
                with self.assertRaises(AuthError):
                    self.login()
        self.token_request.assert_not_called()

    def test_totp_window_boundary_waits_for_fresh_code(self):
        with patch("volatility_scanner.brokers.fyers.totp.time.time", side_effect=[59, 59, 60.1]), patch(
            "volatility_scanner.brokers.fyers.totp.time.sleep"
        ) as sleep:
            self.login()
        sleep.assert_called_once_with(1.1)
        self.assertEqual(self.session.post.call_args_list[1].kwargs["json"]["otp"], generate_totp(SECRET, 60.1))

    def test_missing_request_key_stops_before_sending_otp(self):
        self.session.post.side_effect = [response({"s": "ok"})]
        with self.assertRaises(AuthError):
            self.login()
        self.assertEqual(self.session.post.call_count, 1)

    def test_rate_limit_stops_before_otp_without_retry(self):
        self.session.post.side_effect = [response({"s": "error", "code": 429}, 429)]
        with self.assertRaisesRegex(AuthError, "rate-limited login") as error:
            self.login()
        self.assertFalse(error.exception.retryable)
        self.assertEqual(self.session.post.call_count, 1)
        self.token_request.assert_not_called()
