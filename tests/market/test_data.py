import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from volatility_scanner.market.data import FyersDataError, FyersDataProvider, MarketSnapshot, NSEDataProvider, SampleMarketDataProvider, OptionQuote, derive_missing_ivs
from volatility_scanner.analytics.pricing import black76_price


class TestData(unittest.TestCase):
    def test_missing_ivs_derived_from_same_expiry_forward(self):
        quotes = []
        for strike in (24900, 25000, 25100):
            ce = black76_price(futures_price=25000, strike=strike, sigma=.18, time_to_expiry=.02, option_type="CE")
            pe = black76_price(futures_price=25000, strike=strike, sigma=.18, time_to_expiry=.02, option_type="PE")
            quotes.append(OptionQuote(strike, ce - .1, ce + .1, pe - .1, pe + .1))
        snapshot = MarketSnapshot("NIFTY", 26000, quotes)
        derive_missing_ivs(snapshot, .02)
        for quote in quotes:
            self.assertAlmostEqual(quote.ce_iv, .18, places=6)
            self.assertAlmostEqual(quote.pe_iv, .18, places=6)
            self.assertEqual(quote.ce_iv_source, "derived")
        quotes[0].ce_iv = .25
        quotes[0].ce_iv_source = "FYERS"
        quotes[1].pe_iv = None
        quotes[1].pe_bid = quotes[1].pe_ask + 1
        derive_missing_ivs(snapshot, .02)
        self.assertEqual(quotes[0].ce_iv, .25)
        self.assertIsNone(quotes[1].pe_iv)

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
        with patch.dict("os.environ", {}, clear=True), patch(
            "volatility_scanner.market.data.dotenv_values", return_value={}
        ):
            with self.assertRaises(FyersDataError):
                FyersDataProvider()
            with self.assertRaises(FyersDataError):
                FyersDataProvider(client_id="app", access_token="token")

    def test_fyers_reads_local_env_without_interpolation_or_environment_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                'FYERS_CLIENT_ID="file-app"\nFYERS_ACCESS_TOKEN="${PRIVATE_VALUE}"\n'
                'FYERS_NIFTY_FUTURES_SYMBOL="NSE:TEST-FUT"\n', encoding="utf-8-sig"
            )
            with patch("volatility_scanner.market.data.ENV_FILE", env_file), patch.dict(
                "os.environ", {"PRIVATE_VALUE": "must-not-expand"}, clear=True
            ):
                provider = FyersDataProvider()
                self.assertEqual(provider.client_id, "file-app")
                self.assertEqual(provider.access_token, "${PRIVATE_VALUE}")
                self.assertEqual(provider.futures_symbol, "NSE:TEST-FUT")
                self.assertNotIn("FYERS_ACCESS_TOKEN", os.environ)

                with patch.dict("os.environ", {"FYERS_CLIENT_ID": "env-app"}):
                    self.assertEqual(FyersDataProvider().client_id, "env-app")
                    self.assertEqual(FyersDataProvider(client_id="explicit-app").client_id, "explicit-app")

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
