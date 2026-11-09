"""Estimated depletion with median filtering, hysteresis and explicit temporal coverage."""
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from itertools import groupby
from statistics import median
from zoneinfo import ZoneInfo


def boundaries(start: date, end: date, group_by: str, tz: ZoneInfo):
    """Half-open calendar buckets, including clipped first/last buckets and DST."""
    result = [datetime.combine(start, datetime.min.time(), tz).timestamp()]
    cursor = start
    while cursor < end:
        if group_by == 'day':
            cursor += timedelta(days=1)
        elif group_by == 'month':
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        else:
            cursor = date(cursor.year + 1, 1, 1)
        cursor = min(cursor, end)
        result.append(datetime.combine(cursor, datetime.min.time(), tz).timestamp())
    return result


def estimate(rows, start, end, group_by, timezone, settings, now=None):
    tz = ZoneInfo(timezone)
    now = datetime.now(UTC).timestamp() if now is None else now
    edges = boundaries(start, end, group_by, tz)
    buckets = [{'start': datetime.fromtimestamp(a, tz).date().isoformat(),
                'end': datetime.fromtimestamp(b, tz).date().isoformat(),
                'consumption_liters': None, 'refill_liters': None, 'coverage_percent': 0,
                'status': 'no_data', 'valid_readings': 0, '_covered': 0.0, '_consumed': 0.0,
                '_refilled': 0.0} for a, b in zip(edges, edges[1:], strict=False)]

    def distribute(a, b, field, value):
        if b <= a or a >= edges[-1] or b <= edges[0]:
            return
        index = max(0, bisect_right(edges, a) - 1)
        while index < len(buckets) and edges[index] < b:
            overlap = max(0, min(b, edges[index + 1]) - max(a, edges[index]))
            buckets[index][field] += value * overlap / (b - a)
            index += 1

    # A centered median requires a following sample. Single-sample spikes and the
    # unconfirmed newest sample cannot become consumption or refill events.
    rows = [row for row in rows if row['received_at'] <= now]
    # Give minute-by-minute prototype bursts and hourly production readings
    # the same temporal weight. A short burst must not look like a tank refill.
    hourly = []
    for _, points in groupby(rows, key=lambda row: (int(row['received_at'] // 3600), row['calibration_key'])):
        points = list(points)
        hourly.append({**points[len(points) // 2],
                       'received_at': median([row['received_at'] for row in points]),
                       'volume_liters': median([row['volume_liters'] for row in points])})
    stable = []
    for a, b, c in zip(hourly, hourly[1:], hourly[2:], strict=False):
        if a['calibration_key'] == b['calibration_key'] == c['calibration_key'] and (
            0 < b['received_at'] - a['received_at'] <= settings.consumption_max_gap_seconds
            and 0 < c['received_at'] - b['received_at'] <= settings.consumption_max_gap_seconds
        ):
            stable.append({**b, 'volume_liters': median([a['volume_liters'], b['volume_liters'], c['volume_liters']])})
    events = []
    anchor = previous = None
    for row in stable:
        if previous is None or row['calibration_key'] != previous['calibration_key'] or (
            row['received_at'] - previous['received_at'] > settings.consumption_max_gap_seconds
        ):
            anchor = previous = row
            continue
        distribute(previous['received_at'], row['received_at'], '_covered', row['received_at'] - previous['received_at'])
        decline = anchor['volume_liters'] - row['volume_liters']
        if decline >= settings.consumption_noise_liters:
            distribute(anchor['received_at'], row['received_at'], '_consumed', decline)
            anchor = row
        elif -decline >= settings.consumption_refill_liters:
            index = bisect_right(edges, row['received_at']) - 1
            if 0 <= index < len(buckets):
                buckets[index]['_refilled'] += -decline
                events.append({'received_at': datetime.fromtimestamp(row['received_at'], tz).isoformat(),
                               'estimated_liters': round(-decline, 2)})
            anchor = row
        previous = row
    backfilled = 0
    for row in rows:
        index = bisect_right(edges, row['received_at']) - 1
        if 0 <= index < len(buckets):
            buckets[index]['valid_readings'] += 1
            backfilled += row['source'] == 'backfilled_current'
    total_coverage = total_consumption = total_refills = 0.0
    for i, bucket in enumerate(buckets):
        covered = bucket.pop('_covered')
        consumed = bucket.pop('_consumed')
        refilled = bucket.pop('_refilled')
        duration = edges[i + 1] - edges[i]
        bucket['coverage_percent'] = round(min(100, covered / duration * 100), 1)
        if covered:
            bucket.update(consumption_liters=round(consumed, 2), refill_liters=round(refilled, 2),
                          status='complete' if covered / duration >= .9 and edges[i + 1] <= now else 'partial')
        elif bucket['valid_readings']:
            bucket['status'] = 'insufficient_data'
        total_coverage += covered
        total_consumption += consumed
        total_refills += refilled
    return {'timezone': timezone, 'start': start.isoformat(), 'end': end.isoformat(), 'group_by': group_by,
            'consumption_liters': round(total_consumption, 2) if total_coverage else None,
            'refill_liters': round(total_refills, 2) if total_coverage else None,
            'coverage_percent': round(total_coverage / (edges[-1] - edges[0]) * 100, 1),
            'backfilled_readings': backfilled, 'buckets': buckets, 'refills': events,
            'method': {'name': 'hourly_median3_hysteresis_v1', 'aggregation_seconds': 3600, 'noise_liters': settings.consumption_noise_liters,
                       'refill_threshold_liters': settings.consumption_refill_liters,
                       'max_gap_seconds': settings.consumption_max_gap_seconds,
                       'estimated': True},
            'first_available_at': datetime.fromtimestamp(rows[0]['received_at'], tz).isoformat() if rows else None,
            'last_available_at': datetime.fromtimestamp(rows[-1]['received_at'], tz).isoformat() if rows else None}
