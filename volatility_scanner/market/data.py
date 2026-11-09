from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from volatility_scanner.config import ENV_FILE
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from curl_cffi import requests as curl_requests, CurlHttpVersion
from volatility_scanner.analytics.pricing import invert_black76_iv
from volatility_scanner.brokers.fyers.auth import AuthError, ensure_access_token



@dataclass
class OptionQuote:
    strike: float
    ce_bid: float | None = None
    ce_ask: float | None = None
    pe_bid: float | None = None
    pe_ask: float | None = None
    ce_iv: float | None = None
    pe_iv: float | None = None
    ce_iv_source: str = "unavailable"
    pe_iv_source: str = "unavailable"
    ce_symbol: str | None = None
    pe_symbol: str | None = None
    ce_updated_at: float = 0.0
    pe_updated_at: float = 0.0


@dataclass
class MarketSnapshot:
    symbol: str
    futures_price: float
    option_chain: list[OptionQuote] = field(default_factory=list)
    source: str = "sample"
    warning: str | None = None
    expiry_data: list[dict[str, str]] = field(default_factory=list)
    last_update: float = 0.0
    feed_status: str = "snapshot"
    http_version: str = ""
    futures_updated_at: float = 0.0


class FyersDataError(RuntimeError):
    """Raised when FYERS configuration or a market-data response is invalid."""


