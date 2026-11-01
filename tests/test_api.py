import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Telemetry
from app.settings import Settings


@pytest.fixture
def settings(tmp_path):
    tanks = tmp_path / "tanks.json"
    tanks.write_text(json.dumps({"NodeMCU_ESP32": {"name": "Dipòsit", "length_mm": 1000,
                     "width_mm": 1000, "height_mm": 1000, "sensor_to_bottom_mm": 1050,
                     "reserve_liters": 150}}))
    return Settings(db_path=str(tmp_path / "api.db"), tank_config_path=str(tanks), mqtt_enabled=False)


def test_volume_and_api_contract(settings, sample):
    with TestClient(create_app(settings)) as client:
        client.app.state.store.insert(sample, "home/gas/telemetry")
        response = client.get("/api/v1/devices/NodeMCU_ESP32/latest")
        assert response.status_code == 200
        body = response.json()
        assert body["volume_liters"] == 600
        assert body["fill_percent"] == 60
        assert body["state"] == "ok"
        assert body["last_valid"]["distance_mm"] == 450
        assert client.get("/api/v1/devices/NodeMCU_ESP32/history").json()["readings"][0]["volume_liters"] == 600
        assert client.get("/api/v1/devices/unknown/latest").status_code == 404
        assert client.get("/api/v1/devices/NodeMCU_ESP32/history?limit=999999").status_code == 422


def test_stale_and_error_keep_last_valid(settings, sample, sample_dict):
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        store.insert(sample, "home/gas/telemetry", time.time() - 4000)
        assert client.get("/api/v1/devices").json()["devices"][0]["state"] == "stale"
        sample_dict.update(sample_id="error-2", distance_mm=None, measurement_status="unstable")
        store.insert(Telemetry.model_validate(sample_dict), "home/gas/telemetry")
        body = client.get("/api/v1/devices").json()["devices"][0]
        assert body["state"] == "sensor_error"
        assert body["volume_liters"] is None
        assert body["last_valid"]["volume_liters"] == 600
        assert body["last_valid"]["stale"] is True


def test_simulation_does_not_overwrite_last_real_valid(settings, sample, sample_dict):
    with TestClient(create_app(settings)) as client:
        client.app.state.store.insert(sample, "topic")
        sample_dict.update(sample_id="demo-2", simulated=True, distance_mm=100.0)
        client.app.state.store.insert(Telemetry.model_validate(sample_dict), "topic")
        body = client.get("/api/v1/devices").json()["devices"][0]
        assert body["state"] == "simulated"
        assert body["last_valid"]["distance_mm"] == 450


def test_outside_geometry_is_not_clamped_to_zero(settings, sample_dict):
    sample_dict["distance_mm"] = 1200.0
    with TestClient(create_app(settings)) as client:
        client.app.state.store.insert(Telemetry.model_validate(sample_dict), "topic")
        body = client.get("/api/v1/devices").json()["devices"][0]
        assert body["volume_liters"] is None
        assert body["state"] == "calibration_error"


def test_no_fake_calibration(settings, sample, tmp_path):
    Path = type(tmp_path)
    Path(settings.tank_config_path).write_text("{}")
    with TestClient(create_app(settings)) as client:
        client.app.state.store.insert(sample, "topic")
        body = client.get("/api/v1/devices").json()["devices"][0]
        assert body["calibration_status"] == "not_configured"
        assert body["volume_liters"] is None


def test_auth_and_health_without_mqtt(settings, sample):
    with TestClient(create_app(replace(settings, api_token="secret-token"))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/devices").status_code == 401
        assert client.get("/api/v1/devices", headers={"Authorization": "Bearer secret-token"}).status_code == 200
        client.app.state.mqtt.storage_errors = 1
        assert client.get("/health/live").status_code == 503


def test_history_ordering(settings, sample_dict):
    with TestClient(create_app(settings)) as client:
        now = time.time()
        for i in range(5):
            sample_dict["sample_id"] = f"sample-{i}"
            client.app.state.store.insert(Telemetry.model_validate(sample_dict), "topic", now - 100 + i)
        result = client.get("/api/v1/devices/NodeMCU_ESP32/history?limit=2").json()
        assert [r["sample_id"] for r in result["readings"]] == ["sample-3", "sample-4"]
        assert result["next_before"] is not None


@pytest.mark.parametrize('distance,percent,liters,clamped', [
    (50.0, 100, 1000, False),
    (150.0, 50, 500, False),
    (250.0, 0, 0, False),
    (40.0, 100, 1000, True),
    (260.0, 0, 0, True),
])
def test_prototype_two_point_calibration(settings, sample_dict, distance, percent, liters, clamped):
    from pathlib import Path
    Path(settings.tank_config_path).write_text(json.dumps({'NodeMCU_ESP32': {
        'name': 'Prototip', 'full_distance_mm': 50, 'empty_distance_mm': 250,
        'nominal_capacity_liters': 1000, 'reserve_liters': 150,
    }}))
    sample_dict['distance_mm'] = distance
    with TestClient(create_app(settings)) as client:
        client.app.state.store.insert(Telemetry.model_validate(sample_dict), 'topic')
        body = client.get('/api/v1/devices/NodeMCU_ESP32/latest').json()
        assert body['fill_percent'] == percent
        assert body['volume_liters'] == liters
        assert body['calibration_clamped'] is clamped
        assert body['calibration_mode'] == 'two_point_prototype'
