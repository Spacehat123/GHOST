"""FastAPI and WebSocket bridge for Ghost Desktop GUI with OmniRoute & Neural Audio Streaming."""
import asyncio
import json
import os
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain
from ghost.voice.duplex import DuplexCoordinator, VoiceState
from ghost.voice.kokoro import KokoroSpeechEngine, split_clauses
from ghost.watchdog.service import AutonomousWatchdog
from ghost.mcp.bridge import register_auth_callback
from ghost.server.agency_api import (
    agency_router,
    _event_subscribers,
    get_or_create_session_token
)
import edge_tts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ghost.server")

app = FastAPI(title="Ghost Voice Assistant & OmniRoute Neural Runtime")
app.include_router(agency_router)

agent = GhostAgent()
duplex = DuplexCoordinator(on_interrupt=agent.interrupt)

# Pending authorization events
_pending_auth_futures = {}

GUI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../gui"))
app.mount("/static", StaticFiles(directory=os.path.join(GUI_DIR, "static")), name="static")

# Simon 'Ghost' Riley edge-tts neural voice settings
GHOST_VOICE = "en-GB-RyanNeural"
GHOST_PITCH = "-18Hz"
GHOST_RATE = "-10%"

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(GUI_DIR, "index.html"))

@app.on_event("startup")
async def startup_event():
    """Launch autonomous background watchdog patrol and warm up speech engine."""
    watchdog = AutonomousWatchdog.get_instance()
    watchdog.start_patrol()
    # Trigger initial patrol sweep asynchronously
    asyncio.create_task(watchdog.run_patrol_sweep())

@app.on_event("shutdown")
async def shutdown_event():
    watchdog = AutonomousWatchdog.get_instance()
    watchdog.stop_patrol()

@app.post("/api/watchdog/signal")
async def receive_watchdog_signal(request: Request):
    """
    Webhook endpoint for external systems, sub-agents, or mail hooks to send signals.
    Payload: { event_type, source, summary, payload, urgency }
    """
    body = await request.json()
    watchdog = AutonomousWatchdog.get_instance()
    res = await watchdog.emit_signal(
        event_type=body.get("event_type", "generic_alert"),
        source=body.get("source", "external"),
        summary=body.get("summary", "New signal received."),
        payload=body.get("payload", {}),
        urgency=body.get("urgency", "normal")
    )
    return {"status": "received", "event": res}

@app.post("/api/watchdog/patrol")
async def trigger_patrol():
    """Manually trigger an immediate operations patrol sweep."""
    watchdog = AutonomousWatchdog.get_instance()
    sweep = await watchdog.run_patrol_sweep()
    return {"status": "success", "report": sweep}

@app.get("/api/settings")
async def get_settings():
    """Retrieve active connection settings (masking API key)."""
    cfg = OmniRouteBrain.load_config()
    key = cfg.get("api_key", "")
    masked_key = (key[:4] + "..." + key[-4:]) if len(key) > 8 else ("***" if key else "")
    return {
        "base_url": cfg.get("base_url", "http://127.0.0.1:20128/v1"),
        "model": cfg.get("model", "antigravity/gemini-3.8-flash-low"),
        "thinking_level": cfg.get("thinking_level", "low"),
        "has_key": bool(key),
        "masked_key": masked_key
    }

@app.post("/api/settings")
async def save_settings(request: Request):
    """Securely save user's OmniRoute connection settings and API key."""
    try:
        body = await request.json()
        base_url = body.get("base_url", "http://127.0.0.1:20128/v1")
        api_key = body.get("api_key", "").strip()
        model = body.get("model", "antigravity/gemini-3.8-flash-low")
        thinking_level = body.get("thinking_level", "low")

        # If user didn't change the masked key, preserve existing key
        existing_cfg = OmniRouteBrain.load_config()
        if not api_key and existing_cfg.get("api_key"):
            api_key = existing_cfg["api_key"]

        OmniRouteBrain.save_config(
            base_url=base_url,
            api_key=api_key,
            model=model,
            thinking_level=thinking_level
        )
        return {"status": "success", "message": "OmniRoute connection saved securely."}
    except Exception as e:
        logger.error(f"Error saving settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tts/clauses")
async def get_tts_clauses(text: str = Query(..., description="Text to split into clauses")):
    """Split text into conversational clauses for pipelined audio synthesis."""
    clean_text = text.strip()
    if not clean_text:
        return {"clauses": []}
    return {"clauses": split_clauses(clean_text)}

