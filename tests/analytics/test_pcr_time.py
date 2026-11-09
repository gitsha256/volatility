import math
import unittest

from volatility_scanner.analytics.pcr_time import PCRTimeScanner, YEAR_SECONDS, derivative, strike_slopes
from volatility_scanner.analytics.exposure import exposure_profile, spot_greeks


class TestPCRTime(unittest.TestCase):
    def update(self, scanner, minute, **changes):
        args = dict(timestamp=minute * 60, expiry=YEAR_SECONDS, call_oi=100, put_oi=100, spot=25000)
        args.update(changes)
        return scanner.update(('expiry', 25000), **args)

    def test_scores_and_actual_derivatives_are_different(self):
        scanner = PCRTimeScanner()
        first = self.update(scanner, 0)
        self.assertEqual(first['pcr_over_t3'], 1)
        for minute in range(1, 6):
            last = self.update(scanner, minute)
        self.assertTrue(last['window_ready'])
        self.assertGreater(last['pcr_over_t3'], 1)
        self.assertAlmostEqual(last['pcr_d1_per_minute'], 0)
        self.assertAlmostEqual(last['pcr_d2_per_minute2'], 0)
        self.assertAlmostEqual(last['pcr_d3_per_minute3'], 0)
        self.assertFalse(last['positioning_stress_alert'])

    def test_five_minute_alert_and_full_window_spot_range(self):
        for moved in (False, True):
            scanner = PCRTimeScanner()
            for minute in range(6):
                result = self.update(scanner, minute, put_oi=700 if minute == 5 else 100,
                                     spot=26000 if moved and minute == 2 else 25000)
            self.assertEqual(result['positioning_stress_alert'], not moved)

    def test_clock_only_growth_does_not_become_positioning_alert(self):
        scanner = PCRTimeScanner()
        for minute in range(6):
            result = self.update(scanner, minute, expiry=400)
        self.assertTrue(result['raw_stress_alert'])
        self.assertFalse(result['positioning_stress_alert'])
        self.assertEqual(result['pcr_growth_fraction'], 0)

    def test_undefined_and_expired_values_reset_history(self):
        for change in ({'call_oi': 0}, {'put_oi': -1}, {'put_oi': True}, {'spot': float('nan')},
                       {'expiry': 0}, {'valid': False}):
            scanner = PCRTimeScanner()
            self.update(scanner, 0)
            result = self.update(scanner, 1, **change)
            self.assertFalse(result['valid'])
            self.assertIsNone(result['pcr_over_t3'])
            self.assertFalse(scanner.history)

    def test_zero_puts_floor_gap_and_out_of_order(self):
        scanner = PCRTimeScanner()
        self.assertEqual(self.update(scanner, 0, put_oi=0)['pcr'], 0)
        self.assertEqual(self.update(scanner, 0)['reason'], 'non_increasing_timestamp')
        self.assertIsNone(self.update(scanner, 3)['pcr_d1_per_minute'])
        floor = self.update(scanner, 4, expiry=250)
        self.assertTrue(floor['time_floor_active'])
        self.assertTrue(math.isfinite(floor['pcr_over_t3']))
        self.assertFalse(floor['raw_stress_alert'])

    def test_nonuniform_third_derivative_and_bounded_history(self):
        points = [(minute * 60, minute ** 3) for minute in (0, .5, 2, 3)]
        self.assertAlmostEqual(derivative(points, 3), 6)
        scanner = PCRTimeScanner()
        for minute in range(1000):
            self.update(scanner, minute)
        self.assertLessEqual(len(next(iter(scanner.history.values()))), 9)

    def test_cross_strike_spacing_and_missing_pairs(self):
        rows = [dict(strike=k, pcr_over_t3=k * 2) for k in (100, 120, 200)]
        strike_slopes(rows)
        self.assertEqual([r['pcr_over_t3_strike_slope'] for r in rows], [2, 2, 2])
        rows[1]['pcr_over_t3'] = None
        strike_slopes(rows)
        self.assertTrue(all(r['pcr_over_t3_strike_slope'] is None for r in rows))


class TestExposure(unittest.TestCase):
    def test_greeks_against_finite_differences(self):
        for kind in ('CE', 'PE'):
            s, k, sigma, t, r, q = 103., 100., .2, .1, .05, .01
            def calc(s=s, sigma=sigma, t=t):
                return spot_greeks(s, k, sigma, t, kind, r, q)
            g, h = calc(), 1e-5
            self.assertAlmostEqual(g['gamma'], (calc(s=s+h)['delta']-calc(s=s-h)['delta'])/(2*h), places=7)
            self.assertAlmostEqual(g['vanna'], (calc(sigma=sigma+h)['delta']-calc(sigma=sigma-h)['delta'])/(2*h), places=6)
            self.assertAlmostEqual(g['charm_per_year'], -(calc(t=t+h)['delta']-calc(t=t-h)['delta'])/(2*h), places=6)
            self.assertAlmostEqual(g['speed'], (calc(s=s+h)['gamma']-calc(s=s-h)['gamma'])/(2*h), places=7)
            vega_high = calc(sigma=sigma+h)['gamma'] * s*s*(sigma+h)*t
            vega_low = calc(sigma=sigma-h)['gamma'] * s*s*(sigma-h)*t
            self.assertAlmostEqual(g['volga'], (vega_high-vega_low)/(2*h), places=6)

    def test_exposure_scaling_and_balanced_book(self):
        rows = [dict(strike=100, oi=100, sigma=.2, option_type=kind) for kind in ('CE','PE')]
        result = exposure_profile(rows, spot=100, years=.1)
        self.assertAlmostEqual(result['net_gamma_dollar_1pct'], 0)
        self.assertTrue(result['gamma_flip_indeterminate'])
        self.assertIsNone(result['nearest_gamma_flip'])
        rows.pop()
        one = exposure_profile(rows, spot=100, years=.1)
        many = exposure_profile(rows, spot=100, years=.1, oi_multiplier=75)
        self.assertAlmostEqual(many['net_gamma_dollar_1pct'], one['net_gamma_dollar_1pct']*75)
        self.assertAlmostEqual(one['net_vanna_dollar_1iv_point'],
                               100 * spot_greeks(100,100,.2,.1,'CE')['vanna'] * 100 * .01)

    def test_flip_is_aggregate_underlying_root_not_strike_interpolation(self):
        rows = [dict(strike=100, oi=100, sigma=.2, option_type='CE'),
                dict(strike=110, oi=100, sigma=.2, option_type='PE')]
        result = exposure_profile(rows, spot=105, years=.1)
        expected = math.sqrt(100*110) * math.exp(-.5 * .2**2 * .1)
        self.assertAlmostEqual(result['nearest_gamma_flip'], expected, places=5)
        self.assertEqual(result['call_wall'], 100)
        self.assertEqual(result['put_wall'], 110)
