import base64
import hashlib
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from app.calibration import calibrated_values

log = logging.getLogger("smartgas.notifications")
THRESHOLDS = tuple(range(90, -1, -10))


class NotificationPolicy:
    """Planifica dins de la mateixa transacció SQLite que desa la lectura."""

    def __init__(self, settings, tanks):
        self.settings = settings
        self.tanks = tanks

    def plan(self, db, sample, now):
        if sample.simulated or sample.measurement_status != "ok":
            return
        tank = self.tanks.get(sample.device_id)
        if tank is None:
            return
        values = calibrated_values(tank, sample.distance_mm)
        percent = values["fill_percent"]
        if percent is None:
            return
        fingerprint = hashlib.sha256(tank.model_dump_json().encode()).hexdigest()
        row = db.execute("SELECT * FROM notification_state WHERE device_id=?", (sample.device_id,)).fetchone()
        if row is None or row["calibration_key"] != fingerprint:
            # Primera lectura o canvi de calibratge: referència, sense avisos retroactius.
            armed = {threshold for threshold in THRESHOLDS if percent > threshold}
            if row is not None:
                db.execute("UPDATE notification_outbox SET status='cancelled' WHERE device_id=? AND status='pending'",
                           (sample.device_id,))
        else:
            try:
                state = json.loads(row["state"])
                previous = state["last_percent"]
                armed = set(state["armed"])
            except (ValueError, KeyError, TypeError) as error:
                raise sqlite3.DatabaseError("Estat de notificació invàlid") from error
            for threshold in THRESHOLDS:
                if threshold in armed and previous > threshold >= percent:
                    payload = self.payload(sample, tank, values, threshold)
                    event_id = hashlib.sha256(f"{sample.device_id}:{sample.sample_id}:{threshold}".encode()).hexdigest()
                    db.execute("""INSERT INTO notification_outbox
                        (event_id,device_id,threshold_percent,payload,created_at,next_attempt_at)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                        (event_id, sample.device_id, threshold, json.dumps(payload, ensure_ascii=False), now, now))
                    armed.remove(threshold)
                # Un ascens de 5 punts evita que el soroll a la frontera repeteixi avisos.
                if percent >= threshold + self.settings.ntfy_hysteresis_percent:
                    armed.add(threshold)
                    db.execute("""UPDATE notification_outbox SET status='cancelled'
                        WHERE device_id=? AND threshold_percent=? AND status='pending'""",
                        (sample.device_id, threshold))
        state = json.dumps({"last_percent": percent, "armed": sorted(armed)})
        db.execute("""INSERT INTO notification_state(device_id,calibration_key,state) VALUES(?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET calibration_key=excluded.calibration_key,state=excluded.state""",
            (sample.device_id, fingerprint, state))

    def payload(self, sample, tank, values, threshold):
        if threshold == 0:
            title, priority, tags = "Gasoil esgotat: 0%", 5, ["rotating_light", "oil_drum"]
            advice = "Dipòsit al mínim del calibratge. Comprova el nivell i reposa gasoil immediatament."
        elif threshold == 10:
            title, priority, tags = "Gasoil crític: 10%", 5, ["rotating_light", "oil_drum"]
            advice = "Risc elevat de quedar-se sense gasoil. Reposa gasoil com més aviat millor."
        elif threshold == 20:
            title, priority, tags = "Reserva baixa de gasoil: 20%", 4, ["warning", "oil_drum"]
            advice = "Queda poca reserva. Programa la reposició de gasoil amb prioritat."
        else:
            title, priority, tags = f"Gasoil: llindar del {threshold}%", 3, ["oil_drum"]
            advice = "El nivell ha travessat aquest llindar en descens."
        message = (f"{tank.name}: ha baixat al {threshold}% o menys.\n"
                   f"Nivell actual: {values['fill_percent']:.1f}% · {values['volume_liters']:.1f} L.\n{advice}")
        if values["calibration_mode"] == "two_point_prototype":
            message += "\nCalibratge provisional del prototip; litres orientatius."
        elif values["calibration_mode"] == "geometry_nominal":
            message += "\nVolum estimat a partir de la capacitat nominal i dimensions aproximades."
        message += f"\nVeure l'evolució: {self.settings.public_web_url}"
        return {"topic": self.settings.ntfy_topic, "title": title, "message": message,
                "priority": priority, "tags": tags, "click": self.settings.public_web_url,
                "actions": [{"action": "view", "label": "Veure evolució", "url": self.settings.public_web_url}]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NotificationSender:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.stopped = threading.Event()
        self.thread = None
        self.opener = urllib.request.build_opener(NoRedirect())

    def start(self):
        if self.settings.ntfy_enabled:
            self.thread = threading.Thread(target=self.run, name="ntfy-sender", daemon=True)
            self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=8)

    def run(self):
        while not self.stopped.is_set():
            try:
                self.store.check_mqtt_silence(self.settings)
                if not self.deliver_one():
                    self.stopped.wait(1)
            except sqlite3.Error:
                log.exception("No es pot consultar/desar la cua ntfy; es reintentarà")
                self.stopped.wait(5)

    def deliver_one(self):
        event = self.store.next_notification()
        if event is None:
            return False
        payload = json.loads(event["payload"])
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.settings.ntfy_token:
            headers["Authorization"] = "Bearer " + self.settings.ntfy_token
        elif self.settings.ntfy_username:
            pair = f"{self.settings.ntfy_username}:{self.settings.ntfy_password}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        request = urllib.request.Request(self.settings.ntfy_server_url.rstrip("/") + "/",
                                         data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers,
                                         method="POST")
        try:
            with self.opener.open(request, timeout=5) as response:
                result = json.loads(response.read(65536))
                if not isinstance(result, dict) or result.get("event") != "message" or not result.get("id") or result.get("topic") != payload["topic"]:
                    raise ValueError("Resposta ntfy inesperada")
        except (OSError, ValueError, urllib.error.HTTPError) as error:
            reason = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else "Error de connexió o resposta ntfy"
            delay = min(3600, 30 * 2 ** min(event["attempts"], 7))
            self.store.notification_failed(event["id"], reason, time.time() + delay)
            log.warning("Avís ntfy pendent (%s); nou intent en %s segons", reason, delay)
            return True
        self.store.notification_sent(event["id"])
        log.info("Avís ntfy confirmat: dispositiu=%s, llindar=%s%%", event["device_id"], event["threshold_percent"])
        return True