@app.get("/api/tts")
async def get_tts(
    text: str = Query(..., description="Text or clause to synthesize with Simon Riley neural voice"),
    first_clause: bool = Query(False, description="Whether to optimize for time-to-first-sound by synthesizing the first clause only")
):
    """
    Synthesize text into audio using local Kokoro-82M INT8 ONNX (bm_george British voice),
    with clause streaming and automatic seamless fallback to Edge-TTS (en-GB-RyanNeural).
    """
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Empty text provided")

    # If first_clause requested, synthesize the primary clause for immediate sub-second audio output
    target_text = clean_text
    if first_clause:
        clauses = split_clauses(clean_text)
        if clauses:
            target_text = clauses[0]

    # 1. Primary: Local Kokoro-82M INT8 (British military persona bm_george)
    try:
        kokoro = KokoroSpeechEngine.get_instance()
        if kokoro.is_available():
            audio_bytes, mime = await kokoro.synthesize_wav(target_text, voice="bm_george", speed=1.05)
            if audio_bytes:
                return Response(
                    content=audio_bytes,
                    media_type=mime,
                    headers={
                        "X-Provider": kokoro.get_active_provider(),
                        "X-Clause-Count": str(len(split_clauses(clean_text)))
                    }
                )
    except Exception as e:
        logger.warning(f"Kokoro local synthesis failed: {e}. Falling back to Edge-TTS.")

    # 2. Secondary Fallback: Cloud Edge-TTS
    try:
        communicate = edge_tts.Communicate(
            target_text,
            voice=GHOST_VOICE,
            pitch=GHOST_PITCH,
            rate=GHOST_RATE
        )
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        audio_bytes = b"".join(audio_chunks)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        logger.error(f"TTS generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("GUI connected to Ghost WebSocket.")

    current_task = None
    import uuid

    # Register dynamic authorization callback over this websocket
    async def _handle_auth(description: str, action_type: str) -> bool:
        auth_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _pending_auth_futures[auth_id] = fut
        try:
            await websocket.send_json({
                "type": "auth_request",
                "auth_id": auth_id,
                "description": description,
                "action_type": action_type
            })
            return await fut
        finally:
            _pending_auth_futures.pop(auth_id, None)

    register_auth_callback(_handle_auth)

    watchdog = AutonomousWatchdog.get_instance()
    async def _on_watchdog_signal(event: dict):
        try:
            await websocket.send_json(event)
        except Exception:
            pass

    watchdog.register_broadcast_callback(_on_watchdog_signal)

    try:
        await websocket.send_json({"type": "state", "state": duplex.state.value})

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            if action == "auth_response":
                auth_id = msg.get("auth_id")
                allowed = bool(msg.get("allowed", False))
                if auth_id in _pending_auth_futures and not _pending_auth_futures[auth_id].done():
                    _pending_auth_futures[auth_id].set_result(allowed)
                continue

            if action == "speech_start":
                if duplex.handle_barge_in():
                    logger.info("Barge-in: Interrupting ongoing tasks!")
                    if current_task and not current_task.done():
                        current_task.cancel()
                    await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                    await asyncio.sleep(0.08)

                duplex.set_state(VoiceState.LISTENING)
                await websocket.send_json({"type": "state", "state": "LISTENING"})

            elif action == "speech_interim":
                transcript = msg.get("transcript", "")
                await websocket.send_json({"type": "transcript_interim", "text": transcript})

            elif action == "speech_final":
                user_text = msg.get("transcript", "").strip()
                if not user_text:
                    duplex.set_state(VoiceState.IDLE)
                    await websocket.send_json({"type": "state", "state": "IDLE"})
                    continue

                await websocket.send_json({
                    "type": "transcript_final",
                    "role": "user",
                    "text": user_text
                })

                if current_task and not current_task.done():
                    current_task.cancel()

                async def run_agent_stream(prompt: str):
                    try:
                        async for event in agent.ask_stream(prompt):
                            if event.get("type") == "state":
                                duplex.set_state(VoiceState(event["state"]))
                            await websocket.send_json(event)
                    except asyncio.CancelledError:
                        logger.info("Agent task cancelled by barge-in")
                        duplex.set_state(VoiceState.INTERRUPTED)
                        await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                    except Exception as e:
                        logger.error(f"Error in agent stream: {e}", exc_info=True)
                        await websocket.send_json({"type": "error", "message": str(e)})
                        duplex.set_state(VoiceState.IDLE)
                        await websocket.send_json({"type": "state", "state": "IDLE"})

                current_task = asyncio.create_task(run_agent_stream(user_text))

            elif action == "interrupt":
                duplex.handle_barge_in()
                if current_task and not current_task.done():
                    current_task.cancel()
                await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                await asyncio.sleep(0.12)
                duplex.set_state(VoiceState.IDLE)
                await websocket.send_json({"type": "state", "state": "IDLE"})

    except WebSocketDisconnect:
        logger.info("GUI disconnected.")
        if current_task and not current_task.done():
            current_task.cancel()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


@app.websocket("/ws/events")
async def ws_agency_events(websocket: WebSocket):
    """WebSocket endpoint for live agency event streaming."""
    import hmac
    import time
    token = websocket.query_params.get("token")
    client_host = websocket.client.host if websocket.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await websocket.close(code=1008)
        return

    expected = get_or_create_session_token()
    if not token or not hmac.compare_digest(token.strip(), expected):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Command Deck connected to /ws/events.")

    queue = asyncio.Queue()

    def _on_event(event_type: str, payload: dict):
        try:
            queue.put_nowait({"type": event_type, "payload": payload, "time": time.time()})
        except Exception:
            pass

    _event_subscribers.append(_on_event)
    try:
        while True:
            evt = await queue.get()
            await websocket.send_json(evt)
    except WebSocketDisconnect:
        logger.info("Command Deck disconnected from /ws/events.")
    finally:
        if _on_event in _event_subscribers:
            _event_subscribers.remove(_on_event)
