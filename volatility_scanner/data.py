from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class OptionQuote:
    strike: float
    ce_bid: float | None = None
    ce_ask: float | None = None
    pe_bid: float | None = None
    pe_ask: float | None = None
    ce_iv: float | None = None
    pe_iv: float | None = None


@dataclass
class MarketSnapshot:
    symbol: str
    futures_price: float
    option_chain: list[OptionQuote] = field(default_factory=list)
    source: str = "sample"
    warning: str | None = None


class MarketDataProvider:
    def fetch_snapshot(self, *, symbol: str = "NIFTY") -> MarketSnapshot:
        raise NotImplementedError


class SampleMarketDataProvider(MarketDataProvider):
    def fetch_snapshot(self, *, symbol: str = "NIFTY") -> MarketSnapshot:
        futures = 25000.0
        strikes = [24750, 24800, 24850, 24900, 24950, 25000, 25050, 25100, 25150, 25200]
        option_chain: list[OptionQuote] = []
        for strike in strikes:
            option_chain.append(
                OptionQuote(
                    strike=strike,
                    ce_bid=max(30.0, (futures - strike) * 0.18 + 20.0),
                    ce_ask=max(35.0, (futures - strike) * 0.18 + 25.0),
                    pe_bid=max(28.0, (strike - futures) * 0.18 + 18.0),
                    pe_ask=max(34.0, (strike - futures) * 0.18 + 24.0),
                    ce_iv=0.14,
                    pe_iv=0.14,
                )
            )
        return MarketSnapshot(symbol=symbol, futures_price=futures, option_chain=option_chain, source="sample")


class NSEDataProvider(MarketDataProvider):
    def _fetch_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_snapshot(self, *, symbol: str = "NIFTY") -> MarketSnapshot:
        try:
            feed = self._fetch_json("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY")
            records = feed.get("records", {}).get("data", [])
            if not records:
                raise ValueError("No records returned")

            futures_price = float(feed.get("records", {}).get("underlyingValue", 25000.0))
            option_chain: list[OptionQuote] = []
            for record in records[:10]:
                strike = float(record.get("strikePrice", 0.0))
                if strike <= 0:
                    continue
                ce = record.get("CE", {})
                pe = record.get("PE", {})
                option_chain.append(
                    OptionQuote(
                        strike=strike,
                        ce_bid=ce.get("bidPrice"),
                        ce_ask=ce.get("askPrice"),
                        pe_bid=pe.get("bidPrice"),
                        pe_ask=pe.get("askPrice"),
                        ce_iv=ce.get("impliedVolatility"),
                        pe_iv=pe.get("impliedVolatility"),
                    )
                )

            if not option_chain:
                raise ValueError("No option chain rows parsed")

            return MarketSnapshot(symbol=symbol, futures_price=futures_price, option_chain=option_chain, source="nse")
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            fallback = SampleMarketDataProvider().fetch_snapshot(symbol=symbol)
            return MarketSnapshot(
                symbol=symbol,
                futures_price=fallback.futures_price,
                option_chain=fallback.option_chain,
                source="sample",
                warning="NSE API unavailable; sample data was used as fallback.",
            )
