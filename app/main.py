import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.mqtt_service import MqttService
from app.settings import Settings, load_tanks
from app.store import Store

STATIC = Path(__file__).parent / "static"


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp is not None else None


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        app.state.tanks = load_tanks(settings.tank_config_path)
        app.state.store = Store(settings.db_path, settings.retention_days)
        app.state.mqtt = MqttService(settings, app.state.store)
        app.state.mqtt.start()
        try:
            yield
        finally:
            app.state.mqtt.stop()
            app.state.store.close()

    app = FastAPI(title="SmartGasSensor API", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def authorize(authorization: str | None = Header(default=None)):
        expected = f"Bearer {settings.api_token}"
        if settings.api_token and not hmac.compare_digest((authorization or "").encode(), expected.encode()):
            raise HTTPException(401, "Cal un token API vàlid", headers={"WWW-Authenticate": "Bearer"})

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def decorate(row):
        result = {**row, "received_at": iso(row["received_at"])}
        result["age_seconds"] = max(0, round(time.time() - row["received_at"]))
        result["stale"] = result["age_seconds"] > settings.stale_after_seconds
        tank = app.state.tanks.get(row["device_id"])
        result.update(volume_liters=None, fill_percent=None, level_mm=None, calibration_status="not_configured")
        if tank:
            result.update(name=tank.name, capacity_liters=round(tank.capacity_liters, 2),
                          reserve_liters=tank.reserve_liters, calibration_status="configured")
            result["calibration_mode"] = "two_point_prototype" if tank.full_distance_mm is not None else "geometry"
            result["calibration_clamped"] = False
            if row["measurement_status"] == "ok":
                if tank.full_distance_mm is not None:
                    height = tank.empty_distance_mm - row["distance_mm"]
                    span = tank.empty_distance_mm - tank.full_distance_mm
                    fraction = max(0.0, min(1.0, height / span))
                    result.update(level_mm=round(max(0.0, min(span, height)), 1),
                                  volume_liters=round(tank.capacity_liters * fraction, 2),
                                  fill_percent=round(100 * fraction, 2),
                                  calibration_clamped=not 0 <= height <= span)
                else:
                    height = tank.sensor_to_bottom_mm - row["distance_mm"]
                    # La geometria real conserva la detecció d'errors sense saturar-la.
                    if 0 <= height <= tank.height_mm:
                        volume = tank.length_mm * tank.width_mm * height / 1_000_000
                        result.update(level_mm=round(height, 1), volume_liters=round(volume, 2),
                                      fill_percent=round(100 * height / tank.height_mm, 2))
                    else:
                        result["calibration_status"] = "out_of_geometry"
        else:
            result["name"] = row["device_id"]
        return result

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/health/live", tags=["health"])
    def live(response: Response):
        healthy = app.state.mqtt.status()["storage_errors_total"] == 0
        if not healthy:
            response.status_code = 503
        return {"status": "ok" if healthy else "storage_failure_requires_restart"}

    @app.get("/health/ready", tags=["health"])
    def ready(response: Response):
        # La web continua disponible amb les dades desades si el broker cau.
        try:
            app.state.store.healthy()
        except Exception:
            response.status_code = 503
            return {"status": "database_unavailable"}
        if app.state.mqtt.status()["storage_errors_total"]:
            response.status_code = 503
            return {"status": "storage_failure_requires_restart"}
        return {"status": "ok"}

    @app.get("/api/v1/status", dependencies=[Depends(authorize)])
    def status():
        mqtt = app.state.mqtt.status()
        mqtt["last_message_at"] = iso(mqtt["last_message_at"])
        return {"server_time": iso(time.time()), "mqtt": mqtt,
                "stale_after_seconds": settings.stale_after_seconds, "retention_days": settings.retention_days}

    @app.get("/api/v1/devices", dependencies=[Depends(authorize)])
    def devices():
        output = []
        for row in app.state.store.latest_all():
            current = decorate(row)
            valid = app.state.store.last_valid(row["device_id"])
            current["last_valid"] = decorate(valid) if valid else None
            if current["simulated"]:
                current["state"] = "simulated"
            elif current["stale"]:
                current["state"] = "stale"
            elif current["measurement_status"] != "ok":
                current["state"] = "sensor_error"
            elif current["calibration_status"] == "out_of_geometry":
                current["state"] = "calibration_error"
            elif current.get("volume_liters") is not None and current["volume_liters"] <= current["reserve_liters"]:
                current["state"] = "reserve"
            else:
                current["state"] = "ok"
            output.append(current)
        return {"devices": output}

    @app.get("/api/v1/devices/{device_id}/latest", dependencies=[Depends(authorize)])
    def latest(device_id: str):
        for row in devices()["devices"]:
            if row["device_id"] == device_id:
                return row
        raise HTTPException(404, "Dispositiu desconegut")

    @app.get("/api/v1/devices/{device_id}/history", dependencies=[Depends(authorize)])
    def history(device_id: str, hours: int = Query(default=24, ge=1, le=17520),
                limit: int = Query(default=500, ge=1, le=5000), before: float | None = Query(default=None, gt=0)):
        if device_id not in {row["device_id"] for row in app.state.store.latest_all()}:
            raise HTTPException(404, "Dispositiu desconegut")
        readings = app.state.store.history(device_id, time.time() - hours * 3600, limit, before)
        return {"readings": [decorate(row) for row in readings], "limit": limit,
                "next_before": readings[0]["received_at"] if len(readings) == limit else None}

    @app.get("/api/v1/metrics", dependencies=[Depends(authorize)], response_class=PlainTextResponse)
    def metrics():
        mqtt = app.state.mqtt.status()
        lines = ["# HELP smartgas_mqtt_connected MQTT connection state", "# TYPE smartgas_mqtt_connected gauge",
                 f"smartgas_mqtt_connected {int(mqtt['connected'])}"]
        for key in ("received_total", "duplicates_total", "rejected_total", "storage_errors_total"):
            lines.extend([f"# TYPE smartgas_{key} counter", f"smartgas_{key} {mqtt[key]}"])
        return "\n".join(lines) + "\n"

    return app


app = create_app()
