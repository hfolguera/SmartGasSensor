import io
import json
import time
import urllib.error
from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.models import Telemetry
from app.notifications import NotificationPolicy, NotificationSender
from app.settings import Settings, Tank
from app.store import Store


@pytest.fixture
def notification_setup(tmp_path, sample_dict):
    settings = Settings(ntfy_enabled=True)
    tanks = {sample_dict['device_id']: Tank(name='Dipòsit de prova', full_distance_mm=50,
             empty_distance_mm=250, nominal_capacity_liters=1000, reserve_liters=150)}
    policy = NotificationPolicy(settings, tanks)
    store = Store(str(tmp_path / 'notifications.db'), notification_policy=policy)
    counter = 0

    def reading(percent, **changes):
        nonlocal counter
        counter += 1
        data = {**sample_dict, 'sample_id': f'notification-test-{counter}',
                'distance_mm': 250.0 - 2 * percent, **changes}
        sample = Telemetry.model_validate(data)
        store.insert(sample, 'home/gas/telemetry')
        return sample

    yield settings, tanks, store, reading
    store.close()


def test_first_measurement_sets_baseline_without_retroactive_spam(notification_setup):
    _, _, store, reading = notification_setup
    reading(8)
    assert store.notification_summary()['pending'] == 0


def test_each_crossed_threshold_once_and_duplicates_do_not_repeat(notification_setup):
    _, _, store, reading = notification_setup
    reading(100)
    sample = reading(68)
    assert [event['threshold_percent'] for event in reversed(store.notifications())] == [90, 80, 70]
    assert not store.insert(sample, 'home/gas/telemetry')
    reading(67)
    reading(69)
    reading(68)
    assert store.notification_summary()['pending'] == 3


def test_hysteresis_rearms_only_after_five_point_rise(notification_setup):
    _, _, store, reading = notification_setup
    reading(100)
    reading(89)
    reading(91)
    reading(89)
    assert len(store.notifications()) == 1
    reading(95)
    assert store.notification_summary()['cancelled'] == 1
    reading(89)
    assert len(store.notifications()) == 2
    assert store.notification_summary()['pending'] == 1


def test_critical_priorities_and_direct_link(notification_setup):
    settings, _, store, reading = notification_setup
    reading(25)
    reading(0)
    events = {event['threshold_percent']: event for event in store.notifications()}
    assert events[20]['priority'] == 4
    assert events[10]['priority'] == 5
    assert events[0]['priority'] == 5
    for event in events.values():
        assert event['topic'] == 'home-notifications'
        assert event['click'] == settings.public_web_url
        assert event['actions'][0]['url'] == settings.public_web_url
        assert settings.public_web_url in event['message']
        assert 'provisional' in event['message']


def test_invalid_and_simulated_measurements_do_not_create_alerts(notification_setup):
    _, _, store, reading = notification_setup
    reading(100)
    reading(15, simulated=True)
    reading(10, measurement_status='no_echo', distance_mm=None, valid_samples=0)
    assert not store.notifications()
    reading(89)
    assert [event['threshold_percent'] for event in store.notifications()] == [90]


def test_state_and_outbox_survive_restart(notification_setup):
    settings, tanks, store, reading = notification_setup
    reading(100)
    sample = reading(89)
    reopened = Store(str(store.db.execute('PRAGMA database_list').fetchone()['file']),
                     notification_policy=NotificationPolicy(settings, tanks))
    assert reopened.notification_summary()['pending'] == 1
    reopened.insert(sample.model_copy(update={'sample_id': 'after-restart', 'distance_mm': 74.0}), 'topic')
    assert reopened.notification_summary()['pending'] == 1
    reopened.close()


def test_calibration_change_cancels_obsolete_pending_alerts(notification_setup):
    _, tanks, store, reading = notification_setup
    reading(100)
    reading(89)
    tanks['NodeMCU_ESP32'] = tanks['NodeMCU_ESP32'].model_copy(update={'empty_distance_mm': 300.0})
    reading(70)
    assert store.notification_summary()['pending'] == 0
    assert store.notification_summary()['cancelled'] == 1


