"""Regression tests for UI elements: Voice selection, Built-in Workspace, and Personal Intelligence."""

import os
import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

from ghost.server.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_html_contains_restored_voice_and_workspace_elements():
    """Verify that all restored UI elements exist in index.html and were not stripped."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    index_path = os.path.join(repo_root, "gui", "index.html")
    assert os.path.exists(index_path)

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    # 1. Voice selector restored
    voice_sel = soup.find("select", {"id": "select-hud-voice"})
    assert voice_sel is not None, "Missing select-hud-voice in index.html"
    options = [o.get("value") for o in voice_sel.find_all("option")]
    assert "af_nicole" in options
    assert "am_adam" in options
    assert "bf_alice" in options
    assert "hf_alpha" in options

    # 2. Workspace toggle button in top HUD
    ws_toggle = soup.find("button", {"id": "btn-toggle-workspace"})
    assert ws_toggle is not None

    # 3. Built-in Workspace quick buttons in left dock
    expected_module_btns = [
        "btn-ws-pdf", "btn-ws-editor", "btn-ws-code", "btn-ws-image",
        "btn-ws-camera", "btn-ws-audio", "btn-ws-video", "btn-ws-browser",
        "btn-ws-files", "btn-ws-terminal", "btn-ws-charts", "btn-ws-sysmon"
    ]
    for btn_id in expected_module_btns:
        btn = soup.find("button", {"id": btn_id})
        assert btn is not None, f"Missing workspace button: {btn_id}"

    # 4. Adaptive Interface containers
    canvas_container = soup.find("div", {"id": "canvas-container"})
    assert canvas_container is not None
    mini_dock = soup.find("div", {"id": "mini-core-dock"})
    assert mini_dock is not None

    ws_viewport = soup.find("div", {"id": "workspace-viewport"})
    assert ws_viewport is not None
    assert soup.find("div", {"id": "workspace-tab-strip"}) is not None
    assert soup.find("div", {"id": "workspace-content-pane"}) is not None

    # 5. Personal Intelligence System in Command Deck
    mem_view = soup.find("section", {"id": "view-memory"})
    assert mem_view is not None
    assert soup.find("input", {"id": "memory-search-input"}) is not None
    assert soup.find("select", {"id": "select-memory-inferred-filter"}) is not None
    assert soup.find("div", {"id": "personal-memories-list"}) is not None
    assert soup.find("div", {"id": "personal-influence-audit"}) is not None
    assert soup.find("div", {"id": "personal-memory-modal"}) is not None

    # Category strip
    cat_btns = soup.find_all("button", class_="memory-cat-btn")
    cats = [b.get("data-cat") for b in cat_btns]
    for c in ["all", "profile", "procedural", "project", "goal", "knowledge", "episodic"]:
        assert c in cats, f"Missing category button for: {c}"

    # Scripts loaded
    scripts = [s.get("src") for s in soup.find_all("script") if s.get("src")]
    assert "/static/js/orb.js" in scripts
    assert "/static/js/workspace.js" in scripts
    assert "/static/js/app.js" in scripts
    assert "/static/js/agency_deck.js" in scripts


def test_voice_api_roundtrip(client):
    """Test voice selection and querying via REST API."""
    res = client.get("/api/tts/voices")
    assert res.status_code == 200
    data = res.json()
    assert "voices" in data
    assert "current" in data

    # Switch voice
    post_res = client.post("/api/tts/voice", json={"voice": "am_adam"})
    assert post_res.status_code == 200
    assert post_res.json()["voice"] == "am_adam"

    # Reset back to af_nicole
    client.post("/api/tts/voice", json={"voice": "af_nicole"})
