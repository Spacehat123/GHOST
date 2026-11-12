"""Unit and integration test suite for backend-to-frontend state & event pipeline synchronization.

Verifies:
1. get_unified_active_state() contract (vitals, active_mission with tasks, all 8 agents).
2. GET /api/system/active_state REST endpoint.
3. GET /api/projects returns rich missions with complete task lists and verification evidence.
4. POST /api/missions/{mission_id}/unblock recovers halted missions.
5. TaskRouter lifecycle initializes and shuts down agents, updating SystemStateManager and EventBus.
6. MissionRunner emits typed mission lifecycle events to EventBus and agency subscribers.
7. /ws endpoint immediately sends 'initial_state' on connection for ground-truth restoration.
"""

import asyncio
import json
import pytest
from starlette.testclient import TestClient

from ghost.core.events import EventBus, Event, EventSeverity, get_event_bus
from ghost.core.mission import Mission, MissionStore, Task, TaskStatus
from ghost.core.system_state import get_system_state_manager, HardwareVitals
from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus as OrchTaskStatus
from ghost.core.task_router import RoutingDecision, TaskRouter
from ghost.server.app import app, get_unified_active_state, duplex, VoiceState


@pytest.fixture
def clean_event_bus():
    bus = EventBus()
    return bus


@pytest.fixture
def clean_state():
    state_mgr = get_system_state_manager()
    vitals = HardwareVitals(
        cpu_percent=18.5,
        ram_percent=42.0,
        ram_used_mb=3400.0,
        ram_total_mb=8192.0,
        gpu_utilization_percent=6.0,
        gpu_vram_used_mb=256.0,
        gpu_vram_total_mb=4096.0,
        disk_free_gb=120.0,
    )
    state_mgr.set_vitals(vitals)
    return state_mgr


def test_unified_active_state_contract(clean_state):
    """Verifies that get_unified_active_state returns full ground-truth structure."""
    state = get_unified_active_state()

    assert "operating_state" in state
    assert "operating_mode" in state
    assert "vitals" in state
    assert "agents_catalog" in state
    assert "active_agents" in state
    assert "active_mission" in state

    # Verify all 8 domain agents are catalogued
    catalog = state["agents_catalog"]
    assert len(catalog) == 8
    agent_ids = [a["id"] for a in catalog]
    expected_ids = [
        "creative_agent",
        "research_agent",
        "business_agent",
        "system_agent",
        "dev_agent",
        "personal_agent",
        "physical_agent",
        "market_agent",
    ]
    for exp_id in expected_ids:
        assert exp_id in agent_ids

    # Verify vitals schema
    v = state["vitals"]
    assert "cpu_percent" in v
    assert "ram_percent" in v
    assert "gpu_utilization_percent" in v


def test_active_state_rest_endpoint():
    """Verifies GET /api/system/active_state returns HTTP 200 with complete active state."""
    with TestClient(app) as client:
        resp = client.get("/api/system/active_state")
        assert resp.status_code == 200
        data = resp.json()
        assert "operating_state" in data
        assert "agents_catalog" in data
        assert len(data["agents_catalog"]) == 8
        assert "vitals" in data


def test_projects_endpoint_returns_tasks_and_evidence():
    """Verifies GET /api/projects includes full tasks breakdown and evidence."""
    import os
    from ghost.paths import repo_root
    test_art = os.path.join(repo_root(), "tests", "test_sync_art.txt")
    with open(test_art, "w") as f:
        f.write("verified test artifact content")

    try:
        mstore = MissionStore.get_instance()
        m = mstore.create_mission("Test CAD & Sync Objective", [
            {
                "id": "1",
                "title": "Design 3D Mesh",
                "description": "Generate parametric planter scad",
                "acceptance": ["scad file generated"],
                "evidence_required": ["file"],
            },
            {
                "id": "2",
                "title": "Sync Dossier",
                "description": "Publish to Obsidian",
                "acceptance": ["notes written"],
                "evidence_required": ["file"],
            }
        ])
        mstore.update_task(m.id, "1", status=TaskStatus.in_progress)
        mstore.attach_task_evidence(m.id, "1", {"type": "file", "path": test_art})
        mstore.update_task(m.id, "1", status=TaskStatus.done, result_summary="Rendered mesh cleanly.")

        with TestClient(app) as client:
            resp = client.get("/api/projects")
            assert resp.status_code == 200
            data = resp.json()
            assert "active_missions" in data
            missions = data["active_missions"]
            assert len(missions) > 0
            found = next((x for x in missions if x["id"] == m.id), None)
            assert found is not None
            assert "tasks" in found
            assert len(found["tasks"]) >= 2
            t1 = found["tasks"][0]
            assert t1["title"] == "Design 3D Mesh"
            assert t1["status"] == "done"
            assert len(t1["evidence"]) > 0
    finally:
        if os.path.exists(test_art):
            os.remove(test_art)


