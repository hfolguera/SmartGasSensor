from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False, strict=True)
    schema_version: Literal[1]
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    sample_id: str = Field(min_length=1, max_length=240)
    distance_mm: float | None = Field(default=None, ge=0, le=100000)
    spread_mm: float | None = Field(default=None, ge=0, le=100000)
    valid_samples: int = Field(ge=0, le=10000)
    measurement_status: Literal["ok", "no_echo", "out_of_range", "unstable", "insufficient_samples"]
    battery_mv: int | None = Field(default=None, ge=0, le=20000)
    wifi_rssi_dbm: int | None = Field(default=None, ge=-150, le=0)
    awake_ms: int = Field(ge=0)
    sample_period_s: int = Field(ge=1, le=604800)
    firmware_version: str = Field(min_length=1, max_length=100)
    simulated: bool = False

    @model_validator(mode="after")
    def valid_measurement(self):
        if self.measurement_status == "ok" and (self.distance_mm is None or self.valid_samples == 0):
            raise ValueError("Una mesura ok requereix distància i mostres vàlides")
        if self.measurement_status != "ok" and self.distance_mm is not None:
            raise ValueError("Una mesura amb error ha de tenir distance_mm=null")
        return self
