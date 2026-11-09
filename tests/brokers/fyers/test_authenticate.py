import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from volatility_scanner.apps.authenticate import auth_code_from_redirect, exchange_code, main
from volatility_scanner.brokers.fyers.auth import AuthError


class TestAuthenticate(unittest.TestCase):
    def test_main_reads_env_secret_and_saves_token(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                'FYERS_CLIENT_ID=app\nFYERS_APP_SECRET=private-secret\n'
                'FYERS_REDIRECT_URI=https://example.com/callback\n', encoding="utf-8"
            )
            with patch("volatility_scanner.apps.authenticate.ENV_FILE", env_file), patch.dict("os.environ", {}, clear=True), patch(
                "volatility_scanner.apps.authenticate.secrets.token_urlsafe", return_value="state"
            ), patch("volatility_scanner.apps.authenticate.getpass.getpass", return_value="https://example.com/callback?state=state&auth_code=code"), patch(
                "volatility_scanner.apps.authenticate.webbrowser.open"
            ), patch("volatility_scanner.apps.authenticate.exchange_code", return_value={"access_token": "new-token", "refresh_token": "new-refresh"}) as exchange, patch("builtins.print") as output:
                main()
                exchange.assert_called_once_with("app", "private-secret", "code")
                self.assertNotIn("private-secret", str(output.call_args_list))
                self.assertNotIn("new-token", str(output.call_args_list))
                self.assertNotIn("new-refresh", str(output.call_args_list))
            self.assertIn("FYERS_ACCESS_TOKEN='new-token'", env_file.read_text(encoding="utf-8-sig"))
            self.assertIn("FYERS_REFRESH_TOKEN='new-refresh'", env_file.read_text(encoding="utf-8-sig"))

    def test_redirect_rejects_wrong_state_or_destination(self):
        redirect = "https://example.com/callback"
        self.assertEqual(auth_code_from_redirect(redirect + "?state=test&auth_code=code", redirect, "test"), "code")
        for url in [redirect + "?state=wrong&auth_code=code", "https://other.com/callback?state=test&auth_code=code", redirect + "?state=test"]:
            with self.assertRaises(ValueError):
                auth_code_from_redirect(url, redirect, "test")

    def test_exchange_returns_token(self):
        with patch("volatility_scanner.brokers.fyers.auth.urllib.request.urlopen", return_value=io.BytesIO(
            json.dumps({"s": "ok", "access_token": "test-token"}).encode()
        )) as request:
            self.assertEqual(exchange_code("app", "secret", "code")["access_token"], "test-token")
        body = json.loads(request.call_args.args[0].data)
        self.assertNotIn("secret", body.values())
        self.assertEqual(body["code"], "code")

    def test_exchange_rejection_does_not_echo_response_secrets(self):
        with patch("volatility_scanner.brokers.fyers.auth.urllib.request.urlopen", return_value=io.BytesIO(
            b'{"s":"error","message":"sensitive-response"}'
        )):
            with self.assertRaises(AuthError) as error:
                exchange_code("app", "secret", "code")
        self.assertNotIn("sensitive-response", str(error.exception))