def test_reading_and_outbox_are_atomic(notification_setup):
    _, _, store, reading = notification_setup
    reading(100)
    real_policy = store.notification_policy

    def fail_after_plan(db, sample, now):
        import sqlite3
        real_policy.plan(db, sample, now)
        raise sqlite3.OperationalError('simulated commit failure')

    store.notification_policy = Mock(plan=fail_after_plan)
    with pytest.raises(Exception, match='simulated commit failure'):
        reading(89)
    assert store.notification_summary()['pending'] == 0
    assert len(store.history('NodeMCU_ESP32', 0)) == 1


def test_sender_posts_utf8_json_with_click_action_and_auth(notification_setup):
    settings, _, store, reading = notification_setup
    reading(100)
    reading(89)
    sender = NotificationSender(replace(settings, ntfy_token='test-token'), store)
    response = io.BytesIO(json.dumps({'id': 'ntfy-test-id', 'event': 'message', 'topic': 'home-notifications'}).encode())
    sender.opener = Mock()
    sender.opener.open.return_value = response
    assert sender.deliver_one()
    request = sender.opener.open.call_args.args[0]
    body = json.loads(request.data)
    assert request.full_url == settings.ntfy_server_url + '/'
    assert request.get_header('Authorization') == 'Bearer test-token'
    assert body['click'] == settings.public_web_url
    assert body['priority'] == 3
    assert store.notification_summary()['sent'] == 1
    assert not sender.deliver_one()


def test_failed_delivery_is_persisted_and_retried(notification_setup):
    settings, _, store, reading = notification_setup
    reading(100)
    reading(89)
    sender = NotificationSender(settings, store)
    sender.opener = Mock()
    sender.opener.open.side_effect = urllib.error.HTTPError('https://test.invalid', 503, 'unavailable', {}, None)
    assert sender.deliver_one()
    event = store.notifications()[0]
    assert event['status'] == 'pending'
    assert event['attempts'] == 1
    assert event['last_error'] == 'HTTP 503'
    assert store.next_notification() is None
    with store.lock, store.db:
        store.db.execute('UPDATE notification_outbox SET next_attempt_at=?', (time.time() - 1,))
    sender.opener.open.side_effect = None
    sender.opener.open.return_value = io.BytesIO(b'{"id":"retry-id","event":"message","topic":"home-notifications"}')
    assert sender.deliver_one()
    assert store.notifications()[0]['status'] == 'sent'
    assert store.notifications()[0]['attempts'] == 2


def test_http_transport_delivers_to_local_ntfy_stub(notification_setup):
    import http.server
    import threading

    settings, _, store, reading = notification_setup
    reading(100)
    reading(89)
    captured = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = b'{"id":"stub-id","event":"message","topic":"home-notifications"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sender = NotificationSender(replace(settings, ntfy_server_url=f'http://127.0.0.1:{server.server_port}'), store)
        assert sender.deliver_one()
        assert captured[0]['topic'] == 'home-notifications'
        assert captured[0]['priority'] == 3
        assert captured[0]['click'] == settings.public_web_url
        assert 'Dipòsit' in captured[0]['message']
        assert store.notification_summary()['sent'] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_v1_database_upgrades_without_losing_readings(tmp_path, sample):
    path = str(tmp_path / 'v1.db')
    old = Store(path)
    old.insert(sample, 'topic')
    old.db.executescript('DROP TABLE notification_state; DROP TABLE notification_outbox; PRAGMA user_version=1;')
    old.close()
    upgraded = Store(path)
    assert upgraded.latest_all()[0]['sample_id'] == sample.sample_id
    assert upgraded.db.execute('PRAGMA user_version').fetchone()[0] == 4
    assert upgraded.notification_summary()['pending'] == 0
    upgraded.close()


