from pathlib import Path
import os

ENV_FILE = Path(os.getenv('VOLATILITY_ENV_FILE', Path(__file__).resolve().parents[1] / '.env')).expanduser().resolve()

from dataclasses import dataclass


@dataclass(frozen=True)
class ScannerConfig:
    symbol: str = "NIFTY"
    bar_interval: str = "5min"
    forecast_interval: str = "5min"
    ewma_lambda: float = 0.94
    session_minutes: int = 375
    min_iv_rv_spread: float = 2.0
    min_variance_ratio: float = 1.15
    strategies: tuple[str, ...] = ("long_straddle", "long_call", "long_put")
    lot_size: int = 1
    transaction_cost: float = 100.0
    max_lots: int = 1
    require_liquid_quotes: bool = True
    require_forecast_confidence: bool = True
    confidence_interval_sigma: float = 1.0

    @property
    def regular_session_minutes(self) -> int:
        return self.session_minutes
