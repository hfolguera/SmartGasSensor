from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.consumption import boundaries, estimate
from app.models import Telemetry
from app.settings import Settings, Tank
from app.store import Store

TZ = ZoneInfo('Europe/Madrid')
SETTINGS = Settings()


def rows(volumes, start='2026-01-01T00:00:00', hours=None):
    origin = datetime.fromisoformat(start).replace(tzinfo=TZ).timestamp()
    hours = list(range(len(volumes))) if hours is None else hours
    return [{'id': i, 'received_at': origin + hour * 3600, 'volume_liters': volume,
             'calibration_key': 'calibration1', 'source': 'ingested'}
            for i, (hour, volume) in enumerate(zip(hours, volumes, strict=True))]


def report(data, start='2026-01-01', end='2026-01-02', group='day'):
    return estimate(data, date.fromisoformat(start), date.fromisoformat(end), group,
                    'Europe/Madrid', SETTINGS, now=datetime(2026, 12, 31, tzinfo=TZ).timestamp())


def test_depletion_is_estimated_only_between_confirmed_samples():
    result = report(rows([100, 90, 80, 70, 60, 50]))
    assert result['consumption_liters'] == 30
    assert result['refill_liters'] == 0
    assert result['buckets'][0]['status'] == 'partial'
    assert result['coverage_percent'] == 12.5


@pytest.mark.parametrize('volumes', [[100, 101, 99, 100, 101, 99, 100],
                                     [100, 100, 100, 800, 100, 100, 100]])
def test_noise_and_isolated_spikes_are_not_consumption_or_refills(volumes):
    result = report(rows(volumes))
    assert result['consumption_liters'] == 0
    assert result['refill_liters'] == 0


def test_refill_is_not_counted_as_consumption():
    result = report(rows([100, 90, 80, 300, 290, 280, 270]))
    assert result['consumption_liters'] == 10
    assert result['refill_liters'] == 200
    assert len(result['refills']) == 1


def test_empty_insufficient_and_future_periods_are_unknown():
    assert report([])['consumption_liters'] is None
    result = report(rows([100, 90, 80]))
    assert result['consumption_liters'] is None
    assert result['buckets'][0]['status'] == 'insufficient_data'
    assert report(rows([100, 90, 80, 70]), '2027-01-01', '2027-02-01')['consumption_liters'] is None


def test_month_boundaries_allocate_observed_decline_and_totals_match():
    data = rows([100, 90, 80, 70, 60, 50, 40], start='2026-01-31T22:00:00')
    result = report(data, '2026-01-01', '2026-03-01', 'month')
    assert [b['consumption_liters'] for b in result['buckets']] == [10, 30]
    assert result['consumption_liters'] == 40
    assert report(data, '2026-02-01', '2026-03-01', 'month')['consumption_liters'] == 30


def test_gaps_and_calibration_changes_are_not_consumption():
    result = report(rows([100, 90, 80, 70, 20, 10, 0, 0], hours=[0, 1, 2, 3, 12, 13, 14, 15]))
    assert result['consumption_liters'] == 20
    assert result['coverage_percent'] == 8.3
    data = rows([100, 90, 80, 70, 600, 590, 580, 570])
    for point in data[4:]:
        point['calibration_key'] = 'calibration2'
    result = report(data)
    assert result['consumption_liters'] == 20
    assert result['refill_liters'] == 0


def test_calendar_days_handle_dst_and_leap_year():
    for day, end, duration in [('2026-03-29', '2026-03-30', 23), ('2026-10-25', '2026-10-26', 25)]:
        edges = boundaries(date.fromisoformat(day), date.fromisoformat(end), 'day', TZ)
        assert (edges[1] - edges[0]) / 3600 == duration
    assert len(boundaries(date(2024, 2, 28), date(2024, 3, 1), 'day', TZ)) == 3


def test_complete_month_and_annual_aggregation():
    data = rows([900] * (24 * 31 + 3), start='2025-12-31T23:00:00')
    monthly = report(data, '2026-01-01', '2026-02-01', 'month')
    assert monthly['buckets'][0]['status'] == 'complete'
    assert monthly['coverage_percent'] == 100
    assert monthly['consumption_liters'] == 0
    assert report(data, '2026-01-01', '2027-01-01', 'year')['consumption_liters'] == 0


def test_small_declines_accumulate_without_counting_every_noise_rebound():
    result = report(rows([100, 99, 98, 97, 96, 95, 94, 93]))
    assert result['consumption_liters'] == 5


def tank():
    return Tank(name='Tank', length_mm=1000, width_mm=1000, height_mm=1000,
                sensor_to_bottom_mm=1050)


def test_snapshots_survive_calibration_changes_and_duplicates(tmp_path, sample):
    path = str(tmp_path / 'snapshots.db')
    store = Store(path, tanks={sample.device_id: tank()})
    store.insert(sample, 'test')
    assert store.consumption_readings(sample.device_id)[0]['volume_liters'] == 600
    store.close()
    store = Store(path, tanks={sample.device_id: tank().model_copy(update={'length_mm': 2000})})
    assert not store.insert(sample, 'test')
    old = store.consumption_readings(sample.device_id)[0]
    assert old['volume_liters'] == 600
    assert old['source'] == 'ingested'
    new = sample.model_copy(update={'sample_id': 'new'})
    store.insert(new, 'test')
    points = store.consumption_readings(sample.device_id)
    assert points[-1]['volume_liters'] == 1200
    assert points[0]['calibration_key'] != points[1]['calibration_key']
    with store.db:
        store.db.execute('DELETE FROM readings WHERE sample_id=?', ('new',))
    assert store.db.execute('SELECT COUNT(*) FROM reading_calibration').fetchone()[0] == 1
    store.close()


