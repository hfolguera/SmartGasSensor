import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.models import Telemetry
from app.mqtt_service import MqttService
from app.settings import Settings


def message(payload):
    return SimpleNamespace(payload=payload, topic="home/gas/telemetry", qos=1, mid=42)


def test_ack_only_after_durable_insert(store, sample_dict):
    service = MqttService(Settings(mqtt_enabled=False), store)
    client = Mock()
    client.ack.side_effect = lambda mid, qos: len(store.latest_all()) == 1 or pytest.fail("ACK prematur")
    service.on_message(client, None, message(json.dumps(sample_dict).encode()))
    client.ack.assert_called_once_with(42, 1)
    assert store.latest_all()[0]["distance_mm"] == 450


def test_duplicate_does_not_refresh_age(store, sample):
    assert store.insert(sample, "home/gas/telemetry", received_at=100)
    assert not store.insert(sample, "home/gas/telemetry", received_at=500)
    assert store.latest_all()[0]["received_at"] == 100


def test_storage_failure_does_not_ack(store, sample_dict):
    service = MqttService(Settings(mqtt_enabled=False), store)
    store.insert = Mock(side_effect=sqlite3.OperationalError("disk full"))
    client = Mock()
    service.on_message(client, None, message(json.dumps(sample_dict).encode()))
    client.ack.assert_not_called()
    client.disconnect.assert_called_once()
    assert service.status()["storage_errors_total"] == 1


@pytest.mark.parametrize("payload", [b"{test: test1}", b"[]", b"{}", b"x" * 17000])
def test_bad_messages_discarded_without_killing_consumer(store, payload):
    service = MqttService(Settings(mqtt_enabled=False), store)
    client = Mock()
    service.on_message(client, None, message(payload))
    client.ack.assert_called_once_with(42, 1)
    assert store.latest_all() == []
    assert service.status()["rejected_total"] == 1


def test_sensor_error_is_not_empty_tank(store, sample_dict):
    sample_dict.update(distance_mm=None, measurement_status="no_echo", valid_samples=0)
    store.insert(Telemetry.model_validate(sample_dict), "home/gas/telemetry")
    assert store.latest_all()[0]["distance_mm"] is None


@pytest.mark.parametrize("change", [{"distance_mm": float("nan")}, {"distance_mm": None},
                                    {"schema_version": 2}, {"valid_samples": -1},
                                    {"measurement_status": "unstable"}, {"device_id": "<script>"}])
def test_reject_invalid_sensor_contract(sample_dict, change):
    with pytest.raises(ValidationError):
        Telemetry.model_validate({**sample_dict, **change})


def test_database_survives_restart(tmp_path, sample):
    from app.store import Store
    path = str(tmp_path / "persist.db")
    first = Store(path)
    first.insert(sample, "home/gas/telemetry")
    first.close()
    second = Store(path)
    assert len(second.latest_all()) == 1
    assert not second.insert(sample, "home/gas/telemetry")
    second.close()
