#!/usr/bin/env python3
"""Envia dades simulades a un topic separat per defecte; contrasenya només per env."""
import argparse
import json
import os
import time
import uuid

import paho.mqtt.publish as publish

parser = argparse.ArgumentParser()
parser.add_argument("--host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
parser.add_argument("--topic", default="home/gas/demo/telemetry")
parser.add_argument("--distance", type=float, default=450)
args = parser.parse_args()
auth = {"username": os.environ["MQTT_USERNAME"], "password": os.getenv("MQTT_PASSWORD", "")} if os.getenv("MQTT_USERNAME") else None
sample = {"schema_version": 1, "device_id": "demo-sensor", "sample_id": "demo-" + uuid.uuid4().hex,
          "distance_mm": args.distance, "spread_mm": 0.4, "valid_samples": 9, "measurement_status": "ok",
          "battery_mv": None, "wifi_rssi_dbm": -45, "awake_ms": 5400, "sample_period_s": 900,
          "firmware_version": "demo", "simulated": True}
tls = {"ca_certs": os.getenv("MQTT_CA_FILE") or None} if os.getenv("MQTT_TLS", "false").lower() == "true" else None
publish.single(args.topic, json.dumps(sample), hostname=args.host, port=args.port, qos=1,
               client_id="smartgas-demo-" + str(time.time_ns()), auth=auth, tls=tls)
print("Publicada lectura SIMULADA a", args.topic)
