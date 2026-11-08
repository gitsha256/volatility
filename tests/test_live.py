import time
import unittest
from unittest.mock import Mock, patch

from volatility_scanner.data import FyersDataProvider, SampleMarketDataProvider
from volatility_scanner.live import LiveFeed


class TestLiveFeed(unittest.TestCase):
    def setUp(self):
        self.provider = Mock(spec=FyersDataProvider)
        self.provider.client_id = "app"
        self.provider.access_token = "private-token"
        self.provider.futures_symbol = "NSE:TEST-FUT"
        self.provider._http = None
        snapshot = SampleMarketDataProvider().fetch_snapshot()
        snapshot.expiry_data = [{"expiry": str(time.time() + 86400)}]
        for q in snapshot.option_chain:
            q.ce_symbol = f"NSE:{int(q.strike)}CE"
            q.pe_symbol = f"NSE:{int(q.strike)}PE"
            q.ce_iv = q.pe_iv = None
        self.provider.fetch_snapshot.return_value = snapshot
        with patch.object(LiveFeed, "_connect"):
            self.feed = LiveFeed(self.provider)
            self.feed._thread.join()

    def tearDown(self):
        self.feed.close()

    def test_subscription_and_incremental_updates(self):
        self.feed._socket = Mock()
        self.feed._on_connect()
        self.assertEqual(len(self.feed._socket.subscribe.call_args.kwargs["symbols"]), 21)
        self.feed._on_message({"symbol": "NSE:TEST-FUT", "ltp": 25123})
        self.feed._on_message({"symbol": "NSE:25000CE", "bid_price": 101, "ask_price": 103})
        self.feed._on_message({"symbol": "NSE:25000CE", "bid_price": 102})
        snapshot = self.feed.snapshot()
        q = next(q for q in snapshot.option_chain if q.strike == 25000)
        self.assertEqual(snapshot.futures_price, 25123)
        self.assertEqual((q.ce_bid, q.ce_ask), (102, 103))
        self.assertGreater(q.ce_updated_at, 0)
        self.assertEqual(snapshot.feed_status, "streaming")
        q.ce_bid = 0
        self.assertEqual(next(q for q in self.feed.snapshot().option_chain if q.strike == 25000).ce_bid, 102)
        self.provider.fetch_snapshot.assert_called_once()

    def test_malformed_messages_staleness_and_reconnect(self):
        before = self.feed.snapshot()
        for message in (None, [], {"symbol": "unknown", "ltp": 1}, {"symbol": "NSE:TEST-FUT", "ltp": "nan"}):
            self.feed._on_message(message)
        self.assertEqual(self.feed.snapshot().futures_price, before.futures_price)
        self.assertEqual(self.feed.snapshot().last_update, 0)
        self.feed._on_message({"symbol": "NSE:TEST-FUT", "ltp": 25001})
        self.feed._last_tick -= 20
        self.assertIn("stale", self.feed.snapshot().feed_status)
        self.feed._on_close({})
        self.assertIn("reconnecting", self.feed._status)
        self.feed._socket = Mock()
        self.feed._on_connect()
        self.assertEqual(self.feed.snapshot().last_update, 0)
        self.feed._socket.subscribe.assert_called_once()

    def test_socket_error_does_not_expose_message(self):
        self.feed._on_error({"message": "private-token"})
        self.assertNotIn("private-token", self.feed.snapshot().feed_status)


class TestHTTPTransport(unittest.TestCase):
    def test_quic_failure_retries_with_http2(self):
        from curl_cffi import CurlHttpVersion, requests
        provider = FyersDataProvider(client_id="app", access_token="token", futures_symbol="NSE:TEST-FUT")
        response = Mock(status_code=200, http_version=CurlHttpVersion.V2_0)
        response.json.return_value = {"s": "ok"}
        with patch("volatility_scanner.data.curl_requests.Session") as session:
            session.return_value.get.side_effect = [requests.RequestsError("QUIC blocked"), response]
            self.assertEqual(provider._fetch_json("/quotes", {}), {"s": "ok"})
            self.assertEqual(session.return_value.http_version, CurlHttpVersion.V2TLS)
            self.assertEqual(provider.http_version, "HTTP/2")

    def test_persistent_session_and_negotiated_protocol(self):
        from curl_cffi import CurlHttpVersion
        provider = FyersDataProvider(client_id="app", access_token="token", futures_symbol="NSE:TEST-FUT")
        response = Mock(status_code=200, http_version=CurlHttpVersion.V3)
        response.json.return_value = {"s": "ok"}
        with patch("volatility_scanner.data.curl_requests.Session") as session:
            session.return_value.get.return_value = response
            provider._fetch_json("/quotes", {})
            self.assertEqual(provider.http_version, "HTTP/3")
            response.http_version = CurlHttpVersion.V2_0
            provider._fetch_json("/quotes", {})
            self.assertEqual(provider.http_version, "HTTP/2")
            session.assert_called_once_with(http_version=CurlHttpVersion.V3)
            self.assertEqual(session.return_value.get.call_count, 2)
            self.assertFalse(session.return_value.get.call_args.kwargs["allow_redirects"])
