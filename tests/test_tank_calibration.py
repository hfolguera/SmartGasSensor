import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.calibration import calibrated_values
from app.main import create_app
from app.settings import Tank


@pytest.fixture
def installed_tank():
    return Tank(name='Dipòsit de gasoil', length_mm=1100, width_mm=800,
                height_mm=1600, sensor_to_bottom_mm=1740,
                nominal_capacity_liters=1000, reserve_liters=150)


@pytest.mark.parametrize('distance,percent,liters', [
    (140, 100, 1000), (540, 75, 750), (940, 50, 500),
    (1340, 25, 250), (1740, 0, 0),
])
def test_installed_tank_uses_nominal_capacity(installed_tank, distance, percent, liters):
    values = calibrated_values(installed_tank, distance)
    assert values['fill_percent'] == percent
    assert values['volume_liters'] == liters
    assert values['capacity_liters'] == 1000
    assert values['geometric_capacity_liters'] == 1408
    assert values['calibration_mode'] == 'geometry_nominal'
    assert values['full_distance_mm'] == 140
    assert values['empty_distance_mm'] == 1740


@pytest.mark.parametrize('distance', [139, 1741])
def test_installed_tank_rejects_distances_outside_geometry(installed_tank, distance):
    values = calibrated_values(installed_tank, distance)
    assert values['calibration_status'] == 'out_of_geometry'
    assert values['volume_liters'] is None
    assert values['fill_percent'] is None


def test_missing_measurement_is_not_zero_liters(installed_tank):
    assert calibrated_values(installed_tank, None)['volume_liters'] is None


def test_geometry_without_nominal_capacity_keeps_geometric_volume(installed_tank):
    geometric = Tank.model_validate({**installed_tank.model_dump(), 'nominal_capacity_liters': None})
    values = calibrated_values(geometric, 940)
    assert values['capacity_liters'] == 1408
    assert values['volume_liters'] == 704
    assert values['fill_percent'] == 50
    assert values['calibration_mode'] == 'geometry'


def test_incomplete_or_mixed_calibration_is_rejected(installed_tank):
    with pytest.raises(ValidationError):
        Tank(name='Missing geometry', nominal_capacity_liters=1000)
    with pytest.raises(ValidationError):
        Tank.model_validate({**installed_tank.model_dump(), 'full_distance_mm': 140, 'empty_distance_mm': 1740})


def test_active_configurations_match_installed_tank(installed_tank):
    for path in ('config/tanks.json', 'config/tanks.example.json', 'k8s/tanks.json'):
        content = json.loads(Path(path).read_text())
        assert Tank.model_validate(content['NodeMCU_ESP32']) == installed_tank


def test_nominal_capacity_agrees_between_api_metrics_and_notifications(tmp_path, sample):
    from app.notifications import NotificationPolicy
    from app.settings import Settings

    settings = Settings(db_path=str(tmp_path / 'tank.db'), tank_config_path='config/tanks.json', mqtt_enabled=False)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        store.notification_policy = NotificationPolicy(settings, client.app.state.tanks)
        store.insert(sample.model_copy(update={'sample_id': 'full', 'distance_mm': 140}), 'topic')
        store.insert(sample.model_copy(update={'sample_id': 'half', 'distance_mm': 940}), 'topic')
        latest = client.get('/api/v1/devices/NodeMCU_ESP32/latest').json()
        assert latest['volume_liters'] == 500
        assert latest['fill_percent'] == 50
        assert latest['calibration_mode'] == 'geometry_nominal'
        assert latest['name'] == 'Dipòsit de gasoil'
        assert 'smartgas_device_volume_liters{device_id="NodeMCU_ESP32"} 500\n' in client.get('/metrics').text
        for event in store.notifications():
            assert '500.0 L' in event['message']
            assert 'capacitat nominal' in event['message']
        assert len(store.notifications()) == 5
