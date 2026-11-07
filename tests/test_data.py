import unittest
from unittest.mock import patch

from volatility_scanner.data import (
    FyersDataError,
    FyersDataProvider,
    MarketSnapshot,
    NSEDataProvider,
    SampleMarketDataProvider,
)


class TestData(unittest.TestCase):
    def test_sample_provider_returns_snapshot(self):
        snapshot = SampleMarketDataProvider().fetch_snapshot(symbol="NIFTY")
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertGreater(snapshot.futures_price, 0)
        self.assertTrue(snapshot.option_chain)

    def test_nse_provider_falls_back_cleanly(self):
        snapshot = NSEDataProvider().fetch_snapshot(symbol="NIFTY")
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertGreater(snapshot.futures_price, 0)

    def test_fyers_provider_parses_chain_and_futures_quote(self):
        provider = FyersDataProvider(
            client_id="test-app",
            access_token="test-token",
            futures_symbol="NSE:NIFTY26JUNFUT",
        )
        responses = [
            {
                "s": "ok",
                "data": {
                    "expiryData": [{"date": "25-Jun-2026", "expiry": "1782383400"}],
                    "optionsChain": [
                        {
                            "strike_price": 25000,
                            "option_type": "CE",
                            "bid": 100,
                            "ask": 102,
                            "iv": 14.5,
                        },
                        {
                            "strike_price": 25000,
                            "option_type": "PE",
                            "bid": 98,
                            "ask": 100,
                            "iv": 0.15,
                        },
                        {"strike_price": 0, "option_type": "", "ltp": 25010},
                    ],
                },
            },
            {
                "s": "ok",
                "d": [{"n": "NSE:NIFTY26JUNFUT", "v": {"lp": 25012.5}}],
            },
        ]
        with patch.object(provider, "_fetch_json", side_effect=responses) as fetch:
            snapshot = provider.fetch_snapshot()

        self.assertEqual(snapshot.source, "fyers")
        self.assertEqual(snapshot.futures_price, 25012.5)
        self.assertEqual(snapshot.option_chain[0].ce_iv, 0.145)
        self.assertEqual(snapshot.option_chain[0].pe_iv, 0.15)
        self.assertEqual(fetch.call_args_list[0].args[0], "/options-chain-v3")
        self.assertEqual(fetch.call_args_list[1].args[0], "/quotes")

    def test_fyers_requires_credentials_and_futures_symbol(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(FyersDataError):
                FyersDataProvider()
            with self.assertRaises(FyersDataError):
                FyersDataProvider(client_id="app", access_token="token")

    def test_fyers_api_errors_are_not_replaced_with_sample_data(self):
        provider = FyersDataProvider(
            client_id="test-app",
            access_token="test-token",
            futures_symbol="NSE:NIFTY26JUNFUT",
        )
        with patch.object(
            provider,
            "_fetch_json",
            side_effect=FyersDataError("FYERS market-data error: invalid token"),
        ):
            with self.assertRaisesRegex(FyersDataError, "invalid token"):
                provider.fetch_snapshot()


if __name__ == "__main__":
    unittest.main()
