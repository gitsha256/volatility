# Upstox API reference

Bundled OpenAPI source: [official contract](https://api.upstox.com/v2/api-docs).
Reviewed against 100 API reference pages in the [official documentation index](https://upstox.com/developer/api-documentation/llms.txt).
105 catalogue entries: 103 published contract operations plus 2 documented V3 feed routes.
V2 market feeds are discontinued and fail locally; use V3. `publishEvent` is contract-only, without a public guide.

Each operation is available as `client.call(operation, ...)` or `client.<group>.<operation>(...)`.
Supply request sections as `path={...}`, `query={...}`, `headers={...}`, and `body=...`.

## account

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getUserIps](https://upstox.com/developer/api-documentation/get-app-static-ips) | GET | `/v2/user/ip` | no |
| [updateUserIp](https://upstox.com/developer/api-documentation/update-app-static-ips) | PUT | `/v2/user/ip` | no |
| [getKillSwitch](https://upstox.com/developer/api-documentation/get-kill-switch) | GET | `/v2/user/kill-switch` | no |
| [updateKillSwitch](https://upstox.com/developer/api-documentation/update-kill-switch) | POST | `/v2/user/kill-switch` | no |
| [getUserFundMarginV3](https://upstox.com/developer/api-documentation/get-funds-and-margin-v3) | GET | `/v3/user/get-funds-and-margin` | no |
| [getProfile](https://upstox.com/developer/api-documentation/get-profile) | GET | `/v2/user/profile` | no |
| [getUserFundMargin](https://upstox.com/developer/api-documentation/get-user-fund-margin) | GET | `/v2/user/get-funds-and-margin` | no |

## analytics

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getExchangeTimings](https://upstox.com/developer/api-documentation/get-market-timings) | GET | `/v2/market/timings/{date}` | no |
| [getMarketStatus](https://upstox.com/developer/api-documentation/get-market-status) | GET | `/v2/market/status/{exchange}` | no |
| [getSmartlistOptions](https://upstox.com/developer/api-documentation/get-options-smartlist) | GET | `/v2/market/smartlist/options` | no |
| [getSmartlistMtf](https://upstox.com/developer/api-documentation/get-mtf-smartlist) | GET | `/v2/market/smartlist/mtf` | no |
| [getSmartlistFutures](https://upstox.com/developer/api-documentation/get-futures-smartlist) | GET | `/v2/market/smartlist/futures` | no |
| [getPcrData](https://upstox.com/developer/api-documentation/get-pcr) | GET | `/v2/market/pcr` | no |
| [getOiData](https://upstox.com/developer/api-documentation/get-oi) | GET | `/v2/market/oi` | no |
| [getMaxPainData](https://upstox.com/developer/api-documentation/get-max-pain) | GET | `/v2/market/max-pain` | no |
| [getHolidays](https://upstox.com/developer/api-documentation/get-market-holidays) | GET | `/v2/market/holidays` | no |
| [getHoliday](https://upstox.com/developer/api-documentation/get-market-holidays) | GET | `/v2/market/holidays/{date}` | no |
| [getFiiData](https://upstox.com/developer/api-documentation/get-fii-data) | GET | `/v2/market/fii` | no |
| [getDiiData](https://upstox.com/developer/api-documentation/get-dii-data) | GET | `/v2/market/dii` | no |
| [getChangeOiData](https://upstox.com/developer/api-documentation/get-change-oi) | GET | `/v2/market/change-oi` | no |

## authentication

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [initTokenRequestForIndieUser](https://upstox.com/developer/api-documentation/access-token-request) | POST | `/v3/login/auth/token/request/{client_id}` | no |
| [generateTokenViaTotpAndPin](https://upstox.com/developer/api-documentation/get-token) | POST | `/v2/totp-login/authorization/token` | no |
| [token](https://upstox.com/developer/api-documentation/get-token) | POST | `/v2/login/authorization/token` | no |
| [authorize](https://upstox.com/developer/api-documentation/authorize) | GET | `/v2/login/authorization/dialog` | no |
| [logout](https://upstox.com/developer/api-documentation/logout) | DELETE | `/v2/logout` | no |

## charges

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [postMargin](https://upstox.com/developer/api-documentation/margin) | POST | `/v2/charges/margin` | no |
| [getBrokerage](https://upstox.com/developer/api-documentation/get-brokerage) | GET | `/v2/charges/brokerage` | no |

## events

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| publishEvent | POST | `/v2/events` | no |

## fundamentals

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getShareHoldings](https://upstox.com/developer/api-documentation/get-share-holdings) | GET | `/v2/fundamentals/{isin}/share-holdings` | no |
| [getCompanyProfile](https://upstox.com/developer/api-documentation/get-company-profile) | GET | `/v2/fundamentals/{isin}/profile` | no |
| [getKeyRatios](https://upstox.com/developer/api-documentation/get-key-ratios) | GET | `/v2/fundamentals/{isin}/key-ratios` | no |
| [getIncomeStatement](https://upstox.com/developer/api-documentation/get-income-statement) | GET | `/v2/fundamentals/{isin}/income-statement` | no |
| [getCorporateActions](https://upstox.com/developer/api-documentation/get-corporate-actions) | GET | `/v2/fundamentals/{isin}/corporate-actions` | no |
| [getCashFlow](https://upstox.com/developer/api-documentation/get-cash-flow) | GET | `/v2/fundamentals/{isin}/cash-flow` | no |
| [getBalanceSheet](https://upstox.com/developer/api-documentation/get-balance-sheet) | GET | `/v2/fundamentals/{isin}/balance-sheet` | no |
| [getCompetitors](https://upstox.com/developer/api-documentation/get-competitors) | GET | `/v2/fundamentals/{instrument_key}/competitors` | no |

## history

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getHistoricalCandleData](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data) | GET | `/v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to_date}` | no |
| [getHistoricalCandleData_1](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data) | GET | `/v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to_date}/{from_date}` | no |
| [getIntraDayCandleData](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data) | GET | `/v3/historical-candle/intraday/{instrumentKey}/{unit}/{interval}` | no |
| [getHistoricalCandleData_2](https://upstox.com/developer/api-documentation/get-historical-candle-data) | GET | `/v2/historical-candle/{instrumentKey}/{interval}/{to_date}` | no |
| [getHistoricalCandleData_3](https://upstox.com/developer/api-documentation/get-historical-candle-data) | GET | `/v2/historical-candle/{instrumentKey}/{interval}/{to_date}/{from_date}` | no |
| [getIntraDayCandleData_1](https://upstox.com/developer/api-documentation/get-intra-day-candle-data) | GET | `/v2/historical-candle/intraday/{instrumentKey}/{interval}` | no |
| [getExpiredOptionContracts](https://upstox.com/developer/api-documentation/get-expired-option-contracts) | GET | `/v2/expired-instruments/option/contract` | no |
| [getExpiredHistoricalCandleData](https://upstox.com/developer/api-documentation/get-expired-historical-candle-data) | GET | `/v2/expired-instruments/historical-candle/{expired_instrument_key}/{interval}/{to_date}/{from_date}` | no |
| [getExpiredFutureContracts](https://upstox.com/developer/api-documentation/get-expired-future-contracts) | GET | `/v2/expired-instruments/future/contract` | no |
| [getExpiriesResponse](https://upstox.com/developer/api-documentation/get-expiries) | GET | `/v2/expired-instruments/expiries` | no |

## instruments

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [searchInstrument](https://upstox.com/developer/api-documentation/instrument-search) | GET | `/v2/instruments/search` | no |

## ipo

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getIpoOrders](https://upstox.com/developer/api-documentation/get-ipo-orders) | GET | `/v2/ipos/orders` | no |
| [applyForIpo](https://upstox.com/developer/api-documentation/apply-ipo) | POST | `/v2/ipos/orders` | no |
| [getIpoListing](https://upstox.com/developer/api-documentation/get-ipos) | GET | `/v2/ipos` | no |
| [getIpoDetails](https://upstox.com/developer/api-documentation/get-ipo-details) | GET | `/v2/ipos/{id}` | no |
| [getIpoOrderById](https://upstox.com/developer/api-documentation/get-ipo-order-details) | GET | `/v2/ipos/orders/{order_id}` | no |
| [cancelIpoOrder](https://upstox.com/developer/api-documentation/cancel-ipo-order) | DELETE | `/v2/ipos/orders/{order_id}` | no |

## market

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getFullMarketQuoteV3](https://upstox.com/developer/api-documentation/get-full-market-quote-v3) | GET | `/v3/market-quote/quotes` | no |
| [getMarketQuoteOptionGreek](https://upstox.com/developer/api-documentation/option-greek) | GET | `/v3/market-quote/option-greek` | no |
| [getMarketQuoteOHLCV3](https://upstox.com/developer/api-documentation/get-market-quote-ohlc-v3) | GET | `/v3/market-quote/ohlc` | no |
| [getLtp](https://upstox.com/developer/api-documentation/ltp-v3) | GET | `/v3/market-quote/ltp` | no |
| [getOptionContracts](https://upstox.com/developer/api-documentation/get-option-contracts) | GET | `/v2/option/contract` | no |
| [getPutCallOptionChain](https://upstox.com/developer/api-documentation/get-pc-option-chain) | GET | `/v2/option/chain` | no |
| [getFullMarketQuote](https://upstox.com/developer/api-documentation/get-full-market-quote) | GET | `/v2/market-quote/quotes` | no |
| [getMarketQuoteOHLC](https://upstox.com/developer/api-documentation/get-market-quote-ohlc) | GET | `/v2/market-quote/ohlc` | no |
| [ltp](https://upstox.com/developer/api-documentation/ltp) | GET | `/v2/market-quote/ltp` | no |

## mutual_funds

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getMutualFundSips](https://upstox.com/developer/api-documentation/get-mutual-fund-sips) | GET | `/v2/mf/sips` | no |
| [getMutualFundOrders](https://upstox.com/developer/api-documentation/get-mutual-fund-orders) | GET | `/v2/mf/orders` | no |
| [getMutualFundOrder](https://upstox.com/developer/api-documentation/get-mutual-fund-order-by-id) | GET | `/v2/mf/orders/{order_id}` | no |
| [getMutualFundHoldings](https://upstox.com/developer/api-documentation/get-mutual-fund-holdings) | GET | `/v2/mf/holdings` | no |

## news

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getNews](https://upstox.com/developer/api-documentation/get-news) | GET | `/v2/news` | no |

## payments

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [modifyPayout](https://upstox.com/developer/api-documentation/modify-payout) | PUT | `/v2/user/payments/payout/{transaction_id}` | no |
| [cancelPayout](https://upstox.com/developer/api-documentation/cancel-payout) | DELETE | `/v2/user/payments/payout/{transaction_id}` | no |
| [getPayoutHistory](https://upstox.com/developer/api-documentation/get-user-payouts) | GET | `/v2/user/payments/payout` | no |
| [initiatePayout](https://upstox.com/developer/api-documentation/payout-request) | POST | `/v2/user/payments/payout` | no |
| [getPayoutModes](https://upstox.com/developer/api-documentation/get-payout-modes) | GET | `/v2/user/payments/payout/modes` | no |
| [getPayinHistory](https://upstox.com/developer/api-documentation/get-user-payins) | GET | `/v2/user/payments/payin` | no |

## portfolio

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [convertPositions](https://upstox.com/developer/api-documentation/convert-positions) | PUT | `/v2/portfolio/convert-position` | no |
| [getMtfPositions](https://upstox.com/developer/api-documentation/get-mtf-positions) | GET | `/v3/portfolio/mtf-positions` | no |
| [getPositions](https://upstox.com/developer/api-documentation/get-positions) | GET | `/v2/portfolio/short-term-positions` | no |
| [getHoldings](https://upstox.com/developer/api-documentation/get-holdings) | GET | `/v2/portfolio/long-term-holdings` | no |

## reports

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getTradeWiseProfitAndLossMetaData](https://upstox.com/developer/api-documentation/get-report-meta-data) | GET | `/v2/trade/profit-loss/metadata` | no |
| [getTradeWiseProfitAndLossData](https://upstox.com/developer/api-documentation/get-profit-and-loss-report) | GET | `/v2/trade/profit-loss/data` | no |
| [getProfitAndLossCharges](https://upstox.com/developer/api-documentation/get-trade-charges) | GET | `/v2/trade/profit-loss/charges` | no |
| [getTradesByDateRange](https://upstox.com/developer/api-documentation/get-trade-history) | GET | `/v2/charges/historical-trades` | no |

## streams

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [getPortfolioStreamFeed](https://upstox.com/developer/api-documentation/get-portfolio-stream-feed) | GET | `/v2/feed/portfolio-stream-feed` | no |
| [getPortfolioStreamFeedAuthorize](https://upstox.com/developer/api-documentation/get-portfolio-stream-feed-authorize) | GET | `/v2/feed/portfolio-stream-feed/authorize` | no |
| [getMarketDataFeed](https://upstox.com/developer/api-documentation/get-market-data-feed) | GET | `/v2/feed/market-data-feed` | no |
| [getMarketDataFeedAuthorize](https://upstox.com/developer/api-documentation/get-market-data-feed-authorize) | GET | `/v2/feed/market-data-feed/authorize` | no |
| [getMarketDataFeedV3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed) | GET | `/v3/feed/market-data-feed` | no |
| [getMarketDataFeedAuthorizeV3](https://upstox.com/developer/api-documentation/get-market-data-feed-authorize-v3) | GET | `/v3/feed/market-data-feed/authorize` | no |

## trading

| Operation | Method | Path | Sandbox |
|---|---|---|---|
| [modifyOrder](https://upstox.com/developer/api-documentation/v3/modify-order) | PUT | `/v3/order/modify` | yes |
| [modifyGTTOrder](https://upstox.com/developer/api-documentation/modify-gtt-order) | PUT | `/v3/order/gtt/modify` | no |
| [modifyOrder_1](https://upstox.com/developer/api-documentation/modify-order) | PUT | `/v2/order/modify` | yes |
| [placeOrder](https://upstox.com/developer/api-documentation/v3/place-order) | POST | `/v3/order/place` | yes |
| [placeGTTOrder](https://upstox.com/developer/api-documentation/place-gtt-order) | POST | `/v3/order/gtt/place` | no |
| [exitPositions](https://upstox.com/developer/api-documentation/exit-all-positions) | POST | `/v2/order/positions/exit` | no |
| [placeOrder_1](https://upstox.com/developer/api-documentation/place-order) | POST | `/v2/order/place` | yes |
| [placeMultiOrder](https://upstox.com/developer/api-documentation/place-multi-order) | POST | `/v2/order/multi/place` | yes |
| [getGttOrderDetails](https://upstox.com/developer/api-documentation/get-gtt-order-details) | GET | `/v3/order/gtt` | no |
| [getTradesByOrder](https://upstox.com/developer/api-documentation/get-trades-by-order) | GET | `/v2/order/trades` | no |
| [getTradeHistory](https://upstox.com/developer/api-documentation/get-historical-trades) | GET | `/v2/order/trades/get-trades-for-day` | no |
| [getOrderBook](https://upstox.com/developer/api-documentation/get-order-book) | GET | `/v2/order/retrieve-all` | no |
| [getOrderDetails](https://upstox.com/developer/api-documentation/get-order-history) | GET | `/v2/order/history` | no |
| [getOrderStatus](https://upstox.com/developer/api-documentation/get-order-details) | GET | `/v2/order/details` | no |
| [cancelGTTOrder](https://upstox.com/developer/api-documentation/cancel-gtt-order) | DELETE | `/v3/order/gtt/cancel` | no |
| [cancelOrder](https://upstox.com/developer/api-documentation/v3/cancel-order) | DELETE | `/v3/order/cancel` | yes |
| [cancelMultiOrder](https://upstox.com/developer/api-documentation/cancel-multi-order) | DELETE | `/v2/order/multi/cancel` | no |
| [cancelOrder_1](https://upstox.com/developer/api-documentation/cancel-order) | DELETE | `/v2/order/cancel` | yes |
