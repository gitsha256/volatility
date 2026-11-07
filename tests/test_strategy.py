import unittest

from volatility_scanner.config import ScannerConfig
from volatility_scanner.strategy import NiftyScanner


class TestStrategy(unittest.TestCase):
    def test_long_straddle_qualifies(self):
        scanner = NiftyScanner(ScannerConfig())
        score = scanner.evaluate_single_strategy(
            strategy="long_straddle",
            futures_price=25000.0,
            strike=25000.0,
            iv=0.14,
            forecast_rv=0.18,
            expiry_days=7.0,
            remaining_minutes=180.0,
            option_type="CE",
            bid=100.0,
            ask=120.0,
        )
        self.assertTrue(score.qualifies)
        self.assertGreater(score.volatility_spread, 0.0)

    def test_all_strategies_returns_scores(self):
        scanner = NiftyScanner(ScannerConfig())
        results = scanner.evaluate_all_strategies(
            strategies=["long_straddle", "long_call", "long_put"],
            futures_price=25000.0,
            strike=25000.0,
            iv=0.14,
            forecast_rv=0.18,
            expiry_days=7.0,
            remaining_minutes=180.0,
            option_type="CE",
            bid=100.0,
            ask=120.0,
        )
        self.assertEqual(len(results), 3)
        self.assertTrue(any(result.qualifies for result in results))


if __name__ == "__main__":
    unittest.main()
