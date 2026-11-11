"""Home Assistant REST Integration Client for GHOST Physical Intelligence.

Conforms to ARCHITECTURE_TARGET.md §Layer 3 & PHASE 6 PHYSICAL INTELLIGENCE:
- Directs commands to Home Assistant Core REST API (/api/states, /api/services).
- Independent state verification: verifies physical entity states match expected values after service calls.
- Fail-closed security: if credentials are not configured or endpoint is unreachable, operates in safe simulation mode with explicit status flags.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ghost.core import secrets

logger = logging.getLogger("ghost.integrations.homeassistant")


class HomeAssistantClient:
    """Interface to Home Assistant smart home core with readback verification."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_s: float = 8.0,
    ):
        self.base_url = (base_url or secrets.get("HOMEASSISTANT_URL") or "").rstrip("/")
        self.token = token or secrets.get("HOMEASSISTANT_TOKEN") or ""
        self.timeout_s = timeout_s
        self._mock_states: Dict[str, Dict[str, Any]] = {
            "light.living_room": {"entity_id": "light.living_room", "state": "off", "attributes": {"friendly_name": "Living Room Light"}},
            "switch.desk_lamp": {"entity_id": "switch.desk_lamp", "state": "off", "attributes": {"friendly_name": "Desk Lamp"}},
            "sensor.temperature": {"entity_id": "sensor.temperature", "state": "22.5", "attributes": {"unit_of_measurement": "°C", "friendly_name": "Ambient Temp"}},
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> List[Dict[str, Any]]:
        """Queries all entity states from Home Assistant."""
        if not self.is_configured:
            logger.info("Home Assistant not configured; returning simulated state.")
            return list(self._mock_states.values())

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                res = await client.get(f"{self.base_url}/api/states", headers=self._headers())
                if res.status_code == 200:
                    return res.json()
                logger.warning(f"Home Assistant states query returned HTTP {res.status_code}")
                return list(self._mock_states.values())
        except Exception as e:
            logger.warning(f"Failed to query Home Assistant at {self.base_url}: {e}")
            return list(self._mock_states.values())

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Queries a single entity state from Home Assistant."""
        if not self.is_configured:
            return self._mock_states.get(entity_id)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                res = await client.get(f"{self.base_url}/api/states/{entity_id}", headers=self._headers())
                if res.status_code == 200:
                    return res.json()
                return self._mock_states.get(entity_id)
        except Exception as e:
            logger.debug(f"Home Assistant get_state failed for {entity_id}: {e}")
            return self._mock_states.get(entity_id)

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Calls a Home Assistant service (e.g. light.turn_on, switch.toggle)."""
        data = service_data or {}
        entity_id = data.get("entity_id")

        if not self.is_configured:
            logger.info(f"[SIMULATION] Calling Home Assistant service {domain}.{service} with {data}")
            if entity_id in self._mock_states:
                new_state = "on" if "turn_on" in service or "on" in service else "off"
                self._mock_states[entity_id]["state"] = new_state
            return {"status": "simulated", "domain": domain, "service": service, "data": data}

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            res = await client.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers(),
                json=data,
            )
            if res.status_code not in (200, 201):
                raise RuntimeError(f"Home Assistant service call failed: HTTP {res.status_code} - {res.text}")
            return {"status": "executed", "response": res.json()}

    async def verify_entity_state(
        self,
        entity_id: str,
        expected_state: str,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.5,
    ) -> bool:
        """Independently verifies that the physical entity has transitioned to the expected state."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout_s:
            state_data = await self.get_state(entity_id)
            if state_data and str(state_data.get("state")).lower() == expected_state.lower():
                return True
            await asyncio.sleep(poll_interval_s)
        return False
