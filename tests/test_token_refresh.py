import base64
import io
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from dotenv import dotenv_values

from volatility_scanner.auth import AuthError, ensure_access_token, request_tokens, save_tokens, token_expiry
from volatility_scanner.data import FyersDataError, FyersDataProvider


def jwt(expiry):
    return "header." + base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=") + ".signature"


class TestTokenRefresh(unittest.TestCase):
    def test_disabled_broker_endpoint_has_actionable_error(self):
        body = io.BytesIO(b'{"s":"error","code":-16,"message":"Refresh token API is currently disabled to comply with SEBI regulations."}')
        error = urllib.error.HTTPError("https://api-t1.fyers.in", 400, "Bad Request", {}, body)
        with patch("volatility_scanner.auth.urllib.request.urlopen", side_effect=error), self.assertRaisesRegex(
            AuthError, "FYERS has disabled the refresh-token API"
        ):
            request_tokens("validate-refresh-token", {})

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / ".env"
        self.old = jwt(time.time() - 10)
        self.new = jwt(time.time() + 86400)
        self.path.write_text(
            f"# preserve comment\nFYERS_CLIENT_ID=app\nFYERS_ACCESS_TOKEN={self.old}\n"
            "FYERS_REFRESH_TOKEN=refresh\nFYERS_APP_SECRET=secret\nFYERS_PIN=0012\nOTHER=unchanged\n",
            encoding="utf-8-sig",
        )
        self.environment = patch.dict("os.environ", {}, clear=True)
        self.environment.start()
        self.failure_cache = patch("volatility_scanner.auth._failed_refresh", {})
        self.failure_cache.start()

    def tearDown(self):
        self.failure_cache.stop()
        self.environment.stop()
        self.directory.cleanup()

    def test_expiry_parser_handles_opaque_and_malformed_values(self):
        self.assertAlmostEqual(token_expiry(jwt(1000)), 1000)
        for token in (None, "opaque", "a.not-json.b", "a.W10.b", jwt(float("inf"))):
            self.assertIsNone(token_expiry(token))

    def test_valid_token_avoids_network(self):
        save_tokens(self.path, {"access_token": self.new})
        with patch("volatility_scanner.auth.request_tokens") as request:
            self.assertEqual(ensure_access_token("app", self.new, self.path), self.new)
            request.assert_not_called()

    def test_expired_token_renews_and_preserves_settings(self):
        with patch("volatility_scanner.auth.request_tokens", return_value={"access_token": self.new}) as request:
            self.assertEqual(ensure_access_token("app", self.old, self.path), self.new)
            self.assertEqual(request.call_args.args[0], "validate-refresh-token")
            self.assertEqual(request.call_args.args[1]["pin"], "0012")
        values = dotenv_values(self.path)
        self.assertEqual(values["FYERS_REFRESH_TOKEN"], "refresh")
        self.assertEqual(values["OTHER"], "unchanged")
        self.assertIn("# preserve comment", self.path.read_text(encoding="utf-8-sig"))

    def test_near_expiry_renews_proactively(self):
        near_expiry = jwt(time.time() + 30)
        save_tokens(self.path, {"access_token": near_expiry})
        with patch("volatility_scanner.auth.request_tokens", return_value={"access_token": self.new}) as request:
            self.assertEqual(ensure_access_token("app", near_expiry, self.path), self.new)
            request.assert_called_once()

    def test_provider_loads_renewed_token_at_startup(self):
        with patch("volatility_scanner.data.ENV_FILE", self.path), patch(
            "volatility_scanner.auth.request_tokens", return_value={"access_token": self.new}
        ):
            provider = FyersDataProvider(futures_symbol="TEST")
            self.assertEqual(provider.access_token, self.new)

    def test_totp_mode_replaces_disabled_refresh_flow(self):
        with self.path.open("a", encoding="utf-8") as file:
            file.write("FYERS_AUTH_MODE=totp\nFYERS_USERNAME=USER123\nFYERS_TOTP_SECRET=setup-secret\n")
        with patch("volatility_scanner.totp.login_totp", return_value={"access_token": self.new}) as login, patch(
            "volatility_scanner.auth.request_tokens"
        ) as refresh:
            self.assertEqual(ensure_access_token("app", self.old, self.path), self.new)
            login.assert_called_once()
            refresh.assert_not_called()
        self.assertEqual(dotenv_values(self.path)["FYERS_ACCESS_TOKEN"], self.new)

    def test_totp_rejection_is_not_retried_until_credentials_change(self):
        with self.path.open("a", encoding="utf-8") as file:
            file.write("FYERS_AUTH_MODE=totp\nFYERS_USERNAME=USER123\nFYERS_TOTP_SECRET=setup-secret\n")
        with patch("volatility_scanner.totp.login_totp", side_effect=[
            AuthError("PIN rejected", retryable=False), {"access_token": self.new}
        ]) as login:
            for _ in range(2):
                with self.assertRaises(AuthError):
                    ensure_access_token("app", self.old, self.path)
            login.assert_called_once()
            self.path.write_text(self.path.read_text(encoding="utf-8-sig").replace("FYERS_PIN=0012", "FYERS_PIN=0013"), encoding="utf-8-sig")
            self.assertEqual(ensure_access_token("app", self.old, self.path), self.new)
            self.assertEqual(login.call_count, 2)

    def test_parallel_rejected_requests_share_one_renewal(self):
        with patch("volatility_scanner.auth.request_tokens", return_value={"access_token": self.new}) as request:
            with ThreadPoolExecutor(max_workers=5) as pool:
                tokens = list(pool.map(lambda _: ensure_access_token("app", self.old, self.path, force=True), range(5)))
            self.assertEqual(tokens, [self.new] * 5)
            request.assert_called_once()

    def test_environment_token_updated_after_rotation(self):
        with patch.dict("os.environ", {"FYERS_ACCESS_TOKEN": self.old}), patch(
            "volatility_scanner.auth.request_tokens", return_value={"access_token": self.new}
        ):
            ensure_access_token("app", self.old, self.path)
            self.assertEqual(os.environ["FYERS_ACCESS_TOKEN"], self.new)

    def test_refresh_rejection_backoff_preserves_old_token(self):
        original = self.path.read_bytes()
        with patch("volatility_scanner.auth.request_tokens", side_effect=AuthError("Refresh rejected")) as request:
            for _ in range(2):
                with self.assertRaises(AuthError):
                    ensure_access_token("app", self.old, self.path)
            request.assert_called_once()
        self.assertEqual(self.path.read_bytes(), original)

    def test_write_failure_preserves_original(self):
        original = self.path.read_bytes()
        with patch("volatility_scanner.auth.os.replace", side_effect=PermissionError), self.assertRaises(AuthError):
            save_tokens(self.path, {"access_token": self.new, "refresh_token": "rotated"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_missing_pin_and_expired_refresh_request_reauthentication(self):
        with patch("volatility_scanner.auth.request_tokens") as request:
            with patch.dict("os.environ", {"FYERS_PIN": ""}), self.assertRaisesRegex(AuthError, "FYERS_PIN"):
                ensure_access_token("app", self.old, self.path)
            with patch.dict("os.environ", {"FYERS_REFRESH_TOKEN": self.old}), self.assertRaisesRegex(AuthError, "refresh token expired"):
                ensure_access_token("app", self.old, self.path)
            request.assert_not_called()

    def test_rest_auth_failure_retries_once_with_new_header(self):
        from curl_cffi import CurlHttpVersion
        for status, code in ((401, -15), (200, -8)):
            with self.subTest(status=status):
                provider = FyersDataProvider(client_id="app", access_token="old", futures_symbol="TEST")
                provider._managed_token = True
                rejected = Mock(status_code=status, http_version=CurlHttpVersion.V2_0)
                rejected.json.return_value = {"s": "error", "code": code}
                success = Mock(status_code=200, http_version=CurlHttpVersion.V2_0)
                success.json.return_value = {"s": "ok"}
                provider._http = Mock()
                provider._http.get.side_effect = [rejected, success]
                with patch.object(provider, "refresh_access_token", side_effect=lambda **_: setattr(provider, "access_token", "new")) as renew:
                    self.assertEqual(provider._fetch_json("/quotes", {}), {"s": "ok"})
                    renew.assert_called_once_with(force=True)
                self.assertEqual(provider._http.get.call_args_list[0].kwargs["headers"]["Authorization"], "app:old")
                self.assertEqual(provider._http.get.call_args_list[1].kwargs["headers"]["Authorization"], "app:new")

    def test_rest_persistent_auth_failure_stops_retrying(self):
        provider = FyersDataProvider(client_id="app", access_token="old", futures_symbol="TEST")
        provider._managed_token = True
        provider._http = Mock()
        provider._http.get.return_value.status_code = 401
        with patch.object(provider, "refresh_access_token") as renew, self.assertRaises(FyersDataError):
            provider._fetch_json("/quotes", {})
        renew.assert_called_once()
        self.assertEqual(provider._http.get.call_count, 2)

    def test_websocket_replaced_after_rotation_without_second_refresh(self):
        from volatility_scanner.live import get_live_snapshot
        old = Mock()
        old.auth_failed = True
        old.provider.access_token = "old"
        incoming = Mock()
        incoming.client_id = "app"
        incoming.access_token = "new"
        incoming.futures_symbol = "TEST"
        with patch("volatility_scanner.live._feed", old), patch("volatility_scanner.live._identity", b"old"), patch(
            "volatility_scanner.live.LiveFeed"
        ) as replacement:
            get_live_snapshot(incoming)
            incoming.refresh_access_token.assert_not_called()
            old.close.assert_called_once()
            replacement.assert_called_once_with(incoming)
