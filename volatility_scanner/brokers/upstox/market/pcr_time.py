"""Incremental one-second consumer; ready results published every 60 seconds."""
import asyncio
import heapq
import time

from volatility_scanner.analytics.pcr_time import PCRTimeScanner, finite, strike_slopes, YEAR_SECONDS
from volatility_scanner.analytics.exposure import exposure_profile
from .coverage import expiry_epoch


class StreamingAnalytics:
    def __init__(self, *, pcr=True, exposure=False, iv_unit='percent', oi_multiplier=1.0):
        if iv_unit not in ('percent', 'decimal') or not finite(oi_multiplier) or oi_multiplier <= 0:
            raise ValueError('Explicit IV unit and positive OI multiplier required')
        self.pcr, self.exposure = pcr, exposure
        self.iv_scale = .01 if iv_unit == 'percent' else 1.0
        self.oi_multiplier = oi_multiplier
        self.scanner = PCRTimeScanner()
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
                                             option_type=row['option_type'])
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
            if self.pcr:
                strike_slopes(samples)
                for result in samples:
                    contract = strikes[result['strike']][0]
                    bucket = recorder.symbols[contract['symbol']][0]
                    yield bucket, 'pcr_time_minute', result
                result = self.scanner.update((expiry, None), timestamp=timestamp, expiry=end,
                    call_oi=totals[0], put_oi=totals[1], spot=spot, valid=complete)
                result.update(expiry=expiry, scope='selected_expiry_contracts', contracts_expected=expected,
                              call_oi=totals[0], put_oi=totals[1], connected=recorder.connected)
                yield expiry + '_PCR_TIME', 'pcr_time_minute', result
            if self.exposure:
                status = dict(sample_epoch=timestamp, expiry=expiry, complete=False,
                              contracts_expected=expected, contracts_with_oi_iv=len(options),
                              reason=('expired' if timestamp >= end else
                                      'missing_or_invalid_iv' if complete and len(options) != expected else
                                      'incomplete_or_stale'))
                if complete and len(options) == expected and timestamp < end:
                    try:
                        profile = exposure_profile(options, spot=spot, years=(end - timestamp) / YEAR_SECONDS,
                                                   oi_multiplier=self.oi_multiplier)
                        def all_finite(value):
                            if isinstance(value, dict):
                                return all(all_finite(v) for v in value.values())
                            if isinstance(value, list):
                                return all(all_finite(v) for v in value)
                            return not isinstance(value, (int, float)) or isinstance(value, bool) or finite(value)
                        if not all_finite(profile):
                            raise ValueError('Nonfinite exposure')
                        status.update({k: v for k, v in profile.items() if k != 'strikes'})
                        status.update(complete=True, reason=None, iv_unit='percent' if self.iv_scale == .01 else 'decimal')
                        previous = self.exposure_previous.get(expiry)
                        status['net_gamma_dollar_change_per_minute'] = None
                        if previous and 0 < timestamp - previous[0] <= self.scanner.config.max_gap_seconds:
                            status['net_gamma_dollar_change_per_minute'] = (
                                profile['net_gamma_dollar_1pct'] - previous[1]) * 60 / (timestamp - previous[0])
                        self.exposure_previous[expiry] = (timestamp, profile['net_gamma_dollar_1pct'])
                        for row in profile['strikes']:
                            contract = strikes[row['strike']][0]
                            yield recorder.symbols[contract['symbol']][0], 'exposure_minute', dict(
                                row, sample_epoch=timestamp, expiry=expiry, model=profile['model'])
                    except (ValueError, ArithmeticError):
                        status['reason'] = 'invalid_model_inputs_or_numeric_range'
                if not status['complete']:
                    self.exposure_previous.pop(expiry, None)
                yield expiry + '_EXPOSURE', 'exposure_minute', status

    def consume(self, recorder, timestamp):
        started = time.perf_counter_ns()
        if self.latest_epoch is not None:
            self.missed_samples += max(0, int(timestamp) - int(self.latest_epoch) - 1)
            if timestamp <= self.latest_epoch:
                self.scanner.history.clear()
                self.exposure_previous.clear()
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
