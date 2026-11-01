import pytest

from app.models import Telemetry
from app.store import Store


@pytest.fixture
def sample_dict():
    return {"schema_version": 1, "device_id": "NodeMCU_ESP32", "sample_id": "sensor-boot-1",
            "distance_mm": 450.0, "spread_mm": 0.2, "valid_samples": 9,
            "measurement_status": "ok", "battery_mv": None, "wifi_rssi_dbm": -45,
            "awake_ms": 5400, "sample_period_s": 900, "firmware_version": "0.2.0-esp8266"}


@pytest.fixture
def sample(sample_dict):
    return Telemetry.model_validate(sample_dict)


@pytest.fixture
def store(tmp_path):
    database = Store(str(tmp_path / "data.db"))
    yield database
    database.close()
