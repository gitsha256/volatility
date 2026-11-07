from __future__ import annotations

import math
from dataclasses import dataclass


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class OptionGreeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def black76_price(
    *,
    futures_price: float,
    strike: float,
    sigma: float,
    time_to_expiry: float,
    rate: float = 0.0,
    option_type: str,
) -> float:
    if futures_price <= 0 or strike <= 0:
        raise ValueError("Futures price and strike must both be positive.")
    if sigma <= 0:
        raise ValueError("Volatility must be positive.")
    if time_to_expiry <= 0:
        raise ValueError("Time to expiry must be positive.")

    discount = math.exp(-rate * time_to_expiry)
    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(futures_price / strike) + 0.5 * sigma * sigma * time_to_expiry) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type.upper() == "CE":
        return discount * (futures_price * _normal_cdf(d1) - strike * _normal_cdf(d2))
    if option_type.upper() == "PE":
        return discount * (strike * _normal_cdf(-d2) - futures_price * _normal_cdf(-d1))
    raise ValueError("option_type must be 'CE' or 'PE'.")


def invert_black76_iv(
    *,
    futures_price: float,
    strike: float,
    market_price: float,
    time_to_expiry: float,
    rate: float = 0.0,
    option_type: str,
    lower: float = 1e-6,
    upper: float = 5.0,
    max_iterations: int = 200,
) -> float:
    if market_price <= 0:
        raise ValueError("Market price must be positive.")

    def f(sigma: float) -> float:
        return black76_price(
            futures_price=futures_price,
            strike=strike,
            sigma=sigma,
            time_to_expiry=time_to_expiry,
            rate=rate,
            option_type=option_type,
        ) - market_price

    lo = lower
    hi = upper
    for _ in range(max_iterations):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def option_greeks(
    *,
    futures_price: float,
    strike: float,
    sigma: float,
    time_to_expiry: float,
    rate: float = 0.0,
    option_type: str,
) -> OptionGreeks:
    discount = math.exp(-rate * time_to_expiry)
    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(futures_price / strike) + 0.5 * sigma * sigma * time_to_expiry) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type.upper() == "CE":
        delta = discount * _normal_cdf(d1)
        rho = -time_to_expiry * discount * strike * _normal_cdf(d2)
    elif option_type.upper() == "PE":
        delta = discount * (_normal_cdf(d1) - 1.0)
        rho = time_to_expiry * discount * strike * _normal_cdf(-d2)
    else:
        raise ValueError("option_type must be 'CE' or 'PE'.")

    gamma = discount * _normal_pdf(d1) / (futures_price * sigma * sqrt_t)
    vega = discount * futures_price * _normal_pdf(d1) * sqrt_t

    day_fraction = 1.0 / 365.0
    near = max(time_to_expiry - day_fraction, 1e-6)
    theta_current = black76_price(
        futures_price=futures_price,
        strike=strike,
        sigma=sigma,
        time_to_expiry=time_to_expiry,
        rate=rate,
        option_type=option_type,
    )
    theta_prev = black76_price(
        futures_price=futures_price,
        strike=strike,
        sigma=sigma,
        time_to_expiry=near,
        rate=rate,
        option_type=option_type,
    )
    theta = (theta_prev - theta_current) / day_fraction

    return OptionGreeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
