"""Plan-aware NIFTY streaming coverage and supplementary market snapshots."""
import asyncio
import logging
import math
from datetime import datetime

from volatility_scanner.security.tokens import AuthError
from volatility_scanner.storage.journal import IST
from volatility_scanner.analytics.pcr_time import max_pain_strike

INDEX = 'NSE_INDEX|Nifty 50'
VIX = 'NSE_INDEX|India VIX'
log = logging.getLogger(__name__)


def expiry_epoch(date):
    return int(datetime.fromisoformat(date + 'T15:30:00').replace(tzinfo=IST).timestamp())


def discover(rest, scope='front_two', include_context=True):
    payload = rest.get('v2/option/contract', {'instrument_key': INDEX})
    today = datetime.now(IST).date().isoformat()
    contracts = [dict(r, symbol=r['instrument_key'], option_type=r['instrument_type'],
                      expiry_date=r['expiry']) for r in payload['data'] if r['expiry'] >= today]
    if not contracts:
        raise AuthError('Upstox returned no active NIFTY options')
    nearest = min(r['expiry'] for r in contracts)
    if scope == 'current':
        contracts = [r for r in contracts if r['expiry'] == nearest]
    elif scope == 'front_two':
        dates = set(sorted({r['expiry'] for r in contracts})[:2])
        contracts = [r for r in contracts if r['expiry'] in dates]
    futures = []
    page = 1
    while include_context:
        result = rest.get('v2/instruments/search', {'query': 'NIFTY', 'exchanges': 'NSE',
            'segments': 'FO', 'instrument_types': 'FUT', 'records': 30, 'page_number': page})
        futures.extend(r for r in result.get('data', []) if r.get('underlying_key') == INDEX)
        if page >= result.get('meta_data', {}).get('page', {}).get('total_pages', 1):
            break
        page += 1
    snapshot = rest.get('v2/option/chain', {'instrument_key': INDEX, 'expiry_date': nearest})
    if not snapshot.get('data'):
        raise AuthError('Upstox returned no NIFTY chain snapshot')
    spot = float(snapshot['data'][0]['underlying_spot_price'])
    return contracts, futures, spot


def coverage_plan(contracts, futures, spot, depth_contracts=50, include_context=True, feed_mode='full'):
    # Mixed-mode per-category limits: full 1500, greeks 2000, D30 50.
    # Keep full + D30 + context under 1500 as a conservative depth budget.
    contracts = sorted(contracts, key=lambda r: (r['expiry_date'], abs(float(r['strike_price']) - spot),
                                               float(r['strike_price']), r['option_type']))
    context = {INDEX}
    if include_context:
        context.update({VIX, *(r['instrument_key'] for r in futures)})
    deep = {r['symbol'] for r in contracts[:depth_contracts]}
    if feed_mode != 'full':
        if depth_contracts:
            raise AuthError('30-level depth requires --feed-mode full')
        if len(contracts) > 2000:
            raise AuthError('Selected universe exceeds mixed-mode instrument limit')
        return {'full': context, feed_mode: {r['symbol'] for r in contracts}}
    full_count = max(0, 1500 - len(context) - len(deep))
    remaining = [r for r in contracts if r['symbol'] not in deep]
    full = {r['symbol'] for r in remaining[:full_count]} | context
    greeks = {r['symbol'] for r in remaining[full_count:]}
    if len(greeks) > 2000:
        raise AuthError('NIFTY universe exceeds documented mixed-mode capacity; choose --expiry-scope current')
    return {'full_d30': deep, 'full': full, 'option_greeks': greeks}


def sync_subscriptions(streamer, plan):
    for mode, active in list(streamer.subscriptions.items()):
        removed = set(active) - plan.get(mode, set())
        if removed:
            streamer.unsubscribe(sorted(removed))
    for mode, wanted in plan.items():
        added = wanted - streamer.subscriptions[mode]
        if added:
            streamer.subscribe(sorted(added), mode)


async def supplements(rest, recorder, enabled):
    daily = {}
    while True:
        today = datetime.now(IST).date().isoformat()
        expiries = sorted({r['expiry_date'] for r in recorder.contracts.values()})
        for expiry in expiries:
            common = {'instrument_key': INDEX, 'expiry': expiry, 'date': today}
            for endpoint in ('chain', 'oi', 'pcr', 'max-pain'):
                if endpoint not in enabled:
                    continue
                params = dict(common)
                path = 'v2/market/' + endpoint
                if endpoint == 'chain':
                    path = 'v2/option/chain'
                    params = {'instrument_key': INDEX, 'expiry_date': expiry}
                elif endpoint != 'oi':
                    params['bucket_interval'] = 1
                try:
                    result = await asyncio.to_thread(rest.get, path, params)
                    recorder.enqueue(f'{expiry}_{endpoint}', 'rest', result)
                except AuthError as error:
                    log.warning('%s; stream capture continues', error)
        if enabled & {'fii', 'dii', 'calendar'}:
            jobs = [('v2/market/fii', [('data_type', 'NSE_FO|INDEX_FUTURES'),
                     ('data_type', 'NSE_FO|INDEX_OPTIONS'), ('data_type', 'NSE_EQ|CASH'), ('interval', '1D')]),
                    ('v2/market/dii', {'data_type': 'NSE_EQ|CASH', 'interval': '1D'}),
                    ('v2/market/timings/' + today, {}), ('v2/market/holidays', {})]
            for path, params in jobs:
                name = path.split('/')[2]
                if name not in enabled and not (name in ('timings', 'holidays') and 'calendar' in enabled):
                    continue
                if daily.get(path) == today:
                    continue
                try:
                    result = await asyncio.to_thread(rest.get, path, params)
                    recorder.enqueue(path.split('/')[2].upper(), 'rest', result)
                    daily[path] = today
                except AuthError as error:
                    log.warning('%s; stream capture continues', error)
        await asyncio.sleep(60)

def local_summary(contracts, latest):
    strikes = {}
    seen = 0
    for row in contracts:
        oi = latest.get(row['symbol'], {}).get('oi')
        if not isinstance(oi, (int, float)) or not math.isfinite(oi) or oi < 0:
            continue
        seen += 1
        legs = strikes.setdefault(float(row['strike_price']), [0, 0])
        legs[0 if row['option_type'] == 'CE' else 1] += oi
    call_oi = sum(v[0] for v in strikes.values())
    put_oi = sum(v[1] for v in strikes.values())
    complete = seen == len(contracts)
    best = max_pain_strike(strikes) if complete else None
    return dict(call_oi=call_oi, put_oi=put_oi, contracts_seen=seen,
                contracts_expected=len(contracts), complete=complete,
                pcr=put_oi / call_oi if complete and call_oi else None,
                max_pain=best if complete else None)


async def local_analytics(recorder):
    while True:
        grouped = {}
        for row in recorder.contracts.values():
            grouped.setdefault(row['expiry_date'], []).append(row)
        for expiry, contracts in grouped.items():
            recorder.enqueue(expiry + '_LOCAL_ANALYTICS', 'derived_one_second', dict(
                local_summary(contracts, recorder.latest), connected=recorder.connected,
                oldest_quote_receipt_epoch=min((recorder.received.get(r['symbol'], 0) for r in contracts), default=0)))
        await asyncio.sleep(1)
