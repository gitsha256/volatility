"""Observed premium changes versus a local Jacobian/Hessian approximation."""
from .exposure import taylor_change
from .pcr_time import finite, YEAR_SECONDS


class PremiumTracker:
    def __init__(self, max_gap_seconds=2.5):
        self.previous = {}
        self.max_gap_seconds = max_gap_seconds

    def observe(self, *, symbol, timestamp, expiry, spot, sigma, greeks, premium=None):
        required = ('price','delta','gamma','vega','theta_per_year','vanna','volga',
                    'charm_per_year','veta_per_year','theta2_per_year2')
        if (not isinstance(symbol,str) or not symbol or not all(finite(v) for v in (timestamp,expiry,spot,sigma))
                or expiry<=timestamp or min(spot,sigma)<=0 or not isinstance(greeks,dict)
                or not all(finite(greeks.get(k)) for k in required)
                or premium is not None and (not finite(premium) or premium<=0)):
            raise ValueError('Valid contract model, times, spot, IV and optional positive premium required')
        result = dict(symbol=symbol, sample_epoch=timestamp, expiry_epoch=expiry, complete=True,
            spot=spot,iv_decimal=sigma,years_to_expiry=(expiry-timestamp)/YEAR_SECONDS,
            premium=premium, model_price=greeks['price'], greeks=greeks,
            axes=['spot','iv_decimal','calendar_year'],
            jacobian=[greeks['delta'],greeks['vega'],greeks['theta_per_year']],
            hessian=[[greeks['gamma'],greeks['vanna'],greeks['charm_per_year']],
                     [greeks['vanna'],greeks['volga'],greeks['veta_per_year']],
                     [greeks['charm_per_year'],greeks['veta_per_year'],greeks['theta2_per_year2']]],
            comparison_ready=False)
        previous = self.previous.get(symbol)
        if previous and previous['expiry']==expiry and 0 < timestamp-previous['timestamp'] <= self.max_gap_seconds:
            changes = taylor_change(previous['greeks'], spot_change=spot-previous['spot'],
                iv_change=sigma-previous['sigma'], elapsed_years=(timestamp-previous['timestamp'])/YEAR_SECONDS)
            if not all(finite(v) for group in ('first_order','second_order') for v in changes[group].values()) or not finite(changes['predicted_change']):
                self.previous.pop(symbol,None)
                raise ValueError('Premium approximation exceeds numeric range')
            model_change = greeks['price']-previous['greeks']['price']
            observed = premium-previous['premium'] if finite(premium) and finite(previous['premium']) else None
            result.update(comparison_ready=True, decomposition=changes, observed_change=observed,
                model_change=model_change, model_approximation_error=model_change-changes['predicted_change'],
                unexplained_observed_change=observed-changes['predicted_change'] if observed is not None else None)
        self.previous[symbol] = dict(timestamp=timestamp,expiry=expiry,spot=spot,sigma=sigma,greeks=greeks,premium=premium)
        return result
