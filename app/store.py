import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from app.models import Telemetry


class Store:
    def __init__(self, path: str, retention_days: int = 730, notification_policy=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.retention_days = retention_days
        self.last_cleanup = 0.0
        self.notification_policy = notification_policy
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY,
            device_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            received_at REAL NOT NULL,
            topic TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            distance_mm REAL,
            simulated INTEGER NOT NULL DEFAULT 0,
            UNIQUE(device_id, sample_id)
          );
          CREATE INDEX IF NOT EXISTS readings_device_time ON readings(device_id, received_at DESC, id DESC);
          CREATE TABLE IF NOT EXISTS notification_state (
            device_id TEXT PRIMARY KEY, calibration_key TEXT NOT NULL, state TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, device_id TEXT NOT NULL,
            threshold_percent INTEGER NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL, sent_at REAL, last_error TEXT
          );
          CREATE INDEX IF NOT EXISTS notification_pending ON notification_outbox(status,next_attempt_at,id);
          CREATE TABLE IF NOT EXISTS mqtt_monitor (
            id INTEGER PRIMARY KEY CHECK(id=1), reference_at REAL NOT NULL,
            last_received_at REAL, alerted INTEGER NOT NULL DEFAULT 0
          );
          PRAGMA user_version=3;
        """)

        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO mqtt_monitor(id,reference_at,last_received_at)
                SELECT 1,COALESCE(MAX(received_at),?),MAX(received_at) FROM readings WHERE simulated=0""",
                (time.time(),))

    def insert(self, sample: Telemetry, topic: str, received_at: float | None = None) -> bool:
        now = time.time() if received_at is None else received_at
        with self.lock, self.db:
            cursor = self.db.execute(
                """INSERT INTO readings(device_id,sample_id,received_at,topic,payload,status,distance_mm,simulated)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(device_id,sample_id) DO NOTHING""",
                (sample.device_id, sample.sample_id, now, topic, sample.model_dump_json(),
                 sample.measurement_status, sample.distance_mm, int(sample.simulated)),
            )
            inserted = cursor.rowcount == 1
            if inserted and not sample.simulated:
                self.db.execute("UPDATE mqtt_monitor SET reference_at=?,last_received_at=?,alerted=0 WHERE id=1",
                                (now, now))
                self.db.execute("UPDATE notification_outbox SET status='cancelled' WHERE threshold_percent=-1 AND status='pending'")
            if inserted and self.notification_policy is not None:
                self.notification_policy.plan(self.db, sample, now)
            # Conserva sempre l'última lectura de cada dispositiu, encara que sigui antiga.
            if now - self.last_cleanup >= 3600:
                self.db.execute(
                    """DELETE FROM readings WHERE received_at < ? AND id NOT IN
                       (SELECT MAX(id) FROM readings GROUP BY device_id)""",
                    (now - self.retention_days * 86400,),
                )
                self.db.execute("DELETE FROM notification_outbox WHERE status!='pending' AND created_at<?",
                                (now - self.retention_days * 86400,))
                self.last_cleanup = now
            return inserted

    @staticmethod
    def decode(row):
        if row is None:
            return None
        return {**json.loads(row["payload"]), "received_at": row["received_at"], "topic": row["topic"]}

    def latest_all(self, real_only=False):
        with self.lock:
            condition = "WHERE simulated=0" if real_only else ""
            rows = self.db.execute(f"""SELECT * FROM readings WHERE id IN
                (SELECT MAX(id) FROM readings {condition} GROUP BY device_id) ORDER BY device_id""").fetchall()
            return [self.decode(row) for row in rows]

    def last_valid(self, device_id):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM readings WHERE device_id=? AND status='ok' AND simulated=0 ORDER BY id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            return self.decode(row)

    def history(self, device_id, since, limit=500, before=None):
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM readings WHERE device_id=? AND received_at>=? AND received_at<?
                   ORDER BY received_at DESC,id DESC LIMIT ?""",
                (device_id, since, before if before is not None else time.time() + 1, limit),
            ).fetchall()
            return [self.decode(row) for row in reversed(rows)]

    def mqtt_monitor(self):
        with self.lock:
            return dict(self.db.execute("SELECT * FROM mqtt_monitor WHERE id=1").fetchone())

    def check_mqtt_silence(self, settings, now=None):
        """Un avís per interrupció; el termini persisteix encara que es reiniciï el procés."""
        if not settings.ntfy_enabled or not settings.mqtt_enabled:
            return False
        now = time.time() if now is None else now
        with self.lock, self.db:
            monitor = self.mqtt_monitor()
            if monitor['alerted'] or now - monitor['reference_at'] <= 86400:
                return False
            elapsed = (now - monitor['reference_at']) / 3600
            message = (f"Fa {elapsed:.1f} hores que no arriba cap lectura MQTT nova del sensor. "
                       "Comprova l'alimentació, la connexió Wi-Fi i el broker MQTT.")
            if monitor['last_received_at'] is None:
                message += " Encara no s'ha rebut cap lectura real des que es va iniciar la supervisió."
            message += f"\nVeure l'evolució: {settings.public_web_url}"
            payload = {"topic": settings.ntfy_topic, "title": "Sensor sense dades durant més de 24 hores",
                       "message": message, "priority": 4, "tags": ["warning", "satellite"],
                       "click": settings.public_web_url,
                       "actions": [{"action": "view", "label": "Veure evolució", "url": settings.public_web_url}]}
            event_id = hashlib.sha256(f"mqtt-silence:{monitor['reference_at']}".encode()).hexdigest()
            self.db.execute("""INSERT INTO notification_outbox
                (event_id,device_id,threshold_percent,payload,created_at,next_attempt_at)
                VALUES(?,?,-1,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, 'mqtt', json.dumps(payload, ensure_ascii=False), now, now))
            self.db.execute("UPDATE mqtt_monitor SET alerted=1 WHERE id=1")
            return True

    def next_notification(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM notification_outbox WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT 1",
                                  (time.time(),)).fetchone()
            return dict(row) if row else None

    def notification_sent(self, event_id):
        with self.lock, self.db:
            self.db.execute("UPDATE notification_outbox SET status='sent',sent_at=?,attempts=attempts+1,last_error=NULL WHERE id=?",
                            (time.time(), event_id))

    def notification_failed(self, event_id, error, next_attempt):
        with self.lock, self.db:
            self.db.execute("UPDATE notification_outbox SET attempts=attempts+1,last_error=?,next_attempt_at=? WHERE id=?",
                            (error, next_attempt, event_id))

    def notification_summary(self):
        with self.lock:
            rows = self.db.execute("SELECT status,COUNT(*) AS count FROM notification_outbox GROUP BY status").fetchall()
            counts = {row['status']: row['count'] for row in rows}
            error = self.db.execute("SELECT last_error FROM notification_outbox WHERE status='pending' AND last_error IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
            return {"pending": counts.get('pending', 0), "sent": counts.get('sent', 0),
                    "cancelled": counts.get('cancelled', 0), "last_error": error['last_error'] if error else None}

    def notifications(self, limit=20):
        with self.lock:
            rows = self.db.execute("SELECT * FROM notification_outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [{"id": row['id'], "device_id": row['device_id'], "threshold_percent": None if row['threshold_percent'] == -1 else row['threshold_percent'],
                     "type": "mqtt_silence" if row['threshold_percent'] == -1 else "level",
                     "created_at": row['created_at'], "sent_at": row['sent_at'], "status": row['status'],
                     "attempts": row['attempts'], "last_error": row['last_error'],
                     **json.loads(row['payload'])} for row in rows]

    def healthy(self):
        with self.lock:
            return self.db.execute("SELECT 1").fetchone()[0] == 1

    def close(self):
        with self.lock:
            self.db.close()
