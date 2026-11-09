"""Black-Scholes exposure scenarios. Call+/put- signs are assumptions, not dealer inventory."""
import math

from .pcr_time import finite
from .pricing import _normal_cdf, _normal_pdf


def spot_greeks(spot, strike, sigma, years, option_type, rate=0.0, dividend=0.0):
    if (not all(finite(v) for v in (spot, strike, sigma, years, rate, dividend))
            or min(spot, strike, sigma, years) <= 0 or option_type not in ('CE', 'PE')):
        raise ValueError('Invalid Black-Scholes inputs; IV must be decimal annualized volatility')
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * sigma * sigma) * years) / (sigma * root)
    d2 = d1 - sigma * root
    discount = math.exp(-dividend * years)
    density = discount * _normal_pdf(d1)
    delta = discount * (_normal_cdf(d1) - (option_type == 'PE'))
    gamma = density / (spot * sigma * root)
    charm = dividend * delta - density * (2 * (rate - dividend) * years - d2 * sigma * root) / (2 * years * sigma * root)
    return dict(delta=delta, gamma=gamma, vanna=-density * d2 / sigma,
                charm_per_year=charm, speed=-gamma / spot * (1 + d1 / (sigma * root)),
                volga=spot * density * root * d1 * d2 / sigma)


def exposure_profile(options, *, spot, years, oi_multiplier=1.0, rate=0.0, dividend=0.0):
    if not finite(oi_multiplier) or oi_multiplier <= 0 or not options:
        raise ValueError('Positive OI multiplier and nonempty option chain required')
    profile, prepared = {}, []
    for row in options:
        strike, oi, sigma, kind = (row[k] for k in ('strike', 'oi', 'sigma', 'option_type'))
        if not finite(oi) or oi < 0:
            raise ValueError('OI must be finite and nonnegative')
        greeks = spot_greeks(spot, strike, sigma, years, kind, rate, dividend)
        unsigned = oi * oi_multiplier
        weight = unsigned * (1 if kind == 'CE' else -1)
        entry = profile.setdefault(strike, dict(strike=strike, net_gamma=0.0, net_vanna=0.0,
            net_delta=0.0, net_speed=0.0, net_volga=0.0,
            charm_delta_per_day=0.0, call_gamma_dollar_1pct=0.0, put_gamma_dollar_1pct=0.0))
        entry['net_gamma'] += weight * greeks['gamma']
        entry['net_vanna'] += weight * greeks['vanna']
        for field in ('delta', 'speed', 'volga'):
            entry['net_' + field] += weight * greeks[field]
        entry['charm_delta_per_day'] += weight * greeks['charm_per_year'] / 365
        entry['call_gamma_dollar_1pct' if kind == 'CE' else 'put_gamma_dollar_1pct'] += unsigned * greeks['gamma'] * spot ** 2 * .01
        prepared.append((strike, sigma, weight))
    for entry in profile.values():
        entry['net_gamma_dollar_1pct'] = entry['net_gamma'] * spot ** 2 * .01
        entry['net_vanna_dollar_1iv_point'] = entry['net_vanna'] * spot * .01
    # Freeze IV/OI/time and reprice aggregate gamma versus hypothetical underlying levels.
    # Cache constant terms once; 21 grid points plus <=24 bisections per sign change.
    prepared = [(math.log(strike), sigma * math.sqrt(years),
                 (rate - dividend + .5 * sigma * sigma) * years, weight)
                for strike, sigma, weight in prepared]
    discount = math.exp(-dividend * years)
    def gamma_at(level):
        log_s = math.log(level)
        return sum(weight * discount * _normal_pdf((log_s - log_k + drift) / denominator)
                   / (level * denominator) for log_k, denominator, drift, weight in prepared)
    grid = [(spot * (.9 + .01 * i)) for i in range(21)]
    values = [gamma_at(level) for level in grid]
    roots = []
    indeterminate = all(value == 0 for value in values)
    if not indeterminate:
        for i, (left, right) in enumerate(zip(grid, grid[1:])):
            low, high = values[i], values[i + 1]
            if low == 0:
                roots.append(left)
            elif low * high < 0:
                for _ in range(24):
                    middle = (left + right) / 2
                    if gamma_at(middle) * low > 0:
                        left = middle
                    else:
                        right = middle
                roots.append((left + right) / 2)
        if values[-1] == 0:
            roots.append(grid[-1])
    rows = sorted(profile.values(), key=lambda row: row['strike'])
    def wall(field):
        row = max(rows, key=lambda row: row[field])
        return row['strike'] if row[field] > 0 else None
    return dict(model='Black-Scholes; call-positive/put-negative OI scenario', oi_multiplier=oi_multiplier,
                net_gamma_dollar_1pct=sum(row['net_gamma_dollar_1pct'] for row in rows),
                net_vanna_dollar_1iv_point=sum(row['net_vanna_dollar_1iv_point'] for row in rows),
                charm_delta_per_day=sum(row['charm_delta_per_day'] for row in rows),
                call_wall=wall('call_gamma_dollar_1pct'), put_wall=wall('put_gamma_dollar_1pct'),
                gamma_flip_candidates=roots, gamma_flip_indeterminate=indeterminate,
                nearest_gamma_flip=min(roots, key=lambda root: abs(root - spot)) if roots else None,
                gamma_flip_search_low=grid[0], gamma_flip_search_high=grid[-1], strikes=rows)
