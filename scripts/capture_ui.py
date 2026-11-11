"""Script to capture real visual screenshots of the redesigned Cybernetic GHOST UI via Brave CDP."""

import asyncio
import base64
import json
import os
import shutil
import urllib.request
import websockets

ARTIFACTS_DIR = "/home/pranc/.gemini/antigravity/brain/a751f0ce-accc-4471-8bb1-f0d8fcca792f"
TEMP_DIR = "temp"


async def capture():
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    req = urllib.request.Request("http://127.0.0.1:9222/json/new?http://127.0.0.1:8765/", method="PUT")
    with urllib.request.urlopen(req) as resp:
        tab = json.loads(resp.read().decode())
    ws_url = tab["webSocketDebuggerUrl"]
    tab_id = tab["id"]
    print(f"Connected to CDP tab: {tab_id}")

    try:
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(json.dumps({
                "id": 2,
                "method": "Emulation.setDeviceMetricsOverride",
                "params": {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False}
            }))
            await asyncio.sleep(2.0)

            msg_counter = 100

            async def capture_frame(filename):
                nonlocal msg_counter
                msg_counter += 1
                curr_id = msg_counter
                await ws.send(json.dumps({"id": curr_id, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == curr_id and "result" in msg and "data" in msg["result"]:
                        data = base64.b64decode(msg["result"]["data"])
                        local_path = os.path.join(TEMP_DIR, filename)
                        with open(local_path, "wb") as f:
                            f.write(data)
                        artifact_path = os.path.join(ARTIFACTS_DIR, filename)
                        shutil.copy2(local_path, artifact_path)
                        print(f"Saved {artifact_path} ({len(data)} bytes)")
                        return artifact_path

            async def eval_js(expr):
                nonlocal msg_counter
                msg_counter += 1
                curr_id = msg_counter
                await ws.send(json.dumps({
                    "id": curr_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expr, "awaitPromise": True}
                }))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == curr_id:
                        return msg.get("result")

            # Skip intro if still playing
            await eval_js("if (window.ghostOrb) { window.ghostOrb.skipIntro(); }")
            await asyncio.sleep(0.5)

            # 1. Main Cybernetic Command Dashboard (IDLE state)
            await eval_js("if (window.ghostOrb) { window.ghostOrb.setState('IDLE'); window.ghostOrb.setAmplitude(0.05); }")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_cybernetic_hud_idle.png")

            # 2. Cybernetic Core in LISTENING state
            await eval_js("if (window.ghostOrb) { window.ghostOrb.setState('LISTENING'); window.ghostOrb.setAmplitude(0.35); }")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_cybernetic_listening.png")

            # 3. Cybernetic Core in THINKING state (accelerating red arcs & harmonic contraction)
            await eval_js("if (window.ghostOrb) { window.ghostOrb.setState('THINKING'); window.ghostOrb.setAmplitude(0.65); }")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_cybernetic_thinking.png")

            # 4. Cybernetic Core in SPEAKING state (audio reactive FFT expansion)
            await eval_js("if (window.ghostOrb) { window.ghostOrb.setState('SPEAKING'); window.ghostOrb.setAmplitude(0.85); window.ghostOrb.setAudioBins([0.8, 0.9, 0.7, 0.85, 0.6, 0.75]); }")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_cybernetic_speaking.png")

            # Reset back to IDLE
            await eval_js("if (window.ghostOrb) { window.ghostOrb.setState('IDLE'); window.ghostOrb.setAmplitude(0.05); }")

            # 5. Command Deck: Awareness View
            await eval_js("document.getElementById('deck-overlay').classList.remove('hidden'); switchView('awareness');")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_awareness.png")

            # 6. Command Deck: Agents View
            await eval_js("switchView('agents');")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_agents.png")

            # 7. Command Deck: Intelligence Feed View
            await eval_js("switchView('feed');")
            await asyncio.sleep(1.0)
            await capture_frame("ghost_intel_feed.png")

    finally:
        # Close CDP tab
        try:
            req_close = urllib.request.Request(f"http://127.0.0.1:9222/json/close/{tab_id}", method="PUT")
            urllib.request.urlopen(req_close)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(capture())
