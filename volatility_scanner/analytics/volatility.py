from __future__ import annotations

import math
from typing import Iterable


def ewma_volatility(returns: Iterable[float], lam: float = 0.94) -> float:
    values = list(returns)
    if not values:
        return 0.0
    variance = 0.0
    for idx, ret in enumerate(values):
        if idx == 0:
            variance = ret * ret
        else:
            variance = lam * variance + (1.0 - lam) * ret * ret
    return math.sqrt(variance)


def parkinson_volatility(high: Iterable[float], low: Iterable[float]) -> float:
    h = list(high)
    l = list(low)
    if len(h) != len(l) or len(h) == 0:
        raise ValueError("High and low series must be aligned and non-empty.")

    total = 0.0
    for hi, lo in zip(h, l):
        if hi <= 0 or lo <= 0:
            continue
        total += (math.log(hi / lo) ** 2)
    return math.sqrt(total / (4.0 * math.log(2.0) * len(h)))


def realized_volatility(log_returns: Iterable[float], *, annualize: bool = True, session_minutes: int = 375, bar_minutes: int = 5) -> float:
    values = list(log_returns)
    if not values:
        return 0.0
    session_rv = math.sqrt(sum(r * r for r in values))
    if not annualize:
        return session_rv
    annualization_factor = math.sqrt(252.0 * (session_minutes / bar_minutes))
    return session_rv * annualization_factor


def ensemble_forecast_volatility(
    *,
    returns: Iterable[float],
    high: Iterable[float],
    low: Iterable[float],
    weights: tuple[float, float, float] = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0),
    lam: float = 0.94,
    annualize: bool = True,
    session_minutes: int = 375,
    bar_minutes: int = 5,
) -> float:
    w1, w2, w3 = weights
    forecast_ewma = ewma_volatility(returns, lam=lam)
    forecast_parkinson = parkinson_volatility(high, low)
    forecast_realized = realized_volatility(returns, annualize=annualize, session_minutes=session_minutes, bar_minutes=bar_minutes)
    return w1 * forecast_ewma + w2 * forecast_parkinson + w3 * forecast_realized


def remaining_session_move(
    futures_price: float,
    sigma: float,
    remaining_minutes: float,
    *,
    session_minutes: int = 375,
    trading_days_per_year: int = 252,
) -> float:
    if remaining_minutes <= 0:
        return 0.0
    fraction = remaining_minutes / float(session_minutes)
    return futures_price * sigma * math.sqrt(fraction / trading_days_per_year)
