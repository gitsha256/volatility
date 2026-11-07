import unittest

from volatility_scanner.data import MarketSnapshot, NSEDataProvider, SampleMarketDataProvider


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


if __name__ == "__main__":
    unittest.main()
