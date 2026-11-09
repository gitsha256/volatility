"""Incremental one-second consumer; ready results published every 60 seconds."""
import asyncio
import heapq
import time

from volatility_scanner.analytics.pcr_time import PCRTimeScanner, finite, strike_slopes, localize_stress, max_pain_strike, YEAR_SECONDS
from volatility_scanner.analytics.exposure import exposure_profile, spot_greeks
from volatility_scanner.analytics.premium import PremiumTracker
from .coverage import expiry_epoch


class StreamingAnalytics:
    def __init__(self, *, pcr=True, exposure=False, iv_unit='percent', oi_multiplier=1.0,
                 drift_seconds=3600, rate=0.0, dividend=0.0):
        if iv_unit not in ('percent', 'decimal') or not finite(oi_multiplier) or oi_multiplier <= 0:
            raise ValueError('Explicit IV unit and positive OI multiplier required')
        self.pcr, self.exposure = pcr, exposure
        self.iv_scale = .01 if iv_unit == 'percent' else 1.0
        self.oi_multiplier = oi_multiplier
        if not all(finite(v) for v in (drift_seconds,rate,dividend)) or drift_seconds <= 0:
            raise ValueError('Finite rates and a positive scenario horizon required')
        self.drift_seconds, self.rate, self.dividend = drift_seconds,rate,dividend
        self.scanner = PCRTimeScanner()
        self.premiums = PremiumTracker(self.scanner.config.max_gap_seconds)
        self._contracts = None
        self.groups = {}
        self.signatures = {}
        self.exposure_previous = {}
        self.locations, self.cached, self.strike_cache, self.expiry_cache = {}, {}, {}, {}
        self.expirations = []
        self.generation = None
        self.latest_output = ()
        self.latest_epoch = None
        self.calculation_ms = self.max_calculation_ms = 0.0
        self.missed_samples = 0

    def _refresh(self, recorder, key, timestamp):
        expiry, strike, side, row = self.locations[key]
        pair, total = self.strike_cache[expiry, strike], self.expiry_cache[expiry]
        fields, received = recorder.latest.get(key, {}), recorder.received.get(key)
        oi, iv = fields.get('oi'), fields.get('iv')
        good = finite(oi) and oi >= 0 and finite(received) and 0 <= timestamp - received <= 15
        old_good, old_oi, _, _ = self.cached.get(key, (False, 0, None, None))
        delta = (oi if good else 0) - (old_oi if old_good else 0)
        pair['counts'][side] += delta
        total['counts'][side] += delta
        pair['good'] += int(good) - int(old_good)
        total['good'] += int(good) - int(old_good)
        if good and finite(iv) and iv > 0:
            option = total['options'].get(key)
            if option is None or option['oi'] != oi or option['sigma'] != iv * self.iv_scale:
                total['options'][key] = dict(strike=strike, oi=oi, sigma=iv*self.iv_scale,
                                             option_type=row['option_type'],symbol=key)
        else:
            total['options'].pop(key, None)
        self.cached[key] = good, oi, iv, received
        if good:
            heapq.heappush(self.expirations, (received + 15, key))

    def calculate(self, recorder, timestamp):
        generation = getattr(recorder, 'analytics_generation', 0)
        rebuild = recorder.contracts is not self._contracts or generation != self.generation
        if rebuild:
            groups = {}
            for row in recorder.contracts.values():
                groups.setdefault(row['expiry_date'], {}).setdefault(float(row['strike_price']), []).append(row)
            signatures = {expiry: frozenset((r['symbol'], r['strike_price'], r['option_type'])
                          for rows in strikes.values() for r in rows) for expiry, strikes in groups.items()}
            changed = {expiry for expiry in self.signatures.keys() | signatures.keys()
                       if self.signatures.get(expiry) != signatures.get(expiry)}
            for key in list(self.scanner.history):
                if key[0] in changed:
                    del self.scanner.history[key]
            for expiry in changed:
                self.exposure_previous.pop(expiry, None)
            if generation != self.generation:
                self.scanner.history.clear()
                self.exposure_previous.clear()
                self.premiums.previous.clear()
            for symbol in list(self.premiums.previous):
                if symbol not in recorder.contracts or recorder.contracts[symbol]['expiry_date'] in changed:
                    del self.premiums.previous[symbol]
            self.groups, self.signatures, self._contracts = groups, signatures, recorder.contracts
            self.generation = generation
            self.locations, self.cached, self.strike_cache, self.expiry_cache = {}, {}, {}, {}
            self.expirations = []
            for expiry, strikes in groups.items():
                self.expiry_cache[expiry] = dict(counts=[0.0, 0.0], good=0, options={})
                for strike, rows in strikes.items():
                    paired = len(rows) == 2 and {r['option_type'] for r in rows} == {'CE', 'PE'}
                    self.strike_cache[expiry, strike] = dict(counts=[0.0, 0.0], good=0, paired=paired)
                    for row in rows:
                        self.locations[row['symbol']] = expiry, strike, (0 if row['option_type']=='CE' else 1), row
        dirty = getattr(recorder, 'analytics_dirty', None)
        updates = self.locations if rebuild or dirty is None else dirty
        for key in updates:
            if key in self.locations:
                self._refresh(recorder, key, timestamp)
        if dirty is not None:
            dirty.clear()
        while self.expirations and self.expirations[0][0] < timestamp:
            deadline, key = heapq.heappop(self.expirations)
            old = self.cached.get(key)
            if old and old[0] and old[3] + 15 == deadline:
                self._refresh(recorder, key, timestamp)
        if len(self.expirations) > max(1, len(self.locations) * 4):
            self.expirations = [(row[3]+15,key) for key,row in self.cached.items() if row[0]]
            heapq.heapify(self.expirations)
        spot = recorder.index.get('ltp')
        def fresh(received):
            return finite(received) and 0 <= timestamp - received <= 15
        spot_valid = recorder.connected and finite(spot) and spot > 0 and fresh(recorder.index_received)
        for expiry, strikes in self.groups.items():
            end = expiry_epoch(expiry)
            samples = []
            aggregate = self.expiry_cache[expiry]
            options = list(aggregate['options'].values()) if self.exposure else ()
            totals = aggregate['counts']
            complete = spot_valid
            for strike, contracts in strikes.items():
                pair = self.strike_cache[expiry, strike]
                counts = pair['counts']
                valid = spot_valid and pair['paired'] and pair['good'] == 2
                complete = complete and valid
                if self.pcr:
                    result = self.scanner.update((expiry, strike), timestamp=timestamp, expiry=end,
                        call_oi=counts[0], put_oi=counts[1], spot=spot, valid=valid)
                    result.update(strike=strike, expiry=expiry, scope='selected_strike_pair',
                                  call_oi=counts[0], put_oi=counts[1])
                    samples.append(result)
            expected = sum(map(len, strikes.values()))
            pcr_context = None
            if self.pcr:
                strike_slopes(samples)
                result = self.scanner.update((expiry, None), timestamp=timestamp, expiry=end,
                    call_oi=totals[0], put_oi=totals[1], spot=spot, valid=complete)
                result.update(expiry=expiry, scope='selected_expiry_contracts', contracts_expected=expected,
                              call_oi=totals[0], put_oi=totals[1], connected=recorder.connected)
                result['max_pain'] = max_pain_strike({strike:self.strike_cache[expiry,strike]['counts']
                    for strike in strikes}) if complete and timestamp<end else None
                result['distance_to_max_pain'] = spot-result['max_pain'] if result['max_pain'] is not None else None
                pcr_context = {key:result.get(key) for key in ('pcr','pcr_over_t3','stress_rate_growth_fraction',
                    'positioning_derivative_alert','max_pain','distance_to_max_pain')}
                localize_stress(samples, result, spot)
                for sample in samples:
                    contract = strikes[sample['strike']][0]
                    yield recorder.symbols[contract['symbol']][0], 'pcr_time_minute', sample
                yield expiry + '_PCR_TIME', 'pcr_time_minute', result
            if self.exposure:
                models = {}
                status = dict(sample_epoch=timestamp, expiry=expiry, complete=False,
                              pcr_context=pcr_context,
                              contracts_expected=expected, contracts_with_oi_iv=len(options),
                              reason=('expired' if timestamp >= end else
                                      'missing_or_invalid_iv' if complete and len(options) != expected else
                                      'incomplete_or_stale'))
                if complete and len(options) == expected and timestamp < end:
                    try:
                        profile = exposure_profile(options, spot=spot, years=(end - timestamp) / YEAR_SECONDS,
                            oi_multiplier=self.oi_multiplier,include_contracts=True,drift_seconds=self.drift_seconds,
                            rate=self.rate,dividend=self.dividend)
                        models = {row['symbol']:row['greeks'] for row in profile['contracts']}
                        def all_finite(value):
                            if isinstance(value, dict):
                                return all(all_finite(v) for v in value.values())
                            if isinstance(value, list):
                                return all(all_finite(v) for v in value)
                            return not isinstance(value, (int, float)) or isinstance(value, bool) or finite(value)
                        if not all_finite(profile):
                            raise ValueError('Nonfinite exposure')
                        status.update({k: v for k, v in profile.items() if k not in ('strikes','contracts')})
                        status.update(complete=True, reason=None, iv_unit='percent' if self.iv_scale == .01 else 'decimal')
                        previous = self.exposure_previous.get(expiry)
                        status['net_gamma_dollar_change_per_minute'] = None
                        if previous and 0 < timestamp - previous[0] <= self.scanner.config.max_gap_seconds:
                            status['net_gamma_dollar_change_per_minute'] = (
                                profile['net_gamma_dollar_1pct'] - previous[1]) * 60 / (timestamp - previous[0])
                            status['net_vanna_dollar_change_per_minute'] = (
                                profile['net_vanna_dollar_1iv_point']-previous[2])*60/(timestamp-previous[0])
                            status['gamma_regime_changed'] = previous[3] != profile['gamma_regime']
                            status['crossed_previous_gamma_flip'] = previous[5] is not None and (previous[4]-previous[5])*(spot-previous[5])<0
                        else:
                            status['net_vanna_dollar_change_per_minute'] = None
                            status['gamma_regime_changed'] = False
                            status['crossed_previous_gamma_flip'] = False
                        status['distance_to_call_wall'] = profile['call_wall']-spot if profile['call_wall'] is not None else None
                        status['distance_to_put_wall'] = spot-profile['put_wall'] if profile['put_wall'] is not None else None
                        status['distance_to_gamma_flip'] = profile['nearest_gamma_flip']-spot if profile['nearest_gamma_flip'] is not None else None
                        self.exposure_previous[expiry] = (timestamp, profile['net_gamma_dollar_1pct'],
                                                          profile['net_vanna_dollar_1iv_point'],profile['gamma_regime'],spot,profile['nearest_gamma_flip'])
                        for row in profile['strikes']:
                            contract = strikes[row['strike']][0]
                            yield recorder.symbols[contract['symbol']][0], 'exposure_minute', dict(
                                row, sample_epoch=timestamp, expiry=expiry, model=profile['model'])
                    except (ValueError, ArithmeticError):
                        status['reason'] = 'invalid_model_inputs_or_numeric_range'
                if not status['complete']:
                    self.exposure_previous.pop(expiry, None)
                yield expiry + '_EXPOSURE', 'exposure_minute', status
                for rows in strikes.values():
                    for contract in rows:
                        key=contract['symbol']
                        option=aggregate['options'].get(key)
                        observation=dict(symbol=key,expiry=expiry,sample_epoch=timestamp,
                            option_type=contract['option_type'],strike=contract['strike_price'],complete=False,
                            reason='expired' if timestamp>=end else 'missing_or_stale_oi_iv_spot')
                        if option and spot_valid and timestamp<end:
                            try:
                                greeks=models.get(key) or spot_greeks(spot,option['strike'],option['sigma'],
                                    (end-timestamp)/YEAR_SECONDS,option['option_type'],self.rate,self.dividend)
                                if not all(finite(v) for v in greeks.values()):
                                    raise ValueError('Nonfinite contract model')
                                fields=recorder.latest.get(key,{})
                                price=fields.get('ltp',fields.get('ltpc.ltp'))
                                price_epoch=getattr(recorder,'price_received',{}).get(key)
                                premium=price if (finite(price) and price>0 and fresh(price_epoch)
                                    and fresh(fields.get('trade_epoch'))) else None
                                observation.update(self.premiums.observe(symbol=key,timestamp=timestamp,expiry=end,
                                    spot=spot,sigma=option['sigma'],greeks=greeks,premium=premium),reason=None)
                            except (ValueError,ArithmeticError):
                                observation['reason']='invalid_model_inputs_or_numeric_range'
                        if not observation['complete']:
                            self.premiums.previous.pop(key,None)
                        yield recorder.symbols[key][0], 'premium_minute', observation

    def consume(self, recorder, timestamp):
        started = time.perf_counter_ns()
        if self.latest_epoch is not None:
            self.missed_samples += max(0, int(timestamp) - int(self.latest_epoch) - 1)
            if timestamp <= self.latest_epoch:
                self.scanner.history.clear()
                self.exposure_previous.clear()
                self.premiums.previous.clear()
        self.latest_output = tuple(self.calculate(recorder, timestamp))
        self.latest_epoch = timestamp
        self.calculation_ms = (time.perf_counter_ns() - started) / 1e6
        self.max_calculation_ms = max(self.max_calculation_ms, self.calculation_ms)
        return self.latest_output

    def health(self):
        return dict(latest_epoch=self.latest_epoch, calculation_ms=self.calculation_ms,
                    max_calculation_ms=self.max_calculation_ms, missed_samples=self.missed_samples,
                    ready_results=len(self.latest_output))


async def stream_analytics(recorder, **settings):
    scanner = StreamingAnalytics(**settings)
    recorder.algorithm_consumer = scanner
    published_minute = int(time.time() // 60)
    while True:
        # No historical replay on a delay: use the current cache and measured timestamp.
        timestamp = time.time()
        scanner.consume(recorder, timestamp)
        minute = int(timestamp // 60)
        if minute != published_minute:
            for bucket, source, payload in scanner.latest_output:
                recorder.enqueue(bucket, source, payload)
            published_minute = minute
        await asyncio.sleep(max(.001, 1 - time.time() % 1))