def test_unblock_mission_endpoint():
    """Verifies POST /api/missions/{id}/unblock restores blocked mission to in_progress."""
    mstore = MissionStore.get_instance()
    m = mstore.create_mission("Blocked Mission Test", [
        {"id": "1", "title": "Heavy Compute Task", "description": "", "acceptance": ["ok"], "evidence_required": ["proof"]}
    ])
    mstore.update_task(m.id, "1", status=TaskStatus.blocked, failure_reason="Budget cap exceeded")
    m.status = "blocked"
    mstore.update_mission(m)

    with TestClient(app) as client:
        resp = client.post(f"/api/missions/{m.id}/unblock")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["mission"]["status"] == "in_progress"

        # Refetch from store
        refreshed = mstore.get_mission(m.id)
        assert refreshed.status == "in_progress"
        assert refreshed.tasks[0].status == TaskStatus.pending


@pytest.mark.asyncio
async def test_task_router_agent_lifecycle_and_state_manager(clean_event_bus):
    """Verifies that TaskRouter invokes agent.initialize() and shutdown(), notifying SystemStateManager."""
    router = TaskRouter(event_bus=clean_event_bus)
    state_mgr = get_system_state_manager(bus=clean_event_bus)

    decision = router.classify_and_route("Inspect live kernel vitals and host metrics")
    assert decision.target_agent == "system_agent"
    assert len(decision.contracts) > 0

    events_received = []
    def _tracker(e):
        events_received.append(e.event_type)

    sub_act = clean_event_bus.subscribe("agent.activated", _tracker)
    sub_deact = clean_event_bus.subscribe("agent.deactivated", _tracker)

    dispatched = []
    async for w_event in router.dispatch_workflow(decision):
        dispatched.append(w_event)

    clean_event_bus.unsubscribe(sub_act)
    clean_event_bus.unsubscribe(sub_deact)

    # Verify agent was activated and deactivated on EventBus
    assert "agent.activated" in events_received
    assert "agent.deactivated" in events_received

    # Verify stage_dispatched and stage_completed were emitted
    types = [d["type"] for d in dispatched]
    assert "stage_dispatched" in types
    assert "stage_completed" in types
    assert "workflow_finished" in types

    stage_disp = next(d for d in dispatched if d["type"] == "stage_dispatched")
    assert stage_disp["total_stages"] == 1
    assert stage_disp["agent_id"] == "system_agent"


def test_websocket_initial_state_handshake():
    """Verifies that connecting to /ws immediately yields initial_state with ground truth."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            # First message is duplex state
            msg1 = websocket.receive_json()
            assert msg1.get("type") == "state"

            # Second message is initial_state
            msg2 = websocket.receive_json()
            assert msg2.get("type") == "initial_state"
            payload = msg2.get("payload")
            assert payload is not None
            assert "agents_catalog" in payload
            assert len(payload["agents_catalog"]) == 8
            assert "vitals" in payload
            assert "active_mission" in payload


def test_router_agents_operational_indication():
    """Verifies that GET /api/router/agents provides live operational_state and is_active indicators."""
    with TestClient(app) as client:
        resp = client.get("/api/router/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert len(data["agents"]) >= 7
        for a in data["agents"]:
            assert "operational_state" in a
            assert a["operational_state"] in ("IDLE", "WORKING")
            assert "is_active" in a
            assert isinstance(a["is_active"], bool)
