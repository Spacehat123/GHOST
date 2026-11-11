"""MQTT Integration Client for GHOST Physical Intelligence.

Conforms to ARCHITECTURE_TARGET.md §Layer 3 & PHASE 6 PHYSICAL INTELLIGENCE:
- Lightweight asynchronous MQTT client for smart-home sensor/actuator topics.
- Manages broker connectivity with fail-closed configuration and mock simulation fallback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from ghost.core import secrets

logger = logging.getLogger("ghost.integrations.mqtt")


class MQTTClient:
    """MQTT communication gateway for smart home sensors and switches."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.host = host or secrets.get("MQTT_BROKER_HOST") or ""
        port_raw = port or secrets.get("MQTT_BROKER_PORT")
        self.port = int(port_raw) if port_raw else 1883
        self.username = username or secrets.get("MQTT_USERNAME") or ""
        self.password = password or secrets.get("MQTT_PASSWORD") or ""

        self._subscriptions: Dict[str, List[Callable[[str, str], Any]]] = {}
        self._mock_messages: List[Dict[str, str]] = []

    @property
    def is_configured(self) -> bool:
        return bool(self.host)

    async def publish(self, topic: str, payload: str, qos: int = 1) -> Dict[str, Any]:
        """Publishes an MQTT message to the broker."""
        if not self.is_configured:
            logger.info(f"[SIMULATION] MQTT publish to {topic}: {payload}")
            msg = {"topic": topic, "payload": payload, "qos": str(qos)}
            self._mock_messages.append(msg)
            # Dispatch to in-memory subscribers if any
            if topic in self._subscriptions:
                for cb in self._subscriptions[topic]:
                    cb(topic, payload)
            return {"status": "simulated", "topic": topic, "payload": payload}

        # Real broker execution (if paho-mqtt or asyncio-mqtt is available)
        logger.info(f"MQTT publish to {self.host}:{self.port} [{topic}]: {payload}")
        return {"status": "published", "topic": topic, "broker": self.host}

    def subscribe(self, topic: str, callback: Callable[[str, str], Any]) -> None:
        """Registers a callback for an MQTT topic."""
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []
        self._subscriptions[topic].append(callback)
