import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Tank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    length_mm: float | None = Field(default=None, gt=0, le=100000)
    width_mm: float | None = Field(default=None, gt=0, le=100000)
    height_mm: float | None = Field(default=None, gt=0, le=100000)
    sensor_to_bottom_mm: float | None = Field(default=None, gt=0, le=100000)
    full_distance_mm: float | None = Field(default=None, ge=0, le=100000)
    empty_distance_mm: float | None = Field(default=None, gt=0, le=100000)
    nominal_capacity_liters: float | None = Field(default=None, gt=0)
    reserve_liters: float = Field(default=150, ge=0)

    @model_validator(mode="after")
    def validate_calibration(self):
        geometry = (self.length_mm, self.width_mm, self.height_mm, self.sensor_to_bottom_mm)
        endpoints = (self.full_distance_mm, self.empty_distance_mm)
        if any(value is not None for value in endpoints):
            if not all(value is not None for value in endpoints) or self.nominal_capacity_liters is None or any(value is not None for value in geometry):
                raise ValueError("El calibratge de dos punts requereix els dos extrems i capacitat nominal, sense geometria")
            if self.empty_distance_mm <= self.full_distance_mm:
                raise ValueError("La distància de buit ha de superar la de ple")
        else:
            if not all(value is not None for value in geometry):
                raise ValueError("Cal indicar totes les dimensions o un calibratge de dos punts")
            if self.sensor_to_bottom_mm < self.height_mm:
                raise ValueError("sensor_to_bottom_mm ha de ser >= height_mm")
        if self.reserve_liters > self.capacity_liters:
            raise ValueError("La reserva supera la capacitat calculada")
        return self

    @property
    def capacity_liters(self):
        if self.nominal_capacity_liters is not None:
            return self.nominal_capacity_liters
        return self.length_mm * self.width_mm * self.height_mm / 1_000_000


def flag(name, default=False):
    return os.getenv(name, str(default)).lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    db_path: str = "./data/smartgas.db"
    mqtt_enabled: bool = True
    mqtt_host: str = "192.168.1.216"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic: str = "home/gas/telemetry"
    mqtt_client_id: str = "smartgas-production"
    mqtt_tls: bool = False
    mqtt_ca_file: str = ""
    tank_config_path: str = "config/tanks.json"
    stale_after_seconds: int = 3600
    retention_days: int = 730
    api_token: str = ""
    ntfy_enabled: bool = False
    ntfy_server_url: str = "https://ntfy.calfolguera.freemyip.com"
    ntfy_topic: str = "home-notifications"
    ntfy_token: str = ""
    ntfy_username: str = ""
    ntfy_password: str = ""
    ntfy_hysteresis_percent: float = 5.0
    public_web_url: str = "https://smartgassensor.calfolguera.freemyip.com"

    @classmethod
    def from_env(cls):
        settings = cls(
            db_path=os.getenv("DB_PATH", "./data/smartgas.db"),
            mqtt_enabled=flag("MQTT_ENABLED", True),
            mqtt_host=os.getenv("MQTT_HOST", "192.168.1.216"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_username=os.getenv("MQTT_USERNAME", ""),
            mqtt_password=os.getenv("MQTT_PASSWORD", ""),
            mqtt_topic=os.getenv("MQTT_TOPIC", "home/gas/telemetry"),
            mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "smartgas-production"),
            mqtt_tls=flag("MQTT_TLS"),
            mqtt_ca_file=os.getenv("MQTT_CA_FILE", ""),
            tank_config_path=os.getenv("TANK_CONFIG_PATH", "config/tanks.json"),
            stale_after_seconds=int(os.getenv("STALE_AFTER_SECONDS", "3600")),
            retention_days=int(os.getenv("RETENTION_DAYS", "730")),
            api_token=os.getenv("API_TOKEN", ""),
            ntfy_enabled=flag("NTFY_ENABLED"),
            ntfy_server_url=os.getenv("NTFY_SERVER_URL", "https://ntfy.calfolguera.freemyip.com"),
            ntfy_topic=os.getenv("NTFY_TOPIC", "home-notifications"),
            ntfy_token=os.getenv("NTFY_TOKEN", ""),
            ntfy_username=os.getenv("NTFY_USERNAME", ""),
            ntfy_password=os.getenv("NTFY_PASSWORD", ""),
            ntfy_hysteresis_percent=float(os.getenv("NTFY_HYSTERESIS_PERCENT", "5")),
            public_web_url=os.getenv("PUBLIC_WEB_URL", "https://smartgassensor.calfolguera.freemyip.com"),
        )
        if not 1 <= settings.mqtt_port <= 65535:
            raise ValueError("MQTT_PORT fora de rang")
        if settings.stale_after_seconds < 60 or settings.retention_days < 1:
            raise ValueError("Intervals de configuració invàlids")
        if not settings.mqtt_client_id or not settings.mqtt_topic:
            raise ValueError("Cal MQTT_CLIENT_ID i MQTT_TOPIC")
        for url in (settings.ntfy_server_url, settings.public_web_url):
            parsed = urlparse(url)
            if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("URL pública/ntfy invàlida; les credencials van en variables separades")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", settings.ntfy_topic):
            raise ValueError("NTFY_TOPIC invàlid")
        if not 0 < settings.ntfy_hysteresis_percent <= 10:
            raise ValueError("NTFY_HYSTERESIS_PERCENT ha d'estar entre 0 i 10")
        return settings


def load_tanks(path: str) -> dict[str, Tank]:
    # Fitxer absent = error de configuració, no fingir que està calibrat.
    content = json.loads(Path(path).read_text())
    if not isinstance(content, dict):
        raise ValueError("tanks.json ha de ser un objecte per device_id")
    tanks = {key: Tank.model_validate(value) for key, value in content.items()}
    return tanks