def test_unexpected_ntfy_json_does_not_crash_worker(notification_setup):
    settings, _, store, reading = notification_setup
    reading(100)
    reading(89)
    sender = NotificationSender(settings, store)
    sender.opener = Mock()
    sender.opener.open.return_value = io.BytesIO(b'[]')
    assert sender.deliver_one()
    assert store.notification_summary()['pending'] == 1
    assert store.notification_summary()['sent'] == 0


def test_silence_alert_after_24h_once_and_restart(tmp_path):
    path = str(tmp_path / 'silence.db')
    settings = Settings(ntfy_enabled=True)
    store = Store(path)
    start = store.mqtt_monitor()['reference_at']
    assert not store.check_mqtt_silence(settings, start + 86400)
    store.close()
    store = Store(path)
    assert store.check_mqtt_silence(settings, start + 86401)
    assert not store.check_mqtt_silence(settings, start + 172800)
    event = store.notifications()[0]
    assert event['type'] == 'mqtt_silence'
    assert event['threshold_percent'] is None
    assert event['priority'] == 4
    assert event['click'] == settings.public_web_url
    assert event['topic'] == 'home-notifications'
    assert 'Encara no' in event['message']
    store.close()
    store = Store(path)
    assert not store.check_mqtt_silence(settings, start + 259200)
    assert len(store.notifications()) == 1
    store.close()


def test_real_error_reading_rearms_silence_but_simulation_and_duplicates_do_not(tmp_path, sample):
    settings = Settings(ntfy_enabled=True)
    store = Store(str(tmp_path / 'silence.db'))
    start = store.mqtt_monitor()['reference_at']
    store.insert(sample, 'topic', start)
    assert store.check_mqtt_silence(settings, start + 86401)
    store.insert(sample, 'topic', start + 86402)
    store.insert(sample.model_copy(update={'sample_id': 'simulation', 'simulated': True}), 'topic', start + 86403)
    assert store.mqtt_monitor()['last_received_at'] == start
    assert store.notification_summary()['pending'] == 1
    error = sample.model_copy(update={'sample_id': 'new-error', 'measurement_status': 'no_echo', 'distance_mm': None})
    store.insert(error, 'topic', start + 86404)
    assert store.notification_summary()['cancelled'] == 1
    assert not store.check_mqtt_silence(settings, start + 86405)
    assert store.check_mqtt_silence(settings, start + 172805)
    assert len(store.notifications()) == 2
    store.close()


def test_silence_monitor_migration_uses_last_real_receipt(tmp_path, sample):
    path = str(tmp_path / 'v2.db')
    store = Store(path)
    store.insert(sample, 'topic', time.time() - 90000)
    store.db.executescript('DROP TABLE mqtt_monitor; PRAGMA user_version=2;')
    store.close()
    store = Store(path)
    assert store.check_mqtt_silence(Settings(ntfy_enabled=True))
    assert 'Encara no' not in store.notifications()[0]['message']
    assert store.latest_all()[0]['sample_id'] == sample.sample_id
    store.close()


@pytest.mark.parametrize('settings', [Settings(ntfy_enabled=False), Settings(ntfy_enabled=True, mqtt_enabled=False)])
def test_silence_alert_disabled_with_no_mqtt_or_no_ntfy(tmp_path, settings):
    store = Store(str(tmp_path / 'disabled.db'))
    assert not store.check_mqtt_silence(settings, time.time() + 90000)
    assert not store.notifications()
    store.close()


def test_silence_worker_runs_without_incoming_messages(tmp_path, monkeypatch):
    import threading
    settings = Settings(ntfy_enabled=True)
    store = Store(str(tmp_path / 'worker.db'))
    with store.db:
        store.db.execute('UPDATE mqtt_monitor SET reference_at=?', (time.time() - 90000,))
    sender = NotificationSender(settings, store)
    queued = threading.Event()

    def capture():
        assert store.next_notification()['threshold_percent'] == -1
        queued.set()
        sender.stopped.set()
        return True

    monkeypatch.setattr(sender, 'deliver_one', capture)
    sender.start()
    assert queued.wait(2)
    sender.stop()
    store.close()


