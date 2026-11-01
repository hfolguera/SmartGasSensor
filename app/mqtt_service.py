import logging
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from app.models import Telemetry

log = logging.getLogger("smartgas.mqtt")
MAX_PAYLOAD_BYTES = 16384


class MqttService:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.lock = threading.Lock()
        self.connected = False
        self.subscribed = False
        self.received = 0
        self.duplicates = 0
        self.rejected = 0
        self.storage_errors = 0
        self.last_message_at = None
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=True,
        )
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.on_connect_fail = self.on_connect_fail
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            self.client.tls_set(ca_certs=settings.mqtt_ca_file or None)
        self.client.enable_logger(log)

    def start(self):
        if self.settings.mqtt_enabled:
            self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
            self.client.loop_start()

    def stop(self):
        if self.settings.mqtt_enabled:
            self.client.disconnect()
            self.client.loop_stop()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            self.connected = not reason_code.is_failure
            self.subscribed = False
        if reason_code.is_failure:
            log.warning("MQTT connexió rebutjada: %s", reason_code)
            return
        result, _ = client.subscribe(self.settings.mqtt_topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            log.error("MQTT no ha pogut demanar la subscripció")
        else:
            log.info("MQTT connectat; subscripció sol·licitada a %s", self.settings.mqtt_topic)

    def on_subscribe(self, client, userdata, mid, reason_codes, properties):
        accepted = bool(reason_codes) and all(not code.is_failure for code in reason_codes)
        with self.lock:
            self.subscribed = accepted
        if not accepted:
            log.error("MQTT subscripció rebutjada; comprova les ACL del broker")

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        with self.lock:
            self.connected = self.subscribed = False
        if reason_code.is_failure:
            log.warning("MQTT desconnectat; es reintentarà")

    def on_connect_fail(self, client, userdata):
        log.warning("Broker MQTT inaccessible; es reintentarà")

    def on_message(self, client, userdata, message):
        try:
            if len(message.payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("payload massa gran")
            sample = Telemetry.model_validate_json(message.payload)
        except (ValidationError, ValueError):
            with self.lock:
                self.rejected += 1
            # No escrivim el payload al log: pot contenir dades no previstes.
            log.warning("Missatge MQTT descartat: format o esquema invàlid, topic=%s", message.topic)
            if message.qos:
                client.ack(message.mid, message.qos)
            return
        try:
            inserted = self.store.insert(sample, message.topic)
        except sqlite3.Error:
            with self.lock:
                self.storage_errors += 1
            # Sense ACK, reconnectar per recuperar el missatge QoS1 de la sessió persistent.
            log.exception("No s'ha pogut persistir la lectura; reiniciant connexió MQTT sense ACK")
            client.disconnect()
            # Un error d'emmagatzematge requereix reinici del procés per recuperar la sessió.
            # La readiness detecta aquest estat, però la liveness evita quedar desconnectat per sempre.
            return
        with self.lock:
            self.received += int(inserted)
            self.duplicates += int(not inserted)
            if inserted:
                self.last_message_at = time.time()
        if message.qos:
            client.ack(message.mid, message.qos)

    def status(self):
        with self.lock:
            return {
                "enabled": self.settings.mqtt_enabled,
                "connected": self.connected,
                "subscribed": self.subscribed,
                "received_total": self.received,
                "duplicates_total": self.duplicates,
                "rejected_total": self.rejected,
                "storage_errors_total": self.storage_errors,
                "last_message_at": self.last_message_at,
            }
