"""MQTT coordinator for Cradlewise — paho-mqtt 1.6.1 API."""
from __future__ import annotations

import json
import logging
import ssl
import threading
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .const import (
    CRADLE_ID, HOST, PORT,
    SHADOW_GET, SHADOW_GET_ACC,
    SHADOW_UPDATE, SHADOW_UPD_ACC, SHADOW_UPD_REJ, SHADOW_DELTA,
)

_LOGGER = logging.getLogger(__name__)

CERTS_DIR = Path(__file__).parent / "certs"


class CradlewiseCoordinator:
    """Manages a single persistent MQTT connection to AWS IoT Core."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self.state: dict[str, Any] = {}
        self.available = False
        self._listeners: list[Callable] = []
        self._client: mqtt.Client | None = None
        self._stop_event = threading.Event()

    # ── Listener management ──────────────────────────────────────────────

    def async_add_listener(self, cb: Callable) -> Callable:
        """Register a state-change listener; returns an unsubscribe function."""
        self._listeners.append(cb)
        def _remove():
            self._listeners.remove(cb)
        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            self.hass.add_job(cb)

    # ── Connection ───────────────────────────────────────────────────────

    def start(self) -> None:
        ctx = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(CERTS_DIR / "amazon_root_ca1.pem"),
        )
        ctx.load_cert_chain(
            certfile=str(CERTS_DIR / "client.pem"),
            keyfile=str(CERTS_DIR / "client.key"),
        )
        ctx.check_hostname = False

        client = mqtt.Client(client_id="cradlewise-ha")
        client.tls_set_context(ctx)
        client.on_connect    = self._on_connect
        client.on_message    = self._on_message
        client.on_disconnect = self._on_disconnect

        self._client = client
        _LOGGER.debug("Connecting to %s:%s", HOST, PORT)
        client.connect_async(HOST, PORT, keepalive=30)
        client.loop_start()

    def stop(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    # ── paho callbacks (run in paho's thread) ────────────────────────────

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc != 0:
            _LOGGER.error("MQTT connect failed rc=%s", rc)
            return
        _LOGGER.info("Cradlewise MQTT connected")
        client.subscribe(SHADOW_GET_ACC, qos=1)
        client.subscribe(SHADOW_UPD_ACC, qos=1)
        client.subscribe(SHADOW_UPD_REJ, qos=1)
        client.subscribe(SHADOW_DELTA,   qos=1)
        client.publish(SHADOW_GET, json.dumps({}), qos=1)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except Exception:
            return

        topic = msg.topic
        if topic == SHADOW_GET_ACC:
            reported = data.get("state", {}).get("reported", {})
            self._merge(reported)

        elif topic == SHADOW_DELTA:
            # Delta carries desired-not-yet-reported keys — don't merge into
            # state or HA will display pending desired values as actual state.
            _LOGGER.debug("Shadow delta (ignored for state): %s", data.get("state", {}))
            self.available = True
            return

        elif topic == SHADOW_UPD_ACC:
            reported = data.get("state", {}).get("reported", {})
            if reported:
                self._merge(reported)

        elif topic == SHADOW_UPD_REJ:
            _LOGGER.warning("Shadow update rejected: %s", data)

        self.available = True
        self._notify()

    def _on_disconnect(self, client, userdata, rc) -> None:
        _LOGGER.warning("Cradlewise MQTT disconnected rc=%s — will reconnect", rc)
        self.available = False
        self._notify()

    # ── State helpers ────────────────────────────────────────────────────

    def _merge(self, reported: dict) -> None:
        for k, v in reported.items():
            if isinstance(v, dict) and isinstance(self.state.get(k), dict):
                self.state[k] = {**self.state[k], **v}
            else:
                self.state[k] = v

    def _merge_delta(self, delta: dict) -> None:
        """Delta only contains changed keys; merge them into state."""
        self._merge(delta)

    # ── Command API ──────────────────────────────────────────────────────

    def send_desired(self, payload: dict) -> None:
        """Publish a shadow desired-state update."""
        if not self._client:
            _LOGGER.error("MQTT not connected")
            return
        msg = json.dumps({"state": {"desired": payload}})
        _LOGGER.debug("shadow update → %s", msg)
        self._client.publish(SHADOW_UPDATE, msg, qos=1)

    # ── Convenience state accessors ──────────────────────────────────────

    def get_actuator(self) -> dict:
        return self.state.get("actuator") or {}

    def get_sound(self) -> dict:
        return self.state.get("soundSynth") or {}

    def get_light(self) -> dict:
        return self.state.get("light") or {}