def derive_missing_ivs(snapshot: MarketSnapshot, time_to_expiry: float) -> None:
    """Estimate Black-76 IV using same-expiry put-call parity at zero rate."""
    if not math.isfinite(time_to_expiry) or time_to_expiry <= 0:
        return
    def valid(bid: float | None, ask: float | None) -> bool:
        return (bid is not None and ask is not None and math.isfinite(bid)
                and math.isfinite(ask) and 0 < bid <= ask)

    paired = [q for q in snapshot.option_chain if valid(q.ce_bid, q.ce_ask) and valid(q.pe_bid, q.pe_ask)]
    if not paired:
        return
    atm = min(paired, key=lambda q: abs((q.ce_bid + q.ce_ask) - (q.pe_bid + q.pe_ask)))
    forward = atm.strike + (atm.ce_bid + atm.ce_ask - atm.pe_bid - atm.pe_ask) / 2.0
    if not math.isfinite(forward) or forward <= 0:
        return
    for quote in snapshot.option_chain:
        for leg in ("ce", "pe"):
            iv = getattr(quote, leg + "_iv")
            if iv is not None and math.isfinite(iv) and iv > 0 and getattr(quote, leg + "_iv_source") != "derived":
                continue
            setattr(quote, leg + "_iv", None)
            setattr(quote, leg + "_iv_source", "unavailable")
            bid, ask = getattr(quote, leg + "_bid"), getattr(quote, leg + "_ask")
            if not valid(bid, ask):
                continue
            try:
                iv = invert_black76_iv(
                    futures_price=forward, strike=quote.strike, market_price=(bid + ask) / 2.0,
                    time_to_expiry=time_to_expiry, option_type=leg.upper(),
                )
            except ValueError:
                continue
            setattr(quote, leg + "_iv", iv)
            setattr(quote, leg + "_iv_source", "derived")


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
                    ce_iv_source="sample",
                    pe_iv_source="sample",
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
        # Read only this repository's file; never expand references to other secrets.
        file_env = dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig")
        self.client_id = client_id or os.getenv("FYERS_CLIENT_ID", file_env.get("FYERS_CLIENT_ID"))
        self.access_token = access_token or os.getenv("FYERS_ACCESS_TOKEN", file_env.get("FYERS_ACCESS_TOKEN"))
        self.futures_symbol = futures_symbol or os.getenv(
            "FYERS_NIFTY_FUTURES_SYMBOL", file_env.get("FYERS_NIFTY_FUTURES_SYMBOL")
        )
        self.option_chain_symbol = option_chain_symbol
        self.strike_count = strike_count
        self.timeout = timeout
        self._http = None
        self.http_version = ""
        self._managed_token = access_token is None
        if self._managed_token and self.client_id:
            self.refresh_access_token()

        if not self.client_id or not self.access_token:
            raise FyersDataError(
                "Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in .env or the environment "
                "before selecting the FYERS data source."
            )
        if not self.futures_symbol:
            raise FyersDataError(
                "Set FYERS_NIFTY_FUTURES_SYMBOL to the active NIFTY futures contract symbol, "
                "for example NSE:<contract>-FUT."
            )
        if strike_count < 1:
            raise ValueError("strike_count must be at least 1.")

    def refresh_access_token(self, *, force: bool = False) -> None:
        try:
            self.access_token = ensure_access_token(self.client_id, self.access_token, ENV_FILE, force=force)
        except AuthError as error:
            raise FyersDataError(str(error)) from None

    def _fetch_json(self, path: str, params: dict[str, str | int], *, _retried: bool = False) -> dict[str, Any]:
        if self._http is None:
            self._http = curl_requests.Session(http_version=CurlHttpVersion.V3)
        request_options = dict(
            params=params, timeout=self.timeout, allow_redirects=False,
            headers={
                "Authorization": f"{self.client_id}:{self.access_token}",
                "Content-Type": "application/json", "version": "3", "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            response = self._http.get(f"{self.DATA_API}{path}", **request_options)
        except curl_requests.RequestsError:
            # Some networks block QUIC instead of allowing negotiation to finish.
            try:
                self._http.http_version = CurlHttpVersion.V2TLS
                response = self._http.get(f"{self.DATA_API}{path}", **request_options)
            except curl_requests.RequestsError:
                raise FyersDataError("Could not connect to FYERS market data.") from None
        self.http_version = {
            CurlHttpVersion.V3: "HTTP/3", CurlHttpVersion.V2_0: "HTTP/2",
            CurlHttpVersion.V1_1: "HTTP/1.1",
        }.get(response.http_version, "HTTP")
        if response.status_code == 401 and self._managed_token and not _retried:
            self.refresh_access_token(force=True)
            return self._fetch_json(path, params, _retried=True)
        if response.status_code != 200:
            raise FyersDataError(f"FYERS data request failed with HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as error:
            raise FyersDataError("FYERS returned a non-JSON market-data response.") from error
        if not isinstance(payload, dict):
            raise FyersDataError("FYERS returned an unexpected market-data response.")
        if payload.get("code") in (-8, -15, -16, -17) and self._managed_token and not _retried:
            self.refresh_access_token(force=True)
            return self._fetch_json(path, params, _retried=True)
        if payload.get("s") != "ok":
            message = payload.get("message") or payload.get("error") or "request rejected"
            raise FyersDataError(f"FYERS market-data error: {message}")
        return payload

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
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
                quote.ce_symbol = item.get("symbol")
                quote.ce_bid, quote.ce_ask, quote.ce_iv = bid, ask, iv
                quote.ce_iv_source = "FYERS" if iv is not None and iv > 0 else "unavailable"
            else:
                quote.pe_symbol = item.get("symbol")
                quote.pe_bid, quote.pe_ask, quote.pe_iv = bid, ask, iv
                quote.pe_iv_source = "FYERS" if iv is not None and iv > 0 else "unavailable"

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

        snapshot = MarketSnapshot(
            symbol=symbol,
            futures_price=futures_price,
            option_chain=sorted(quotes_by_strike.values(), key=lambda quote: quote.strike),
            source="fyers",
            expiry_data=normalized_expiries,
            last_update=time.time(),
            http_version=self.http_version,
        )
        expiry = self._number(normalized_expiries[0]["expiry"]) if normalized_expiries else None
        if expiry is not None:
            derive_missing_ivs(snapshot, (expiry - time.time()) / (365.0 * 86400.0))
        return snapshot
