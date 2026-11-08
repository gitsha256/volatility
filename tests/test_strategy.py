import unittest

from volatility_scanner.config import ScannerConfig
from volatility_scanner.strategy import NiftyScanner


class TestStrategy(unittest.TestCase):
    def test_loss_making_straddle_does_not_qualify(self):
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
        self.assertLess(score.expected_pnl, 0)
        self.assertFalse(score.qualifies)
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
        self.assertFalse(any(result.qualifies for result in results))

    def test_qualification_requires_time_and_positive_pnl(self):
        scanner = NiftyScanner(ScannerConfig(transaction_cost=0))
        inputs = dict(
            strategy="long_straddle", futures_price=25000, strike=25000, iv=.14,
            forecast_rv=.18, expiry_days=7, remaining_minutes=180,
            option_type="CE", bid=100, ask=100,
        )
        self.assertTrue(scanner.evaluate_single_strategy(**inputs).qualifies)
        for change in ({"remaining_minutes": 0}, {"expiry_days": 0}, {"ask": 120}):
            with self.subTest(change=change):
                self.assertFalse(scanner.evaluate_single_strategy(**(inputs | change)).qualifies)
        for change in ({"iv": float("nan")}, {"ask": 99}, {"remaining_minutes": -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                scanner.evaluate_single_strategy(**(inputs | change))


if __name__ == "__main__":
    unittest.main()
