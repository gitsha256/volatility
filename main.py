from __future__ import annotations

import argparse
import json

from volatility_scanner.config import ScannerConfig
from volatility_scanner.storage import initialize_database, save_opportunity_score
from volatility_scanner.strategy import NiftyScanner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NIFTY Volatility Scanner")
    parser.add_argument("--futures-price", type=float, required=True)
    parser.add_argument("--strike", type=float, required=True)
    parser.add_argument("--iv", type=float, required=True)
    parser.add_argument("--forecast-rv", type=float, required=True)
    parser.add_argument("--minutes-left", type=float, default=180.0)
    parser.add_argument("--strategy", choices=["long_straddle", "long_call", "long_put"], default="long_straddle")
    parser.add_argument("--expiry-days", type=float, default=7.0)
    parser.add_argument("--option-type", choices=["CE", "PE"], default="CE")
    parser.add_argument("--bid", type=float, default=100.0)
    parser.add_argument("--ask", type=float, default=120.0)
    parser.add_argument("--rate", type=float, default=0.0)
    parser.add_argument("--db-path", type=str, default="scanner.db")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ScannerConfig()
    scanner = NiftyScanner(config)
    opportunity = scanner.evaluate_single_strategy(
        strategy=args.strategy,
        futures_price=args.futures_price,
        strike=args.strike,
        iv=args.iv,
        forecast_rv=args.forecast_rv,
        expiry_days=args.expiry_days,
        remaining_minutes=args.minutes_left,
        option_type=args.option_type,
        bid=args.bid,
        ask=args.ask,
        rate=args.rate,
    )

    print(json.dumps({
        "strategy": opportunity.strategy,
        "volatility_spread": round(opportunity.volatility_spread, 4),
        "variance_ratio": round(opportunity.variance_ratio, 4),
        "implied_move": round(opportunity.implied_move, 2),
        "forecast_move": round(opportunity.forecast_move, 2),
        "expected_pnl": round(opportunity.expected_pnl, 2),
        "confidence": round(opportunity.confidence, 4),
        "qualifies": opportunity.qualifies,
        "direction_filter": opportunity.direction_filter,
    }, indent=2))

    conn = initialize_database(args.db_path)
    save_opportunity_score(conn, opportunity)
    conn.close()


if __name__ == "__main__":
    main()
