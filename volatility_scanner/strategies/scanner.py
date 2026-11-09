from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from volatility_scanner.config import ScannerConfig
from volatility_scanner.analytics.pricing import OptionGreeks, option_greeks
from volatility_scanner.analytics.volatility import remaining_session_move


@dataclass
class OpportunityScore:
    strategy: str
    futures_price: float
    strike: float
    iv: float
    forecast_rv: float
    volatility_spread: float
    variance_ratio: float
    implied_move: float
    forecast_move: float
    gamma_theta: float
    expected_pnl: float
    confidence: float
    direction_filter: str = "pass"
    qualifies: bool = False


@dataclass
class ScannerResult:
    opportunity: OpportunityScore
    option_greeks: OptionGreeks


class NiftyScanner:
    def __init__(self, config: ScannerConfig | None = None) -> None:
        self.config = config or ScannerConfig()

    def evaluate_single_strategy(
        self,
        *,
        strategy: str,
        futures_price: float,
        strike: float,
        iv: float,
        forecast_rv: float,
        expiry_days: float,
        remaining_minutes: float,
        option_type: str,
        bid: float,
        ask: float,
        rate: float = 0.0,
    ) -> OpportunityScore:
        if not all(math.isfinite(value) for value in (
            futures_price, strike, iv, forecast_rv, expiry_days, remaining_minutes, bid, ask, rate
        )):
            raise ValueError("Scanner inputs must be finite numbers.")
        if futures_price <= 0 or strike <= 0:
            raise ValueError("Futures price and strike must be positive.")
        if expiry_days < 0 or remaining_minutes < 0:
            raise ValueError("Expiry days and remaining minutes cannot be negative.")
        if bid < 0 or ask <= 0 or ask < bid:
            raise ValueError("Quotes must satisfy 0 <= bid <= ask and ask > 0.")
        if iv <= 0 or forecast_rv <= 0:
            raise ValueError("IV and forecast RV must both be positive.")

        implied_move = remaining_session_move(
            futures_price=futures_price,
            sigma=iv,
            remaining_minutes=remaining_minutes,
            session_minutes=self.config.session_minutes,
        )
        forecast_move = remaining_session_move(
            futures_price=futures_price,
            sigma=forecast_rv,
            remaining_minutes=remaining_minutes,
            session_minutes=self.config.session_minutes,
        )
        spread = forecast_rv - iv
        variance_ratio = forecast_rv / iv
        estimated_cost = self.config.transaction_cost + abs(bid - ask) * 10.0
        direction_filter = "pass"
        if strategy in {"long_call", "long_put"}:
            direction_bias = futures_price - strike if option_type.upper() == "CE" else strike - futures_price
            direction_filter = "pass" if direction_bias >= 0 and spread > 0 else "fail"

        greeks = option_greeks(
            futures_price=futures_price,
            strike=strike,
            sigma=iv,
            time_to_expiry=max(expiry_days / 365.0, 1e-6),
            rate=rate,
            option_type=option_type,
        )
        gamma_theta = greeks.gamma + greeks.theta
        expected_pnl = (forecast_move - implied_move) * 0.6 - estimated_cost
        if strategy == "long_straddle":
            expected_pnl = (forecast_move - implied_move) * 1.0 - estimated_cost
            gamma_theta = (greeks.gamma + greeks.theta) * 2.0

        confidence = 1.0 - min(1.0, abs(iv - forecast_rv) / max(forecast_rv, 1e-6))
        qualifies = (
            remaining_minutes > 0
            and expiry_days > 0
            and expected_pnl > 0
            and spread > self.config.min_iv_rv_spread / 100.0
            and variance_ratio >= self.config.min_variance_ratio
            and (strategy != "long_straddle" or gamma_theta < 0)
            and direction_filter == "pass"
        )

        return OpportunityScore(
            strategy=strategy,
            futures_price=futures_price,
            strike=strike,
            iv=iv,
            forecast_rv=forecast_rv,
            volatility_spread=spread,
            variance_ratio=variance_ratio,
            implied_move=implied_move,
            forecast_move=forecast_move,
            gamma_theta=gamma_theta,
            expected_pnl=expected_pnl,
            confidence=confidence,
            direction_filter=direction_filter,
            qualifies=qualifies,
        )

    def scan_chain(
        self,
        *,
        futures_price: float,
        strikes: Iterable[float],
        option_type: str,
        ivs: Iterable[float],
        forecast_rv: float,
        expiry_days: float,
        remaining_minutes: float,
        bids: Iterable[float],
        asks: Iterable[float],
        strategy: str = "long_straddle",
        rate: float = 0.0,
    ) -> list[OpportunityScore]:
        all_scores: list[OpportunityScore] = []
        strike_list = list(strikes)
        iv_list = list(ivs)
        bid_list = list(bids)
        ask_list = list(asks)

        if not (len(strike_list) == len(iv_list) == len(bid_list) == len(ask_list)):
            raise ValueError("All strike-related inputs must have identical lengths.")

        for strike, iv, bid, ask in zip(strike_list, iv_list, bid_list, ask_list):
            score = self.evaluate_single_strategy(
                strategy=strategy,
                futures_price=futures_price,
                strike=strike,
                iv=iv,
                forecast_rv=forecast_rv,
                expiry_days=expiry_days,
                remaining_minutes=remaining_minutes,
                option_type=option_type,
                bid=bid,
                ask=ask,
                rate=rate,
            )
            all_scores.append(score)
        return all_scores

    def best_opportunity(self, scores: Iterable[OpportunityScore]) -> OpportunityScore | None:
        ranked = sorted(scores, key=lambda s: (s.qualifies, s.expected_pnl), reverse=True)
        return ranked[0] if ranked else None

    def evaluate_all_strategies(
        self,
        *,
        strategies: Iterable[str],
        futures_price: float,
        strike: float,
        iv: float,
        forecast_rv: float,
        expiry_days: float,
        remaining_minutes: float,
        option_type: str,
        bid: float,
        ask: float,
        rate: float = 0.0,
    ) -> list[OpportunityScore]:
        results: list[OpportunityScore] = []
        for strategy in strategies:
            results.append(
                self.evaluate_single_strategy(
                    strategy=strategy,
                    futures_price=futures_price,
                    strike=strike,
                    iv=iv,
                    forecast_rv=forecast_rv,
                    expiry_days=expiry_days,
                    remaining_minutes=remaining_minutes,
                    option_type=option_type,
                    bid=bid,
                    ask=ask,
                    rate=rate,
                )
            )
        return results
