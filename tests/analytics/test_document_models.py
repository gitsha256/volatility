import math
import unittest

from volatility_scanner.analytics.exposure import spot_greeks, exposure_profile, taylor_change
from volatility_scanner.analytics.pcr_time import PCRTimeScanner, localize_stress, max_pain_strike, YEAR_SECONDS
from volatility_scanner.analytics.premium import PremiumTracker


class TestHigherGreeks(unittest.TestCase):
    def test_calendar_and_volatility_derivatives(self):
        for kind in ('CE','PE'):
            def model(t=.2,sigma=.25):
                return spot_greeks(103,100,sigma,t,kind,.05,.01)
            g=model();h=1e-6
            for target,source in (('theta_per_year','price'),('veta_per_year','vega'),
                                  ('color_per_year','gamma'),('theta2_per_year2','theta_per_year')):
                numerical=-(model(t=.2+h)[source]-model(t=.2-h)[source])/(2*h)
                self.assertAlmostEqual(g[target],numerical,places=6,msg=target)
            ultima=(model(sigma=.25+h)['volga']-model(sigma=.25-h)['volga'])/(2*h)
            self.assertAlmostEqual(g['ultima'],ultima,places=5)
            vega=(model(sigma=.25+h)['price']-model(sigma=.25-h)['price'])/(2*h)
            self.assertAlmostEqual(g['vega'],vega,places=6)

    def test_full_hessian_improves_price_change_and_converges(self):
        for kind in ('CE','PE'):
            g=spot_greeks(103,100,.25,.2,kind,.05,.01)
            errors=[]
            for scale in (1,.5):
                ds,dv,dt=.1*scale,.001*scale,.0001*scale
                predicted=taylor_change(g,spot_change=ds,iv_change=dv,elapsed_years=dt)
                actual=spot_greeks(103+ds,100,.25+dv,.2-dt,kind,.05,.01)['price']-g['price']
                errors.append(abs(actual-predicted['predicted_change']))
                self.assertLess(errors[-1],abs(actual-predicted['first_order_change']))
            self.assertLess(errors[1],errors[0]/3)

    def test_scenarios_and_gamma_curve_match_direct_models(self):
        option=dict(strike=100,oi=100,sigma=.2,option_type='CE')
        profile=exposure_profile([option],spot=103,years=.2,drift_seconds=3600)
        base=spot_greeks(103,100,.2,.2,'CE')
        for name,iv,elapsed in (('iv_up_1_point',.21,0),('iv_down_1_point',.19,0),
                               ('flat_state_charm',.2,3600/YEAR_SECONDS)):
            expected=100*(spot_greeks(103,100,iv,.2-elapsed,'CE')['delta']-base['delta'])
            self.assertAlmostEqual(profile['delta_change_scenarios'][name],expected)
            self.assertAlmostEqual(profile['hypothetical_hedge_change_scenarios'][name],-expected)
        self.assertEqual(len(profile['gamma_curve']),21)
        self.assertAlmostEqual(profile['gamma_curve'][10]['net_gamma'],100*base['gamma'])
        invalid=exposure_profile([dict(option,sigma=.005)],spot=103,years=1/YEAR_SECONDS)
        self.assertIsNone(invalid['delta_change_scenarios']['iv_down_1_point'])
        self.assertIsNone(invalid['delta_change_scenarios']['flat_state_charm'])


class TestPremiumTracking(unittest.TestCase):
    def test_observed_and_model_residuals_and_gap_reset(self):
        tracker=PremiumTracker();expiry=1000
        g=spot_greeks(103,100,.2,expiry/YEAR_SECONDS,'CE')
        first=tracker.observe(symbol='x',timestamp=0,expiry=expiry,spot=103,sigma=.2,greeks=g,premium=5)
        self.assertFalse(first['comparison_ready'])
        g2=spot_greeks(103.1,100,.201,(expiry-1)/YEAR_SECONDS,'CE')
        second=tracker.observe(symbol='x',timestamp=1,expiry=expiry,spot=103.1,sigma=.201,greeks=g2,premium=5.2)
        self.assertTrue(second['comparison_ready'])
        self.assertAlmostEqual(second['observed_change'],.2)
        self.assertAlmostEqual(second['unexplained_observed_change'],.2-second['decomposition']['predicted_change'])
        self.assertEqual(second['hessian'][0][1],second['hessian'][1][0])
        self.assertEqual(second['hessian'][0][2],second['hessian'][2][0])
        gap=tracker.observe(symbol='x',timestamp=10,expiry=expiry,spot=103,sigma=.2,greeks=g,premium=None)
        self.assertFalse(gap['comparison_ready'])
        with self.assertRaises(ValueError):
            tracker.observe(symbol='x',timestamp=10,expiry=expiry,spot=103,sigma=.2,greeks=g,premium=float('nan'))


class TestDocumentSignals(unittest.TestCase):
    def test_max_pain_matches_brute_payout_and_zero_book_is_undefined(self):
        strikes={90:[100,400],100:[300,200],120:[400,100]}
        def payout(spot):
            return sum(calls*max(spot-k,0)+puts*max(k-spot,0) for k,(calls,puts) in strikes.items())
        self.assertEqual(max_pain_strike(strikes),min(strikes,key=payout))
        self.assertIsNone(max_pain_strike({100:[0,0]}))

    def test_derivative_spike_rule_and_literal_time_bands(self):
        scanner=PCRTimeScanner()
        for second in range(302):
            result=scanner.update('pair',timestamp=second,expiry=YEAR_SECONDS,
                call_oi=100,put_oi=700 if second==301 else 100,spot=25000)
        self.assertTrue(result['derivative_stress_alert'])
        self.assertTrue(result['positioning_derivative_alert'])
        self.assertEqual(result['document_t2_band'],'stabilized')
        self.assertEqual(result['document_t3_band'],'latency')
        for t,band in ((.1,'asymmetry'),(.01,'hedging_chaos'),(.13,'unspecified')):
            row=PCRTimeScanner().update('x',timestamp=0,expiry=t*YEAR_SECONDS,
                call_oi=100,put_oi=100,spot=100)
            self.assertEqual(row['document_t3_band'],band)

    def test_localized_otm_put_requires_put_growth_and_relative_pressure(self):
        rows=[dict(strike=90,valid=True,pcr_growth_fraction=7,positioning_derivative_alert=True,put_oi_growth_fraction=7),
              dict(strike=110,valid=True,pcr_growth_fraction=7,positioning_derivative_alert=True,put_oi_growth_fraction=7)]
        localize_stress(rows,dict(pcr_growth_fraction=1),100)
        self.assertTrue(rows[0]['localized_otm_put_alert'])
        self.assertFalse(rows[1]['localized_otm_put_alert'])
        rows[0]['put_oi_growth_fraction']=0
        localize_stress(rows,dict(pcr_growth_fraction=1),100)
        self.assertFalse(rows[0]['localized_otm_put_alert'])
