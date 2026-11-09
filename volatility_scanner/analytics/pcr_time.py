"""Incremental second-sampled PCR windows; derivatives retain per-minute units."""
from collections import deque
from dataclasses import dataclass
import math
from itertools import islice

YEAR_SECONDS = 365 * 86400


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def derivative(samples, order):
    """Backward Lagrange finite difference at the newest timestamp, in minutes."""
    if len(samples) < order + 1:
        return None
    points = list(islice(reversed(samples), order + 1))
    points.reverse()
    origin = points[-1][0]
    xs = [(point[0] - origin) / 60 for point in points]
    result = 0.0
    for i, (_, value, *_) in enumerate(points):
        polynomial, denominator = [1.0], 1.0
        for j, x in enumerate(xs):
            if j == i:
                continue
            coefficients = [0.0] * (len(polynomial) + 1)
            for power, coefficient in enumerate(polynomial):
                coefficients[power] -= x * coefficient
                coefficients[power + 1] += coefficient
            polynomial = coefficients
            denominator *= xs[i] - x
        result += value * polynomial[order] / denominator
    return result * math.factorial(order)


@dataclass(frozen=True)
class PCRTimeConfig:
    window_seconds: float = 300
    min_expiry_seconds: float = 60
    max_gap_seconds: float = 2.5
    flat_spot_fraction: float = 0.001
    spike_fraction: float = 5.0

    def __post_init__(self):
        values = (self.window_seconds, self.min_expiry_seconds, self.max_gap_seconds,
                  self.flat_spot_fraction, self.spike_fraction)
        if not all(finite(v) for v in values) or min(values) <= 0:
            raise ValueError('PCR configuration values must be finite and positive')
        if self.window_seconds < 1 or self.max_gap_seconds < 1:
            raise ValueError('Window and maximum gap must support second sampling')


class RollingWindow(deque):
    """One-second ring plus monotonic extrema queues: amortized O(1) updates."""
    def __init__(self, seconds):
        super().__init__(maxlen=math.ceil(seconds) + 4)
        self.minimum, self.maximum = deque(), deque()

    def clear(self):
        super().clear()
        self.minimum.clear()
        self.maximum.clear()

    def push(self, sample, seconds):
        self.append(sample)
        target = sample[0] - seconds
        while len(self) > 1 and self[1][0] <= target:
            self.popleft()
        for queue, minimum in ((self.minimum, True), (self.maximum, False)):
            while queue and (queue[-1][1] >= sample[2] if minimum else queue[-1][1] <= sample[2]):
                queue.pop()
            queue.append((sample[0], sample[2]))
            while queue[0][0] < self[0][0]:
                queue.popleft()


class PCRTimeScanner:
    def __init__(self, config=None):
        self.config = config or PCRTimeConfig()
        self.history = {}

    def update(self, key, *, timestamp, expiry, call_oi, put_oi, spot, valid=True):
        if not finite(timestamp) or not finite(expiry):
            raise ValueError('Timestamp and expiry must be finite epoch seconds')
        result = dict(sample_epoch=timestamp, expiry_epoch=expiry, valid=False,
                      pcr=None, pcr_over_t=None, pcr_over_t2=None, pcr_over_t3=None,
                      pcr_d1_per_minute=None, pcr_d2_per_minute2=None, pcr_d3_per_minute3=None,
                      pcr_over_t3_change_per_minute=None,
                      window_ready=False, raw_stress_alert=False, positioning_stress_alert=False)
        remaining = expiry - timestamp
        if remaining <= 0:
            reason = 'expired'
        elif not valid:
            reason = 'incomplete_or_stale'
        elif not all(finite(v) for v in (call_oi, put_oi, spot)) or call_oi <= 0 or put_oi < 0 or spot <= 0:
            reason = 'invalid_input_or_zero_call_oi'
        else:
            reason = None
        if reason:
            self.history.pop(key, None)
            return dict(result, reason=reason)
        history = self.history.get(key)
        if history is None:
            history = self.history[key] = RollingWindow(self.config.window_seconds)
        if history and timestamp <= history[-1][0]:
            return dict(result, reason='non_increasing_timestamp')
        if history and timestamp - history[-1][0] > self.config.max_gap_seconds:
            history.clear()
        pcr = put_oi / call_oi
        effective_seconds = max(remaining, self.config.min_expiry_seconds)
        t = effective_seconds / YEAR_SECONDS
        scores = [pcr / t ** power for power in (1, 2, 3)]
        if not all(math.isfinite(v) for v in (pcr, *scores)):
            self.history.pop(key, None)
            return dict(result, reason='numeric_overflow')
        history.push((timestamp, pcr, spot, scores[2], effective_seconds), self.config.window_seconds)
        target = timestamp - self.config.window_seconds
        floored = remaining < self.config.min_expiry_seconds
        result.update(valid=True, reason=None, pcr=pcr, seconds_to_expiry=remaining,
                      time_years=t, time_floor_active=floored,
                      pcr_over_t=scores[0], pcr_over_t2=scores[1], pcr_over_t3=scores[2])
        if len(history) > 1:
            previous = history[-2]
            result['pcr_over_t3_change_per_minute'] = (scores[2] - previous[3]) * 60 / (timestamp - previous[0])
        for order in (1, 2, 3):
            result[f'pcr_d{order}_per_minute' + (str(order) if order > 1 else '')] = derivative(history, order)
        baseline = history[0]
        if baseline[0] <= target and target - baseline[0] <= self.config.max_gap_seconds:
            result['window_ready'] = True
            result['window_elapsed_seconds'] = timestamp - baseline[0]
            spot_range = (history.maximum[0][1] - history.minimum[0][1]) / baseline[2]
            result['spot_range_fraction'] = spot_range
            result['spot_flat'] = spot_range <= self.config.flat_spot_fraction
            result['time_only_growth_fraction'] = (baseline[4] / effective_seconds) ** 3 - 1
            if baseline[1] > 0 and baseline[3] > 0:
                growth = scores[2] / baseline[3] - 1
                pcr_growth = pcr / baseline[1] - 1
                result.update(stress_growth_fraction=growth, pcr_growth_fraction=pcr_growth)
                raw = growth > self.config.spike_fraction and result['spot_flat'] and not floored
                result['raw_stress_alert'] = raw
                # Require positioning growth as well as clock-driven amplification.
                result['positioning_stress_alert'] = raw and pcr_growth > self.config.spike_fraction
        return result


def strike_slopes(rows):
    """Neighbour secants on actual strike spacing; missing pairs are never bridged."""
    rows.sort(key=lambda row: row['strike'])
    for i, row in enumerate(rows):
        left, right = rows[max(0, i - 1)], rows[min(len(rows) - 1, i + 1)]
        values = (left.get('pcr_over_t3'), right.get('pcr_over_t3'), row.get('pcr_over_t3'))
        row['pcr_over_t3_strike_slope'] = ((values[1] - values[0]) / (right['strike'] - left['strike'])
            if all(finite(v) for v in values) and right['strike'] != left['strike'] else None)
