"""Integration tests for Personal Intelligence & Workspace REST APIs."""

import os
import json
import tempfile
import pytest
from starlette.testclient import TestClient

from ghost.server.app import app
from ghost.core.workspace import WorkspaceManager
from ghost.core.personal_intelligence import PersonalIntelligenceStore


@pytest.fixture
def client():
    return TestClient(app)


def test_personal_intelligence_endpoints(client):
    # 1. Categories
    res = client.get("/api/personal_intelligence/categories")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "profile" in data["categories"]
    assert "procedural" in data["categories"]
    assert "goal" in data["categories"]

    # 2. Add memory
    add_res = client.post("/api/personal_intelligence/memories", json={
        "category": "procedural",
        "subject": "CAD Parametric Grid",
        "content": "Always align modular ports to 10mm increments.",
        "confidence": 0.95,
        "is_inferred": True
    })
    assert add_res.status_code == 200
    created = add_res.json()["memory"]
    mem_id = created["id"]
    assert created["subject"] == "CAD Parametric Grid"
    assert created["is_inferred"] is True

    # 3. List memories with filter
    list_res = client.get(f"/api/personal_intelligence/memories?category=procedural&is_inferred=true")
    assert list_res.status_code == 200
    mems = list_res.json()["memories"]
    assert any(m["id"] == mem_id for m in mems)

    # 4. User Correction (promotes to confirmed)
    upd_res = client.put(f"/api/personal_intelligence/memories/{mem_id}", json={
        "content": "Strictly align modular ports to 10mm increments with 2mm chamfer."
    })
    assert upd_res.status_code == 200
    updated = upd_res.json()["memory"]
    assert updated["content"] == "Strictly align modular ports to 10mm increments with 2mm chamfer."
    assert updated["is_inferred"] is False
    assert updated["source"] == "user_confirmed"

    # 5. Retrieval context
    ctx_res = client.get("/api/personal_intelligence/context?query=CAD")
    assert ctx_res.status_code == 200
    ctx_data = ctx_res.json()
    assert "snapshot" in ctx_data
    assert "retrieved_ids" in ctx_data

    # 6. Delete memory
    del_res = client.delete(f"/api/personal_intelligence/memories/{mem_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_workspace_endpoints(client):
    # 1. Initial state
    res = client.get("/api/workspace/state")
    assert res.status_code == 200
    st = res.json()
    assert "active_module" in st
    assert "tabs" in st

    # 2. Open tab
    open_res = client.post("/api/workspace/open", json={
        "module": "editor",
        "title": "Test Notes",
        "file_path": "/home/pranc/projects/ghost/README.md"
    })
    assert open_res.status_code == 200
    tab = open_res.json()["tab"]
    tab_id = tab["id"]
    assert tab["module"] == "editor"

    # State check
    st_res = client.get("/api/workspace/state")
    st = st_res.json()
    assert st["active_module"] == "editor"
    assert st["is_open"] is True

    # 3. Directory listing
    dir_res = client.get("/api/workspace/directory?path=/home/pranc/projects/ghost")
    assert dir_res.status_code == 200
    d_data = dir_res.json()
    assert d_data["ok"] is True
    assert d_data["count"] > 0

    # 4. Terminal command execution
    term_res = client.post("/api/workspace/terminal", json={
        "command": "echo 'HELLO_INTERNAL_WORKSPACE'"
    })
    assert term_res.status_code == 200
    t_data = term_res.json()
    assert t_data["ok"] is True
    assert "HELLO_INTERNAL_WORKSPACE" in t_data["stdout"]

    # 5. Close tab
    close_res = client.post("/api/workspace/close", json={"tab_id": tab_id})
    assert close_res.status_code == 200
