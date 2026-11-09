import calendar
import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.calibration import calibrated_values
from app.consumption import estimate
from app.metrics import render_metrics
from app.mqtt_service import MqttService
from app.notifications import NotificationPolicy, NotificationSender
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
        policy = NotificationPolicy(settings, app.state.tanks) if settings.ntfy_enabled else None
        app.state.store = Store(settings.db_path, settings.retention_days, policy, app.state.tanks)
        app.state.notifications = NotificationSender(settings, app.state.store)
        app.state.notifications.start()
        app.state.mqtt = MqttService(settings, app.state.store)
        app.state.mqtt.start()
        try:
            yield
        finally:
            app.state.mqtt.stop()
            app.state.notifications.stop()
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
        elif request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    def decorate(row):
        result = {**row, "received_at": iso(row["received_at"])}
        result["age_seconds"] = max(0, round(time.time() - row["received_at"]))
        result["stale"] = result["age_seconds"] > settings.stale_after_seconds
        tank = app.state.tanks.get(row["device_id"])
        result.update(volume_liters=None, fill_percent=None, level_mm=None, calibration_status="not_configured")
        if tank:
            result.update(calibrated_values(tank, row["distance_mm"] if row["measurement_status"] == "ok" else None))
        else:
            result["name"] = row["device_id"]
        return result

    @app.get("/", include_in_schema=False)
    def home():
        page = (STATIC / "index.html").read_text()
        for asset in ('app.js', 'styles.css'):
            version = hashlib.sha256((STATIC / asset).read_bytes()).hexdigest()[:12]
            page = page.replace(f'/static/{asset}', f'/static/{asset}?v={version}')
        return HTMLResponse(page)

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
                "stale_after_seconds": settings.stale_after_seconds, "retention_days": settings.retention_days,
                "valid_measurement_monitors": app.state.store.valid_measurement_monitors(),
                "mqtt_monitor": {**app.state.store.mqtt_monitor(), "silence_threshold_seconds": 86400},
                "notifications": {"enabled": settings.ntfy_enabled, **app.state.store.notification_summary()}}

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
    def history(device_id: str, hours: int = Query(default=24, ge=1, le=87600),
                limit: int = Query(default=500, ge=1, le=5000), before: float | None = Query(default=None, gt=0)):
        if device_id not in {row["device_id"] for row in app.state.store.latest_all()}:
            raise HTTPException(404, "Dispositiu desconegut")
        readings = app.state.store.history(device_id, time.time() - hours * 3600, limit, before)
        return {"readings": [decorate(row) for row in readings], "limit": limit,
                "next_before": readings[0]["received_at"] if len(readings) == limit else None}

    @app.get("/api/v1/devices/{device_id}/consumption", dependencies=[Depends(authorize)])
    def consumption(device_id: str, start: date, end: date,
                    group_by: Literal['day', 'month', 'year'] = 'month', timezone: str = 'Europe/Madrid',
                    compare_previous_year: bool = True):
        if start.year < 1971 or end.year > 2101 or end <= start or (end - start).days > 3660:
            raise HTTPException(422, "Indica un interval vàlid de fins a 10 anys; la data final és exclusiva")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "Zona horària desconeguda") from None
        if device_id not in app.state.tanks:
            raise HTTPException(404, "Dipòsit desconegut o sense calibratge")
        all_rows = app.state.store.consumption_readings(device_id)
        since = (datetime.fromisoformat(settings.consumption_since).timestamp() if settings.consumption_since
                 else app.state.store.consumption_epoch(device_id))
        rows = [row for row in all_rows if row['received_at'] >= since]
        now = time.time()
        result = {'device_id': device_id, **estimate(rows, start, end, group_by, timezone, settings, now)}
        result['reliable_since'] = iso(since)
        result['historical_readings_excluded'] = len(all_rows) - len(rows)
        if compare_previous_year:
            def previous_year(value):
                return value.replace(year=value.year - 1,
                                     day=min(value.day, calendar.monthrange(value.year - 1, value.month)[1]))
            # If this period is still running, cap the previous year at the same
            # local calendar date/time, rather than comparing against a full year.
            local_now = datetime.fromtimestamp(now, ZoneInfo(timezone))
            comparison_now = previous_year(local_now).timestamp() if end > local_now.date() else now
            result['previous_year'] = estimate(rows, previous_year(start), previous_year(end),
                                              group_by, timezone, settings, comparison_now)
        return result

    @app.get("/api/v1/notifications", dependencies=[Depends(authorize)])
    def notifications(limit: int = Query(default=20, ge=1, le=100)):
        events = app.state.store.notifications(limit)
        for event in events:
            event["created_at"] = iso(event["created_at"])
            event["sent_at"] = iso(event["sent_at"])
        return {"enabled": settings.ntfy_enabled, "topic": settings.ntfy_topic,
                "web_url": settings.public_web_url, "hysteresis_percent": settings.ntfy_hysteresis_percent,
                **app.state.store.notification_summary(), "events": events}

    @app.get("/metrics", dependencies=[Depends(authorize)], include_in_schema=False)
    @app.get("/api/v1/metrics", dependencies=[Depends(authorize)])
    def metrics():
        return Response(render_metrics(app.state.store, app.state.mqtt.status(), app.state.tanks, settings),
                        media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()