def test_backfill_and_exclusion_of_simulations_errors_and_outside_geometry(tmp_path, sample):
    path = str(tmp_path / 'migration.db')
    store = Store(path)
    store.insert(sample, 'test')
    store.db.executescript('DROP TABLE reading_calibration; PRAGMA user_version=4;')
    store.close()
    store = Store(path, tanks={sample.device_id: tank()})
    assert store.consumption_readings(sample.device_id)[0]['source'] == 'backfilled_current'
    for changes in [{'simulated': True}, {'measurement_status': 'no_echo', 'distance_mm': None}, {'distance_mm': 2000}]:
        store.insert(sample.model_copy(update={'sample_id': str(changes), **changes}), 'test')
    assert len(store.consumption_readings(sample.device_id)) == 1
    store.close()


def test_api_filters_validation_and_auth(tmp_path, sample_dict):
    import json

    from fastapi.testclient import TestClient

    from app.main import create_app
    config = tmp_path / 'tanks.json'
    config.write_text(json.dumps({sample_dict['device_id']: tank().model_dump()}))
    settings = Settings(db_path=str(tmp_path / 'api.db'), tank_config_path=str(config), mqtt_enabled=False,
                        consumption_since='2026-01-01T00:00:00+01:00')
    with TestClient(create_app(settings)) as client:
        for i, point in enumerate(rows([900, 890, 880, 870, 860, 850])):
            sample = Telemetry.model_validate({**sample_dict, 'sample_id': str(i), 'distance_mm': 1050 - point['volume_liters']})
            client.app.state.store.insert(sample, 'test', point['received_at'])
        url = f"/api/v1/devices/{sample_dict['device_id']}/consumption"
        result = client.get(url, params={'start': '2026-01-01', 'end': '2026-02-01'}).json()
        assert result['consumption_liters'] == 30
        assert result['previous_year']['consumption_liters'] is None
        assert client.get(url, params={'start': '2026-01-01', 'end': '2026-01-01'}).status_code == 422
        assert client.get(url, params={'start': '2026-01-01', 'end': '2026-02-01', 'timezone': '../invalid'}).status_code == 422
        assert client.get(url, params={'start': '2026-01-01', 'end': '2026-02-01', 'group_by': 'week'}).status_code == 422
    with TestClient(create_app(replace(settings, api_token='test-secret'))) as client:
        assert client.get(url, params={'start': '2026-01-01', 'end': '2026-02-01'}).status_code == 401


def test_reliable_epoch_persists_and_does_not_reuse_prototype_history(tmp_path, sample):
    path = str(tmp_path / 'epoch.db')
    store = Store(path, tanks={sample.device_id: tank()})
    epoch = store.consumption_epoch(sample.device_id)
    store.insert(sample, 'test', epoch - 86400)
    store.close()
    store = Store(path, tanks={sample.device_id: tank()})
    assert store.consumption_epoch(sample.device_id) == epoch
    assert store.consumption_readings(sample.device_id)[0]['received_at'] < epoch
    store.close()


def test_dense_short_sensor_manipulation_does_not_become_a_refill():
    data = rows([100] * 300, hours=[minute / 60 for minute in range(300)])
    for point in data[75:85]:
        point['volume_liters'] = 500
    result = report(data)
    assert result['consumption_liters'] == 0
    assert result['refill_liters'] == 0


def test_current_year_comparison_stops_at_same_local_date_time(tmp_path, sample_dict, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from app.main import create_app
    config = tmp_path / 'comparison.json'
    config.write_text(json.dumps({sample_dict['device_id']: tank().model_dump()}))
    settings = Settings(db_path=str(tmp_path / 'comparison.db'), tank_config_path=str(config),
                        mqtt_enabled=False, consumption_since='2024-01-01T00:00:00+01:00')
    with TestClient(create_app(settings)) as client:
        for year in [2025, 2026]:
            for i, point in enumerate(rows([900, 890, 880, 870, 860, 850], start=f'{year}-01-01T00:00:00')):
                sample = Telemetry.model_validate({**sample_dict, 'sample_id': f'{year}-{i}', 'distance_mm': 1050-point['volume_liters']})
                client.app.state.store.insert(sample, 'test', point['received_at'])
        monkeypatch.setattr('app.main.time.time', lambda: datetime(2026, 1, 1, 3, tzinfo=TZ).timestamp())
        result = client.get(f"/api/v1/devices/{sample_dict['device_id']}/consumption", params={
            'start': '2026-01-01', 'end': '2027-01-01'}).json()
        assert result['consumption_liters'] == 10
        assert result['previous_year']['consumption_liters'] == 10
        assert result['previous_year']['buckets'][1]['consumption_liters'] is None
