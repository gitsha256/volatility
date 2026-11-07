from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


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
    expiry_data: list[dict[str, str]] = field(default_factory=list)


class FyersDataError(RuntimeError):
    """Raised when FYERS configuration or a market-data response is invalid."""


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


class FyersDataProvider(MarketDataProvider):
    """Fetch a current-expiry option chain and futures quote from FYERS API v3."""

    DATA_API = "https://api-t1.fyers.in/data"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        futures_symbol: str | None = None,
        option_chain_symbol: str = "NSE:NIFTY50-INDEX",
        strike_count: int = 10,
        timeout: float = 10.0,
    ) -> None:
        self.client_id = client_id or os.getenv("FYERS_CLIENT_ID")
        self.access_token = access_token or os.getenv("FYERS_ACCESS_TOKEN")
        self.futures_symbol = futures_symbol or os.getenv("FYERS_NIFTY_FUTURES_SYMBOL")
        self.option_chain_symbol = option_chain_symbol
        self.strike_count = strike_count
        self.timeout = timeout

        if not self.client_id or not self.access_token:
            raise FyersDataError(
                "Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN before selecting the FYERS data source."
            )
        if not self.futures_symbol:
            raise FyersDataError(
                "Set FYERS_NIFTY_FUTURES_SYMBOL to the active NIFTY futures contract symbol, "
                "for example NSE:<contract>-FUT."
            )
        if strike_count < 1:
            raise ValueError("strike_count must be at least 1.")

    def _fetch_json(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.DATA_API}{path}?{query}",
            headers={
                "Authorization": f"{self.client_id}:{self.access_token}",
                "Content-Type": "application/json",
                "version": "3",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise FyersDataError(f"FYERS data request failed with HTTP {error.code}.") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise FyersDataError("Could not connect to FYERS market data.") from error

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise FyersDataError("FYERS returned a non-JSON market-data response.") from error
        if not isinstance(payload, dict):
            raise FyersDataError("FYERS returned an unexpected market-data response.")
        if payload.get("s") != "ok":
            message = payload.get("message") or payload.get("error") or "request rejected"
            raise FyersDataError(f"FYERS market-data error: {message}")
        return payload

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def fetch_snapshot(self, *, symbol: str = "NIFTY") -> MarketSnapshot:
        chain_response = self._fetch_json(
            "/options-chain-v3",
            {"symbol": self.option_chain_symbol, "strikecount": self.strike_count, "timestamp": ""},
        )
        chain_data = chain_response.get("data")
        if not isinstance(chain_data, dict):
            raise FyersDataError("FYERS option-chain response is missing its data object.")

        raw_chain = chain_data.get("optionsChain")
        if not isinstance(raw_chain, list):
            raise FyersDataError("FYERS option-chain response is missing optionsChain rows.")

        quotes_by_strike: dict[float, OptionQuote] = {}
        for item in raw_chain:
            if not isinstance(item, dict):
                continue
            strike = self._number(item.get("strike_price"))
            option_type = str(item.get("option_type") or "").upper()
            if strike is None or strike <= 0 or option_type not in {"CE", "PE"}:
                continue

            quote = quotes_by_strike.setdefault(strike, OptionQuote(strike=strike))
            bid = self._number(item.get("bid"))
            ask = self._number(item.get("ask"))
            iv = self._number(item.get("iv"))
            if iv is not None and iv > 1:
                iv /= 100.0
            if option_type == "CE":
                quote.ce_bid, quote.ce_ask, quote.ce_iv = bid, ask, iv
            else:
                quote.pe_bid, quote.pe_ask, quote.pe_iv = bid, ask, iv

        if not quotes_by_strike:
            raise FyersDataError("FYERS returned no valid call/put option-chain rows.")

        quote_response = self._fetch_json("/quotes", {"symbols": self.futures_symbol})
        raw_quotes = quote_response.get("d")
        if not isinstance(raw_quotes, list) or not raw_quotes:
            raise FyersDataError("FYERS futures quote response is missing quote rows.")

        quote_row = next(
            (
                item for item in raw_quotes
                if isinstance(item, dict) and item.get("n") == self.futures_symbol
            ),
            raw_quotes[0] if isinstance(raw_quotes[0], dict) else None,
        )
        values = quote_row.get("v") if quote_row else None
        futures_price = self._number(values.get("lp")) if isinstance(values, dict) else None
        if futures_price is None or futures_price <= 0:
            raise FyersDataError("FYERS futures quote did not contain a valid last traded price.")

        expiry_data = chain_data.get("expiryData", [])
        normalized_expiries = [
            {"date": str(item.get("date", "")), "expiry": str(item.get("expiry", ""))}
            for item in expiry_data
            if isinstance(item, dict)
        ] if isinstance(expiry_data, list) else []

        return MarketSnapshot(
            symbol=symbol,
            futures_price=futures_price,
            option_chain=sorted(quotes_by_strike.values(), key=lambda quote: quote.strike),
            source="fyers",
            expiry_data=normalized_expiries,
        )
