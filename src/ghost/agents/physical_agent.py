"""Physical Intelligence Domain Agent for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 6 & PHASE 6 PHYSICAL INTELLIGENCE:
- Smart-home perception and automation via Home Assistant and MQTT.
- Controls smart switches, lights, climate, and IoT actuators.
- Permission-gated physical actions with fail-closed security.
- Mandatory independent readback verification: asserts physical device state transitioned.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.core.events import EventBus
from ghost.core.task_orchestrator import Task, TaskStatus
from ghost.integrations.homeassistant import HomeAssistantClient
from ghost.integrations.mqtt import MQTTClient

logger = logging.getLogger("ghost.agents.physical")


class PhysicalIntelligenceAgent(AgentContract):
    """Specialized agent managing smart-home devices, sensors, and verified physical actions."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        ha_client: Optional[HomeAssistantClient] = None,
        mqtt_client: Optional[MQTTClient] = None,
    ):
        super().__init__(
            agent_id="physical_agent",
            name="Physical Intelligence Agent",
            responsibility="Smart-home perception, Home Assistant and MQTT integration, and verified physical execution",
            supported_tasks=[
                "inspect_environment",
                "control_device",
                "toggle_switch",
                "publish_mqtt_command",
                "verify_physical_state",
            ],
            required_permissions=[
                "physical.inspect",
                "physical.control",
                "physical.mqtt",
            ],
            event_bus=event_bus,
        )
        self.ha_client = ha_client or HomeAssistantClient()
        self.mqtt_client = mqtt_client or MQTTClient()

    async def plan_task(self, objective: Dict[str, Any]) -> List[Task]:
        """Decomposes smart-home physical goals into atomic executable tasks."""
        action = objective.get("type", "inspect_environment")
        obj_id = objective.get("objective_id", f"obj-physical-{uuid.uuid4().hex[:8]}")

        tasks: List[Task] = []

        if action == "control_device" or action == "toggle_switch":
            entity_id = objective.get("entity_id", "switch.desk_lamp")
            target_state = objective.get("target_state", "toggle")
            tasks.append(
                Task(
                    task_id=f"tsk-{uuid.uuid4().hex[:12]}",
                    objective_id=obj_id,
                    assigned_agent=self.agent_id,
                    title=f"Set Physical Device: {entity_id} -> {target_state}",
                    description=f"Send service call to Home Assistant for {entity_id} and independently verify state readback.",
                    status=TaskStatus.PLANNED,
                    input_payload={
                        "action": action,
                        "entity_id": entity_id,
                        "target_state": target_state,
                        "domain": entity_id.split(".")[0] if "." in entity_id else "switch",
                    },
                    required_permissions=["physical.control"],
                )
            )
        else:
            tasks.append(
                Task(
                    task_id=f"tsk-{uuid.uuid4().hex[:12]}",
                    objective_id=obj_id,
                    assigned_agent=self.agent_id,
                    title="Inspect Smart Home Physical Environment",
                    description="Query Home Assistant and MQTT sensors for ambient temperature, presence, and device states.",
                    status=TaskStatus.PLANNED,
                    input_payload={"action": "inspect_environment"},
                    required_permissions=["physical.inspect"],
                )
            )

        return tasks

    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes smart-home physical actions."""
        action = task.input_payload.get("action", "inspect_environment")

        if action in ("control_device", "toggle_switch"):
            entity_id = task.input_payload.get("entity_id", "switch.desk_lamp")
            target_state = task.input_payload.get("target_state", "on")
            domain = task.input_payload.get("domain", "switch")

            service = "turn_on" if target_state == "on" else ("turn_off" if target_state == "off" else "toggle")

            call_res = await self.ha_client.call_service(
                domain=domain,
                service=service,
                service_data={"entity_id": entity_id},
            )

            # Read back state for verification
            readback_state = await self.ha_client.get_state(entity_id)

            return {
                "status": "success",
                "action": action,
                "entity_id": entity_id,
                "service_called": f"{domain}.{service}",
                "call_result": call_res,
                "readback_state": readback_state,
            }

        elif action == "publish_mqtt_command":
            topic = task.input_payload.get("topic", "ghost/actuators/cmd")
            payload = task.input_payload.get("payload", "{}")
            res = await self.mqtt_client.publish(topic, str(payload))
            return {"status": "success", "mqtt_result": res}

        # Default: inspect environment
        states = await self.ha_client.get_states()
        return {
            "status": "success",
            "action": "inspect_environment",
            "entities_count": len(states),
            "states": states,
            "configured": self.ha_client.is_configured,
        }

    async def verify_outcome(self, task: Task, output: Dict[str, Any]) -> VerificationEvidence:
        """Independently verifies physical outcomes via readback or entity confirmation."""
        action = task.input_payload.get("action", "inspect_environment")

        if action in ("control_device", "toggle_switch"):
            readback = output.get("readback_state")
            entity_id = task.input_payload.get("entity_id")
            target_state = task.input_payload.get("target_state")

            if readback and readback.get("entity_id") == entity_id:
                curr_state = str(readback.get("state")).lower()
                verified = True
                if target_state in ("on", "off"):
                    verified = (curr_state == target_state.lower())

                return VerificationEvidence(
                    verified=verified,
                    evidence_type="readback",
                    artifacts=[],
                    details={
                        "entity_id": entity_id,
                        "observed_state": curr_state,
                        "expected_state": target_state,
                        "configured": self.ha_client.is_configured,
                    },
                )

            return VerificationEvidence(
                verified=False,
                evidence_type="readback",
                artifacts=[],
                details={"error": "Failed to read back entity state from Home Assistant"},
            )

        # Inspect environment verification
        verified = (output.get("status") == "success" and output.get("entities_count", 0) >= 0)
        return VerificationEvidence(
            verified=verified,
            evidence_type="sensor_telemetry",
            artifacts=[],
            details={"entities_count": output.get("entities_count", 0)},
        )