def test_valid_data_monitor_errors_recovery_restart(notification_setup):
    settings, tanks, store, reading = notification_setup
    reading(60)
    start = store.valid_measurement_monitors()[0]['last_valid_at']
    assert not store.check_valid_measurements(settings, start + 86400)
    reading(60, measurement_status='no_echo', distance_mm=None)
    reading(60, distance_mm=10000)  # status ok but unusable calibration
    reading(60, simulated=True)
    assert store.valid_measurement_monitors()[0]['reference_at'] == start
    assert store.check_valid_measurements(settings, start + 86401)
    assert not store.check_valid_measurements(settings, start + 172800)
    event = store.notifications()[0]
    assert event['type'] == 'no_valid_data'
    assert event['threshold_percent'] is None
    assert event['priority'] == 4
    assert event['click'] == settings.public_web_url
    path = store.db.execute('PRAGMA database_list').fetchone()['file']
    reopened = Store(path, tanks=tanks)
    assert not reopened.check_valid_measurements(settings, start + 172800)
    reopened.close()
    reading(60)
    assert store.notifications()[0]['status'] == 'cancelled'
    assert store.valid_measurement_monitors()[0]['alerted'] == 0


def test_valid_data_monitor_migrates_history_and_ignores_duplicates(tmp_path, sample_dict):
    path = str(tmp_path / 'upgrade.db')
    tank = Tank(name='Tank', full_distance_mm=50, empty_distance_mm=250, nominal_capacity_liters=1000)
    store = Store(path)
    good = Telemetry.model_validate({**sample_dict, 'sample_id': 'good', 'distance_mm': 150})
    store.insert(good, 'test', received_at=time.time() - 90000)
    store.insert(Telemetry.model_validate({**sample_dict, 'sample_id': 'bad', 'measurement_status': 'no_echo', 'distance_mm': None}), 'test')
    store.db.execute('DROP TABLE valid_measurement_monitor')
    store.close()
    store = Store(path, tanks={good.device_id: tank})
    settings = Settings(ntfy_enabled=True)
    assert not store.insert(good, 'test')
    assert store.check_valid_measurements(settings)
    assert store.valid_measurement_monitors()[0]['last_valid_at'] < time.time() - 86400
    store.close()


def test_valid_data_monitor_no_history_and_disabled(tmp_path):
    settings = Settings(ntfy_enabled=True)
    tank = Tank(name='Tank', full_distance_mm=50, empty_distance_mm=250, nominal_capacity_liters=1000)
    store = Store(str(tmp_path / 'empty.db'), tanks={'sensor': tank})
    start = store.valid_measurement_monitors()[0]['reference_at']
    assert not store.check_valid_measurements(replace(settings, mqtt_enabled=False), start + 90000)
    assert not store.check_valid_measurements(replace(settings, ntfy_enabled=False), start + 90000)
    assert store.check_valid_measurements(settings, start + 90000)
    store.close()


def test_valid_data_monitors_are_per_device(tmp_path, sample_dict):
    tank = Tank(name='Tank', full_distance_mm=50, empty_distance_mm=250, nominal_capacity_liters=1000)
    store = Store(str(tmp_path / 'devices.db'), tanks={'one': tank, 'two': tank})
    settings = Settings(ntfy_enabled=True)
    now = time.time()
    for device in ('one', 'two'):
        store.insert(Telemetry.model_validate({**sample_dict, 'device_id': device, 'sample_id': device, 'distance_mm': 150}), 'test', now - 90000)
    store.insert(Telemetry.model_validate({**sample_dict, 'device_id': 'two', 'sample_id': 'fresh', 'distance_mm': 150}), 'test', now)
    assert store.check_valid_measurements(settings, now)
    events = [e for e in store.notifications() if e['type'] == 'no_valid_data']
    assert [e['device_id'] for e in events] == ['one']
    store.close()
