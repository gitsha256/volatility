import unittest

from volatility_scanner.analytics.pricing import black76_price, invert_black76_iv


class TestPricing(unittest.TestCase):
    def test_iv_rejects_impossible_prices(self):
        for price in (200, float("nan")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                invert_black76_iv(futures_price=100, strike=100, market_price=price,
                                  time_to_expiry=.1, option_type="CE")

    def test_black76_round_trip(self):
        futures_price = 25000.0
        strike = 25000.0
        sigma = 0.14
        t = 0.25

        market = black76_price(
            futures_price=futures_price,
            strike=strike,
            sigma=sigma,
            time_to_expiry=t,
            option_type="CE",
        )
        recovered = invert_black76_iv(
            futures_price=futures_price,
            strike=strike,
            market_price=market,
            time_to_expiry=t,
            option_type="CE",
        )

        self.assertAlmostEqual(recovered, sigma, places=4)

    def test_black76_put_price_positive(self):
        price = black76_price(
            futures_price=25000.0,
            strike=25000.0,
            sigma=0.18,
            time_to_expiry=0.1,
            option_type="PE",
        )
        self.assertGreater(price, 0.0)


if __name__ == "__main__":
    unittest.main()
