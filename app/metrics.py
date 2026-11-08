"""Prometheus text format 0.0.4; snapshots use bounded labels and no simulated data."""
import time
from collections import defaultdict

from app.calibration import calibrated_values


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def render_metrics(store, mqtt, tanks, settings):
    families = defaultdict(list)
    descriptions = {}

    def add(name, value, description, labels=None, kind='gauge'):
        if value is None:
            return
        descriptions[name] = (description, kind)
        label_text = '{' + ','.join(f'{key}="{escape(val)}"' for key, val in sorted(labels.items())) + '}' if labels else ''
        families[name].append(f'{name}{label_text} {float(value):.15g}')

    now = time.time()
    add('smartgas_mqtt_connected', mqtt['connected'], 'MQTT connection state (1 connected).')
    add('smartgas_mqtt_subscribed', mqtt['subscribed'], 'MQTT subscription state (1 subscribed).')
    for key in ('received_total', 'duplicates_total', 'rejected_total', 'storage_errors_total'):
        add('smartgas_' + key, mqtt[key], 'MQTT events since process start: ' + key + '.', kind='counter')
    monitor = store.mqtt_monitor()
    add('smartgas_mqtt_last_received_timestamp_seconds', monitor['last_received_at'], 'Last new real telemetry receipt, Unix seconds; absent until first receipt.')
    add('smartgas_mqtt_silence_seconds', max(0, now - monitor['reference_at']), 'Seconds since new real telemetry, or initial monitoring start.')
    add('smartgas_mqtt_silence_alert_active', monitor['alerted'], 'A silence alert was queued for the current interruption (1 active).')
    for device_monitor in store.valid_measurement_monitors():
        labels = {'device_id': device_monitor['device_id']}
        add('smartgas_device_last_valid_timestamp_seconds', device_monitor['last_valid_at'], 'Last correct calibrated real measurement, Unix seconds.', labels)
        add('smartgas_device_without_valid_data_seconds', max(0, now - device_monitor['reference_at']), 'Seconds without correct calibrated real data, or since monitoring began.', labels)
        add('smartgas_device_no_valid_data_alert_active', device_monitor['alerted'], 'An alert was queued for this period without correct data (1 active).', labels)
    add('smartgas_notifications_enabled', settings.ntfy_enabled, 'Notification delivery enabled (1 enabled).')
    notification_counts = store.notification_summary()
    for state in ('pending', 'sent', 'cancelled'):
        add('smartgas_notifications', notification_counts[state], 'Notifications currently retained in SQLite, by state.', {'state': state})
    for row in store.latest_all(real_only=True):
        labels = {'device_id': row['device_id']}
        age = max(0, now - row['received_at'])
        valid = row['measurement_status'] == 'ok'
        add('smartgas_device_last_received_timestamp_seconds', row['received_at'], 'Last real telemetry receipt in Unix seconds.', labels)
        add('smartgas_device_age_seconds', age, 'Age of latest real telemetry in seconds.', labels)
        add('smartgas_device_stale', age > settings.stale_after_seconds, 'Latest real telemetry is stale (1 stale).', labels)
        add('smartgas_device_measurement_valid', valid, 'Latest real measurement succeeded (1 valid).', labels)
        add('smartgas_device_valid_samples', row['valid_samples'], 'Number of valid echoes in latest measurement.', labels)
        add('smartgas_device_wifi_rssi_dbm', row['wifi_rssi_dbm'], 'Latest Wi-Fi RSSI in dBm, when supplied.', labels)
        add('smartgas_device_battery_volts', row['battery_mv'] / 1000 if row['battery_mv'] is not None else None, 'Battery voltage, when measured.', labels)
        add('smartgas_device_sample_period_seconds', row['sample_period_s'], 'Configured measurement period in seconds.', labels)
        if not valid:
            continue
        add('smartgas_device_distance_meters', row['distance_mm'] / 1000, 'Latest measured distance in meters.', labels)
        tank = tanks.get(row['device_id'])
        if tank:
            values = calibrated_values(tank, row['distance_mm'])
            add('smartgas_device_fill_percent', values['fill_percent'], 'Latest calibrated fuel level (0 to 100); check stale and measurement_valid.', labels)
            add('smartgas_device_volume_liters', values['volume_liters'], 'Latest calibrated fuel volume in liters.', labels)
    lines = []
    for name, samples in families.items():
        description, kind = descriptions[name]
        lines.extend([f'# HELP {name} {description}', f'# TYPE {name} {kind}', *samples])
    return '\n'.join(lines) + '\n'
