"""Minute calculations over the live WebSocket cache; no network requests or orders."""
import asyncio
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

    def calculate(self, recorder, timestamp):
        if recorder.contracts is not self._contracts:
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
            self.groups, self.signatures, self._contracts = groups, signatures, recorder.contracts
        spot = recorder.index.get('ltp')
        def fresh(received):
            return finite(received) and 0 <= timestamp - received <= 15
        spot_valid = recorder.connected and finite(spot) and spot > 0 and fresh(recorder.index_received)
        for expiry, strikes in self.groups.items():
            end = expiry_epoch(expiry)
            samples, options = [], []
            totals = [0.0, 0.0]
            complete = True
            for strike, contracts in strikes.items():
                counts = [0.0, 0.0]
                valid = (spot_valid and len(contracts) == 2
                         and {r['option_type'] for r in contracts} == {'CE', 'PE'})
                for row in contracts:
                    fields = recorder.latest.get(row['symbol'], {})
                    oi = fields.get('oi')
                    good = finite(oi) and oi >= 0 and fresh(recorder.received.get(row['symbol']))
                    valid = valid and good
                    if good:
                        side = 0 if row['option_type'] == 'CE' else 1
                        counts[side] += oi
                        raw_iv = fields.get('iv')
                        if finite(raw_iv) and raw_iv > 0:
                            options.append(dict(strike=strike, oi=oi, option_type=row['option_type'],
                                                sigma=raw_iv * self.iv_scale))
                complete = complete and valid
                totals = [a + b for a, b in zip(totals, counts)]
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
                              reason='expired' if timestamp >= end else 'incomplete_or_stale')
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
                        if previous and 0 < timestamp - previous[0] <= 90:
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


async def stream_analytics(recorder, **settings):
    scanner = StreamingAnalytics(**settings)
    while True:
        # Snapshot stream state on minute boundaries; timestamp records actual evaluation time.
        await asyncio.sleep(60 - time.time() % 60)
        for bucket, source, payload in scanner.calculate(recorder, time.time()):
            recorder.enqueue(bucket, source, payload)
