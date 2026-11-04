#!/usr/bin/env bash
# ==============================================================================
# Ghost AI / Rose Executive One-Click Launcher
# Task Force 141 Web Agency & Autonomous Operations Director
# ==============================================================================
set -e

GHOST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GHOST_URL="http://localhost:8765/"
OMNIROUTE_URL="http://127.0.0.1:20128/api/health"
ICON_PATH="${GHOST_ROOT}/assets/ghost-icon.png"

notify() {
    local title="$1"
    local msg="$2"
    local urgency="${3:-normal}"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a "Ghost AI" -i "${ICON_PATH}" -u "${urgency}" "${title}" "${msg}" || true
    fi
}

echo "[Ghost Launcher] Initiating Task Force 141 Executive Sequence..."
notify "Ghost AI Initiating" "Checking neural core and local services, boss..." "normal"

# 1. Verify OmniRoute (Neural Router Core)
if ! curl -s --max-time 2 "${OMNIROUTE_URL}" >/dev/null 2>&1 && ! curl -s --max-time 2 "http://127.0.0.1:20128/v1/models" >/dev/null 2>&1; then
    echo "[Ghost Launcher] OmniRoute not detected on 20128. Launching omniroute..."
    if command -v omniroute >/dev/null 2>&1; then
        setsid omniroute </dev/null >/tmp/omniroute.log 2>&1 &
        echo "[Ghost Launcher] Waiting for OmniRoute on port 20128..."
        for i in {1..30}; do
            if curl -s --max-time 1 "${OMNIROUTE_URL}" >/dev/null 2>&1 || curl -s --max-time 1 "http://127.0.0.1:20128/v1/models" >/dev/null 2>&1; then
                echo "[Ghost Launcher] OmniRoute is online."
                break
            fi
            sleep 0.5
        done
    else
        notify "OmniRoute Missing" "Binary omniroute not found in PATH." "critical"
    fi
else
    echo "[Ghost Launcher] OmniRoute is already active."
fi

# 2. Verify Ghost Server (Port 8765)
if ! curl -s --max-time 2 "http://127.0.0.1:8765/" >/dev/null 2>&1; then
    echo "[Ghost Launcher] Ghost server not detected on 8765. Launching Uvicorn daemon with CUDA GPU speech..."
    cd "${GHOST_ROOT}"
    
    VENV_PY=""
    if [ -x "${HOME}/.venvs/ghost-gpu/bin/python" ]; then
        VENV_PY="${HOME}/.venvs/ghost-gpu/bin/python"
    elif [ -x "${HOME}/kokoro-gpu/.venv/bin/python" ]; then
        VENV_PY="${HOME}/kokoro-gpu/.venv/bin/python"
    else
        VENV_PY="python3"
    fi

    NVLIBS=$(${VENV_PY} - <<'EOF' 2>/dev/null || true
import glob, os
try:
    import nvidia
    root = list(nvidia.__path__)[0]
    libs = sorted(glob.glob(os.path.join(root, "*", "lib")))
    print(":".join(libs))
except Exception:
    pass
EOF
    )
    if [ -n "${NVLIBS}" ]; then
        export LD_LIBRARY_PATH="${NVLIBS}:${LD_LIBRARY_PATH:-}"
    fi

    PYTHONPATH="${GHOST_ROOT}/src" setsid "${VENV_PY}" -m uvicorn ghost.server.app:app --host 127.0.0.1 --port 8765 </dev/null >/tmp/ghost-server.log 2>&1 &
    
    echo "[Ghost Launcher] Waiting for Ghost HTTP endpoint..."
    for i in {1..30}; do
        if curl -s --max-time 1 "http://127.0.0.1:8765/" >/dev/null 2>&1; then
            echo "[Ghost Launcher] Ghost daemon verified active."
            break
        fi
        sleep 0.5
    done
else
    echo "[Ghost Launcher] Ghost daemon is already online."
fi

# 3. Focus or Open Ghost HUD in Brave Browser
BRAVE_CDP_LIST="http://127.0.0.1:9222/json/list"
TAB_ID=""

if curl -s --max-time 1 "${BRAVE_CDP_LIST}" >/dev/null 2>&1; then
    echo "[Ghost Launcher] Brave CDP detected on port 9222. Checking open tabs..."
    TAB_ID=$(curl -s "${BRAVE_CDP_LIST}" | python3 -c '
import sys, json
try:
    tabs = json.load(sys.stdin)
    for t in tabs:
        if "8765" in t.get("url", ""):
            print(t.get("id", ""))
            break
except Exception:
    pass
' 2>/dev/null || true)
fi

if [ -n "${TAB_ID}" ]; then
    echo "[Ghost Launcher] Found existing Ghost HUD tab (${TAB_ID}). Activating via CDP..."
    curl -s "http://127.0.0.1:9222/json/activate/${TAB_ID}" >/dev/null 2>&1 || true
    # Focus Brave window in Hyprland if hyprctl is present
    if command -v hyprctl >/dev/null 2>&1; then
        hyprctl dispatch focuswindow "class:Brave" >/dev/null 2>&1 || hyprctl dispatch focuswindow "class:brave-browser" >/dev/null 2>&1 || true
    fi
else
    echo "[Ghost Launcher] Opening Ghost HUD in Brave browser..."
    if command -v brave >/dev/null 2>&1; then
        brave "${GHOST_URL}" >/dev/null 2>&1 &
    elif command -v brave-browser >/dev/null 2>&1; then
        brave-browser "${GHOST_URL}" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${GHOST_URL}" >/dev/null 2>&1 &
    fi
fi

# 4. Final Tactical Desktop Notification
notify "Ghost AI Online" "All stations operational. Voice engine and HUD green." "normal"
echo "[Ghost Launcher] Sequence complete. All stations operational."
