import json
import sqlite3
import threading
import time
from pathlib import Path

from app.models import Telemetry


class Store:
    def __init__(self, path: str, retention_days: int = 730):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.retention_days = retention_days
        self.last_cleanup = 0.0
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
          PRAGMA user_version=1;
        """)

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
            # Conserva sempre l'última lectura de cada dispositiu, encara que sigui antiga.
            if now - self.last_cleanup >= 3600:
                self.db.execute(
                    """DELETE FROM readings WHERE received_at < ? AND id NOT IN
                       (SELECT MAX(id) FROM readings GROUP BY device_id)""",
                    (now - self.retention_days * 86400,),
                )
                self.last_cleanup = now
            return inserted

    @staticmethod
    def decode(row):
        if row is None:
            return None
        return {**json.loads(row["payload"]), "received_at": row["received_at"], "topic": row["topic"]}

    def latest_all(self):
        with self.lock:
            rows = self.db.execute("""SELECT * FROM readings WHERE id IN
                (SELECT MAX(id) FROM readings GROUP BY device_id) ORDER BY device_id""").fetchall()
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

    def healthy(self):
        with self.lock:
            return self.db.execute("SELECT 1").fetchone()[0] == 1

    def close(self):
        with self.lock:
            self.db.close()
