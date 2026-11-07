# NIFTY Volatility Scanner

This project implements a compact analytical engine for NIFTY futures + options screening based on the design in the technical brief.

## Features

- Black-76 pricing and IV inversion for futures options
- Greek calculations for CE/PE positions
- Realized-volatility forecasting using EWMA, Parkinson and realized variance
- Horizon alignment for remaining-session analysis
- Strategy scoring for long straddles, long calls and long puts
- SQLite persistence for scored opportunities
- Optional Streamlit dashboard scaffold

## Quick start

CLI:

```bash
python -m main --futures-price 25000 --strike 25000 --iv 0.14 --forecast-rv 0.18 --minutes-left 180 --strategy long_straddle
```

Dashboard:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

This prints the core metrics, including volatility spread, variance ratio, implied and forecast move, and confidence.

## Modules

- `volatility_scanner/config.py` — scanner defaults and configuration
- `volatility_scanner/pricing.py` — Black-76 pricing, IV inversion and Greeks
- `volatility_scanner/volatility.py` — RV and forecast engine
- `volatility_scanner/strategy.py` — opportunity scoring and scanner logic
- `volatility_scanner/storage.py` — SQLite persistence utilities
- `main.py` — CLI entry point

## Design alignment

The implementation follows the recommended architecture:

- Data acquisition is intentionally isolated behind a clean model boundary.
- Option analytics uses Black-76, not Black-Scholes.
- Forecasting combines EWMA + Parkinson + realized volatility.
- Intraday horizon alignment compares appropriate remaining-session variance.
- Mispricing detection is separated from strategy evaluation.
- Risk controls and confidence estimates are included in scoring.

## Notes

This is a research and execution-prep engine, not a ready-to-trade autotrader. The defaults are intentionally conservative and should be validated with historical backtests before live use.
