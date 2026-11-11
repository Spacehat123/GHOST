"""FastAPI and WebSocket bridge for Ghost Desktop GUI with OmniRoute & Neural Audio Streaming."""
import asyncio
import json
import os
import re
import logging
import time
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse, HTMLResponse
from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain
from ghost.core.token_tracker import TOKEN_STATS_PATH
from ghost.voice.duplex import DuplexCoordinator, VoiceState
from ghost.voice.kokoro import KokoroSpeechEngine, split_clauses
from ghost.watchdog.service import AutonomousWatchdog
from ghost.mcp.bridge import register_auth_callback
from ghost.server.agency_api import (
    agency_router,
    _event_subscribers,
    get_or_create_session_token,
    verify_localhost,
    ALLOWED_HOSTS,
    ALLOWED_ORIGINS
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

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
GUI_DIR = os.path.abspath(os.environ.get("GHOST_GUI_DIR") or os.path.join(REPO_DIR, "gui"))
app.mount("/static", StaticFiles(directory=os.path.join(GUI_DIR, "static")), name="static")

# Local demo previews (Operator's 11 P3): demos are served to the operator's
# browser from THIS machine only — nothing is hosted externally by this mount.
_DEMOS_MOUNT = os.environ.get("GHOST_DEMOS_DIR") or os.path.join(REPO_DIR, "demos")
os.makedirs(_DEMOS_MOUNT, exist_ok=True)
app.mount("/demos", StaticFiles(directory=_DEMOS_MOUNT), name="demos")

# Simon 'Ghost' Riley edge-tts neural voice settings
GHOST_VOICE = "en-GB-RyanNeural"
GHOST_PITCH = "-18Hz"
GHOST_RATE = "-10%"

@app.get("/")
async def get_index(request: Request):
    verify_localhost(request)
    index_path = os.path.join(GUI_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    token = get_or_create_session_token()
    meta_tag = f'<meta name="ghost-session-token" content="{token}">'
    if "<head>" in html:
        html = html.replace("<head>", f"<head>\n  {meta_tag}", 1)
    else:
        html = f"{meta_tag}\n{html}"
    return HTMLResponse(content=html, media_type="text/html")

@app.on_event("startup")
async def startup_event():
    """Launch watchdog patrol + the FULL autonomy runtime (P0-P5 wiring)."""
    # Load grant secret from secrets vault if not in environment
    try:
        from ghost.core import secrets
        sec = secrets.get("GHOST_GRANT_SECRET")
        if sec and "GHOST_GRANT_SECRET" not in os.environ:
            os.environ["GHOST_GRANT_SECRET"] = sec
    except Exception:
        pass

    watchdog = AutonomousWatchdog.get_instance()
    watchdog.start_patrol()
    # Trigger initial patrol sweep asynchronously
    asyncio.create_task(watchdog.run_patrol_sweep())
    # The body: heartbeat, scheduler, queue worker, digest (autonomy/runtime.py)
    from ghost.autonomy.runtime import get_or_start_runtime
    await get_or_start_runtime()
    # The orb narrates too (§14.2.5): spoken lines stream to the GUI.
    from ghost.voice.narrator import set_speech_broadcast
    from ghost.server.agency_api import broadcast_agency_event
    set_speech_broadcast(broadcast_agency_event)

@app.on_event("shutdown")
async def shutdown_event():
    watchdog = AutonomousWatchdog.get_instance()
    watchdog.stop_patrol()
    from ghost.autonomy.runtime import get_runtime
    rt = get_runtime()
    if rt:
        await rt.stop()

@app.post("/api/watchdog/signal")
async def receive_watchdog_signal(request: Request):
    """
    Webhook endpoint for external systems, sub-agents, or mail hooks to send signals.
    Payload: { event_type, source, summary, payload, urgency }

    Operator's 11 P6: a reply/email/intake signal is ALSO an immediate-triage
    trigger — the intake poll is enqueued as a queue job and the 1s worker
    runs it, instead of waiting for the next scheduled inbox poll.
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
    poked = False
    et = str(body.get("event_type", "")).lower()
    if et in ("reply", "reply_received", "email", "intake"):
        try:
            from ghost.core.jobqueue import JobQueue
            import uuid as _uuid
            JobQueue().enqueue("intake.email_poll", {"source": body.get("source", "signal")},
                               idempotency_key=f"intake:{_uuid.uuid4().hex}")
            poked = True
        except Exception:
            pass   # no queue reachable — the scheduled poll still covers it
    if et in ("customer_report", "site_issue"):
        # Operator's 11 P7: a reported break becomes a fixer job (staged, never auto-applied)
        try:
            from ghost.core.jobqueue import JobQueue
            import uuid as _uuid
            payload = dict(body.get("payload") or {})
            payload.setdefault("source", "signal")
            payload.setdefault("key", f"issue-{_uuid.uuid4().hex[:8]}")
            JobQueue().enqueue("fixer.customer_report", payload,
                               idempotency_key=f"fixer:{payload['key']}")
            poked = True
        except Exception:
            pass
    return {"status": "received", "event": res, "intake_poked": poked}

@app.post("/api/watchdog/patrol")
async def trigger_patrol():
    """Manually trigger an immediate operations patrol sweep."""
    watchdog = AutonomousWatchdog.get_instance()
    sweep = await watchdog.run_patrol_sweep()
    return {"status": "success", "report": sweep}

# ------------------------- T9: human-reserved act packages -------------------

@app.get("/api/reserved")
async def reserved_list(state: str = None):
    """The operator's one-screen queue: every package awaiting a click."""
    from ghost.control.reserved import list_packages
    return {"packages": list_packages(state=state)}


@app.post("/api/reserved/{pid}/approve")
async def reserved_approve(pid: str):
    """THE one action on a finished package. Operator-only: this endpoint is
    the sole authority that mints an operator grant and executes once."""
    from ghost.control.reserved import approve
    out = approve(pid)
    if "error" in out:
        raise HTTPException(status_code=409, detail=out["error"])
    return out


@app.post("/api/reserved/{pid}/dismiss")
async def reserved_dismiss(pid: str):
    from ghost.control.reserved import dismiss
    out = dismiss(pid)
    if "error" in out:
        raise HTTPException(status_code=409, detail=out["error"])
    return out

# ------------------------- T9: control-plane settings ------------------------

_SETTING_SECTIONS = {
    "razorpay": ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"],
    "smtp": ["GHOST_OUTREACH_SMTP_HOST", "GHOST_OUTREACH_SMTP_PORT",
             "GHOST_OUTREACH_SMTP_USER", "GHOST_OUTREACH_SMTP_PASSWORD",
             "GHOST_OUTREACH_FROM", "GHOST_OUTREACH_REPLY_TO"],
    "domain": ["GHOST_SENDING_DOMAIN"],
    "authority": ["GHOST_GRANT_SECRET"],
    # Operator's 11 P5: the reply-intake inbox — read-only IMAP, vault-first
    "email": ["GHOST_INBOX_EMAIL", "GHOST_IMAP_HOST", "GHOST_IMAP_PORT",
              "GHOST_IMAP_USER", "GHOST_IMAP_PASSWORD"],
}


def _mask(value: Optional[str]) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


@app.get("/api/settings/sections")
async def settings_sections():
    """Every operator-configurable section, secrets MASKED (last 4 only).
    The operator never edits config files — this is the surface."""
    from ghost.core.secrets import get, SETTABLE_SECRETS
    from ghost.core.dnscheck import check_domain_deliverability
    sections = {}
    for name, keys in _SETTING_SECTIONS.items():
        sections[name] = {k: {"set": bool(get(k)), "masked": _mask(get(k))}
                          for k in keys}
    domain = get("GHOST_SENDING_DOMAIN")
    sections["domain"]["dns"] = (check_domain_deliverability(domain)
                                 if domain else {"note": "no sending domain set"})
    sections["settable"] = list(SETTABLE_SECRETS)
    return sections


@app.post("/api/settings/section/{section}")
async def settings_put(section: str, request: Request):
    """Write one section through the control plane into the secrets vault.
    Only SETTABLE_SECRETS names pass; everything is audited as operator.*"""
    from ghost.core.secrets import set_secret, SETTABLE_SECRETS
    if section not in _SETTING_SECTIONS:
        raise HTTPException(status_code=404, detail=f"unknown section {section}")
    body = await request.json()
    written, refused = [], []
    for key, value in (body or {}).items():
        if key not in _SETTING_SECTIONS[section] or key not in SETTABLE_SECRETS:
            refused.append(key)
            continue
        if value in (None, ""):
            continue   # empty = keep existing (masked-value round-trip safety)
        if set_secret(key, str(value)):
            written.append(key)
        else:
            refused.append(key)
    if refused:
        raise HTTPException(status_code=400, detail=f"refused: {refused}")
    return {"status": "success", "written": written}


@app.post("/api/settings/test/{target}")
async def settings_test(target: str):
    """Live test-connection buttons: verify the integration BEFORE relying on
    it. Every result is honest — not_configured is a state, not an error."""
    from ghost.core.secrets import get
    if target == "razorpay":
        key_id, key_secret = get("RAZORPAY_KEY_ID"), get("RAZORPAY_KEY_SECRET")
        if not (key_id and key_secret):
            return {"ok": False, "state": "not_configured"}
        import base64
        import urllib.request
        try:
            auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
            req = urllib.request.Request("https://api.razorpay.com/v1/payments?count=1",
                                         headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return {"ok": r.status == 200, "state": "ok" if r.status == 200 else "error"}
        except Exception as e:
            return {"ok": False, "state": "error", "error": str(e)[:150]}
    if target == "smtp":
        from ghost.autonomy.sender import smtp_configured
        if not smtp_configured():
            return {"ok": False, "state": "not_configured"}
        import smtplib
        import ssl
        try:
            with smtplib.SMTP(get("GHOST_OUTREACH_SMTP_HOST"),
                              int(get("GHOST_OUTREACH_SMTP_PORT") or 587),
                              timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(get("GHOST_OUTREACH_SMTP_USER"),
                             get("GHOST_OUTREACH_SMTP_PASSWORD"))
            return {"ok": True, "state": "ok"}
        except Exception as e:
            return {"ok": False, "state": "error", "error": str(e)[:150]}
    if target == "domain":
        domain = get("GHOST_SENDING_DOMAIN")
        if not domain:
            return {"ok": False, "state": "not_configured"}
        from ghost.core.dnscheck import warmup_may_start
        gate = warmup_may_start(domain)
        return {"ok": bool(gate.get("allowed")), "state": "ok" if gate.get("allowed")
                else "incomplete", "missing": gate.get("missing", [])}
    if target == "ollama":
        from ghost.core.model_routing import check_local_model
        probe = check_local_model()
        return {"ok": probe["status"] == "available", "state": probe["status"],
                "models": probe.get("models", [])[:5]}
    if target == "email":
        # Operator's 11 P5: reply-intake inbox — read-only IMAP login probe
        from ghost.comms.email_service import EmailService
        if not EmailService.get_instance().imap_configured():
            return {"ok": False, "state": "not_configured"}
        try:
            msgs = EmailService.get_instance().check_inbox(unread_only=True, limit=1)
            return {"ok": True, "state": "ok", "unread_sample": len(msgs)}
        except Exception as e:
            return {"ok": False, "state": "error", "error": str(e)[:150]}
    raise HTTPException(status_code=404, detail=f"unknown test target {target}")


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

@app.get("/api/tts/voices")
async def get_tts_voices():
    """List available installed Kokoro voices and current active voice."""
    kokoro = KokoroSpeechEngine.get_instance()
    voices = kokoro.get_installed_voices()
    current_voice = os.environ.get("KOKORO_VOICE", getattr(kokoro, "voice", "af_nicole"))
    return {
        "current": current_voice,
        "voices": voices
    }

@app.post("/api/tts/voice")
async def set_tts_voice(request: Request):
    """Set the active Kokoro voice persona."""
    data = await request.json()
    new_voice = data.get("voice", "").strip()
    if not new_voice:
        raise HTTPException(status_code=400, detail="Voice name required")
    os.environ["KOKORO_VOICE"] = new_voice
    kokoro = KokoroSpeechEngine.get_instance()
    kokoro.voice = new_voice
    return {"status": "success", "voice": new_voice}

@app.get("/api/tts/clauses")
async def get_tts_clauses(text: str = Query(..., description="Text to split into clauses")):
    """Split text into conversational clauses for pipelined audio synthesis."""
    clean_text = text.strip()
    if not clean_text:
        return {"clauses": []}
    return {"clauses": split_clauses(clean_text)}

@app.get("/api/tts")
async def get_tts(
    text: str = Query(..., description="Text or clause to synthesize"),
    first_clause: bool = Query(False, description="Whether to optimize for time-to-first-sound by synthesizing the first clause only"),
    voice: Optional[str] = Query(None, description="Kokoro voice identifier (e.g. af_aoede, bm_george)")
):
    """
    Synthesize text into audio using local Kokoro-82M ONNX (default af_aoede),
    with clause streaming and automatic seamless fallback to Edge-TTS.
    """
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Empty text provided")

    target_voice = voice or os.environ.get("KOKORO_VOICE", "af_nicole")

    # If first_clause requested, synthesize the primary clause for immediate sub-second audio output
    target_text = clean_text
    if first_clause:
        clauses = split_clauses(clean_text)
        if clauses:
            target_text = clauses[0]

    # 1. Primary: Local Kokoro ONNX
    import time
    t0 = time.perf_counter()
    try:
        kokoro = KokoroSpeechEngine.get_instance()
        if kokoro.is_available():
            audio_bytes, mime = await kokoro.synthesize_wav(target_text, voice=target_voice, speed=1.05)
            synth_ms = int((time.perf_counter() - t0) * 1000)
            if audio_bytes:
                return Response(
                    content=audio_bytes,
                    media_type=mime,
                    headers={
                        "X-Provider": kokoro.get_active_provider(),
                        "X-Synth-Ms": str(synth_ms),
                        "X-Clause-Count": str(len(split_clauses(clean_text)))
                    }
                )
    except Exception as e:
        logger.warning(f"Kokoro local synthesis failed: {e}. Falling back to Edge-TTS.")

    # 2. Secondary Fallback: Cloud Edge-TTS
    try:
        fallback_voice = "en-US-JennyNeural" if target_voice.startswith("af_") else ("en-US-GuyNeural" if target_voice.startswith("am_") else GHOST_VOICE)
        fallback_pitch = "+0Hz" if target_voice.startswith("a") else GHOST_PITCH
        fallback_rate = "+0%" if target_voice.startswith("a") else GHOST_RATE
        communicate = edge_tts.Communicate(
            target_text,
            voice=fallback_voice,
            pitch=fallback_pitch,
            rate=fallback_rate
        )
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        synth_ms = int((time.perf_counter() - t0) * 1000)
        audio_bytes = b"".join(audio_chunks)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "X-Provider": "EdgeTTS",
                "X-Synth-Ms": str(synth_ms),
                "X-Clause-Count": str(len(split_clauses(clean_text)))
            }
        )
    except Exception as e:
        logger.error(f"TTS generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_host = websocket.client.host if websocket.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await websocket.close(code=1008)
        return

    host_header = websocket.headers.get("host", "").strip().lower()
    if not host_header or host_header not in ALLOWED_HOSTS:
        await websocket.close(code=1008)
        return

    origin = websocket.headers.get("origin")
    if origin and origin.lower().rstrip("/") not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return

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

        if os.path.exists(TOKEN_STATS_PATH):
            try:
                with open(TOKEN_STATS_PATH, "r", encoding="utf-8") as f:
                    t_data = json.load(f)
                    await websocket.send_json({
                        "type": "token_stats",
                        "last": t_data.get("last", 0),
                        "cached_tokens": t_data.get("last_cached", 0),
                        "today": t_data.get("today", 0),
                        "today_cached": t_data.get("today_cached", 0)
                    })
            except Exception:
                pass

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

    client_host = websocket.client.host if websocket.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await websocket.close(code=1008)
        return

    host_header = websocket.headers.get("host", "").strip().lower()
    if not host_header or host_header not in ALLOWED_HOSTS:
        await websocket.close(code=1008)
        return

    origin = websocket.headers.get("origin")
    if origin and origin.lower().rstrip("/") not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return

    token = websocket.query_params.get("token")
    expected = get_or_create_session_token()
    if not token or not hmac.compare_digest(token.strip(), expected):
        await websocket.close(code=1008)
        return
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

    from ghost.core.events import get_event_bus
    bus = get_event_bus()

    def _on_bus_event(evt):
        try:
            queue.put_nowait({
                "type": "universal_event",
                "event_type": evt.event_type,
                "source": evt.source,
                "severity": evt.severity.value,
                "payload": evt.payload,
                "timestamp": evt.timestamp,
                "event_id": evt.event_id,
            })
        except Exception:
            pass

    bus_sub_id = bus.subscribe("*", _on_bus_event)

    try:
        while True:
            evt = await queue.get()
            await websocket.send_json(evt)
    except WebSocketDisconnect:
        logger.info("Command Deck disconnected from /ws/events.")
    finally:
        bus.unsubscribe(bus_sub_id)
        if _on_event in _event_subscribers:
            _event_subscribers.remove(_on_event)


# ============================================================ Core Foundation APIs
# Universal Intelligence APIs for Awareness, Agents, Projects, Memory, Control, and Events

@app.get("/api/system/state")
async def get_system_state():
    """Returns real-time snapshot of system vitals, desktop context, and active agents."""
    from ghost.core.system_state import get_system_state_manager
    mgr = get_system_state_manager()
    return mgr.get_snapshot().to_dict()


@app.get("/api/awareness/telemetry")
async def get_awareness_telemetry():
    """Returns detailed telemetry including hardware vitals, git state, and recent anomalies."""
    from ghost.core.system_state import get_system_state_manager
    from ghost.collectors.git_collector import GitCollector
    from ghost.core.events import get_event_bus, EventSeverity
    from ghost.paths import repo_root

    mgr = get_system_state_manager()
    vitals = mgr.get_snapshot().vitals.to_dict()

    git_coll = GitCollector(repo_paths=[repo_root()])
    repo_state = git_coll.sample_repo(repo_root())

    bus = get_event_bus()
    alerts = bus.query(limit=10, min_severity=EventSeverity.WARNING)

    return {
        "vitals": vitals,
        "desktop": {
            "focused_window": mgr.get_snapshot().focused_window,
            "active_workspace": mgr.get_snapshot().active_workspace,
        },
        "git": repo_state.to_dict() if repo_state else None,
        "recent_alerts": [a.to_dict() for a in alerts],
    }


@app.get("/api/agents")
async def list_agents():
    """Returns all specialized domain agents with their status and capabilities."""
    from ghost.agents import get_available_agents
    from ghost.core.system_state import get_system_state_manager
    mgr = get_system_state_manager()
    active = set(mgr.get_snapshot().active_agents)
    agents = []
    for a in get_available_agents():
        agents.append({
            "agent_id": a.agent_id,
            "name": a.name,
            "responsibility": a.responsibility,
            "supported_tasks": a.supported_tasks,
            "required_permissions": a.required_permissions,
            "status": "ACTIVE" if a.name in active else "STANDBY",
        })
    return {"agents": agents, "count": len(agents)}


@app.post("/api/agents/{agent_id}/plan")
async def plan_agent_task(agent_id: str, request: Request):
    """Decomposes an objective into atomic tasks using a specialized agent."""
    from ghost.agents import get_available_agents
    body = await request.json()
    matched = next((a for a in get_available_agents() if a.agent_id == agent_id), None)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    tasks = await matched.plan_task(body)
    return {"agent_id": agent_id, "tasks": [t.to_dict() for t in tasks]}


@app.post("/api/agents/{agent_id}/execute")
async def execute_agent_task(agent_id: str, request: Request):
    """Executes a task with a specialized agent and produces verification evidence."""
    from ghost.agents import get_available_agents
    from ghost.core.task_orchestrator import Task
    body = await request.json()
    matched = next((a for a in get_available_agents() if a.agent_id == agent_id), None)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    task = Task.from_dict(body.get("task", body))
    context = body.get("context", {})
    output = await matched.execute_task(task, context)
    evidence = await matched.verify_outcome(task, output)
    return {
        "agent_id": agent_id,
        "task_id": task.task_id,
        "output": output,
        "evidence": evidence.to_dict(),
    }


@app.get("/api/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 100):
    """Returns tasks managed by TaskOrchestrator."""
    from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
    orch = TaskOrchestrator()
    st = TaskStatus(status) if status else None
    tasks = orch.list_tasks(status=st, limit=limit)
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}


@app.post("/api/tasks")
async def create_task(request: Request):
    """Creates a new task in TaskOrchestrator."""
    from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
    body = await request.json()
    orch = TaskOrchestrator()
    task = orch.submit_task(
        objective_id=body.get("objective_id", "manual"),
        assigned_agent=body.get("assigned_agent", "general"),
        title=body.get("title", "Operator Task"),
        description=body.get("description", ""),
        input_payload=body.get("input_payload", {}),
        required_permissions=body.get("required_permissions", []),
        timeout_s=float(body.get("timeout_s", 300.0)),
        initial_status=TaskStatus(body.get("status", TaskStatus.QUEUED.value)),
    )
    return {"task": task.to_dict()}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancels an in-progress or queued task."""
    from ghost.core.task_orchestrator import TaskOrchestrator, TaskStatus
    orch = TaskOrchestrator()
    task = orch.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    try:
        updated = orch.transition(task_id, TaskStatus.CANCELLED, error_reason="Operator cancelled")
        return {"task_id": task_id, "cancelled": True, "status": updated.status.value}
    except Exception as e:
        return {"task_id": task_id, "cancelled": False, "error": str(e)}


@app.get("/api/projects")
async def list_projects():
    """Returns workspace git status, active missions, and queue jobs."""
    from ghost.collectors.git_collector import GitCollector
    from ghost.core.mission import MissionStore
    from ghost.core.jobqueue import JobQueue
    from ghost.paths import repo_root

    git_coll = GitCollector(repo_paths=[repo_root()])
    git_state = git_coll.sample_repo(repo_root())

    missions = []
    try:
        mstore = MissionStore.get_instance()
        active = mstore.active_mission()
        if active:
            missions.append({
                "id": active.id,
                "goal": active.goal,
                "status": active.status,
                "tasks_count": len(active.tasks),
            })
    except Exception:
        pass

    q_stats = {}
    try:
        q_stats = JobQueue().stats()
    except Exception:
        pass

    return {
        "repository": {
            "path": repo_root(),
            "branch": git_state.branch if git_state else "unknown",
            "commit": git_state.head_commit if git_state else "unknown",
            "is_dirty": git_state.is_dirty if git_state else False,
            "modified_files": git_state.modified_files if git_state else [],
            "untracked_files": git_state.untracked_files if git_state else [],
        },
        "active_missions": missions,
        "queue_stats": q_stats,
    }


@app.get("/api/memory/search")
async def search_memory(q: str = Query("")):
    """Searches memory directives and facts."""
    from ghost.core.memory import CoreMemoryStore
    from ghost.core.follow import DirectiveManager

    results = {
        "query": q,
        "directives": [],
        "facts": [],
    }
    try:
        dirs = DirectiveManager.list_all()
        if q:
            results["directives"] = [d for d in dirs if q.lower() in d.get("text", "").lower()]
        else:
            results["directives"] = dirs
    except Exception:
        pass

    try:
        mem = CoreMemoryStore()
        data = mem.load()
        facts = data.get("facts", [])
        if q:
            results["facts"] = [f for f in facts if q.lower() in str(f).lower()]
        else:
            results["facts"] = facts
    except Exception:
        pass

    return results


@app.get("/api/memory/directives")
async def list_directives():
    from ghost.core.follow import DirectiveManager
    return {"directives": DirectiveManager.list_all()}


@app.post("/api/memory/directives")
async def add_directive(request: Request):
    from ghost.core.follow import DirectiveManager
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Directive text required")
    res = DirectiveManager.add_directive(text)
    return {"status": "added", "directive": res}


@app.get("/api/tools")
async def list_tools():
    """Returns registered tools and parameter schemas."""
    from ghost.core.tool_registry import get_tool_registry
    reg = get_tool_registry()
    tools = []
    for t in reg.list_tools():
        tools.append({
            "name": t.name,
            "description": t.description,
            "is_side_effect": t.is_side_effect,
            "timeout_s": t.timeout_s,
            "parameters": t.parameters_schema,
        })
    return {"tools": tools, "count": len(tools)}


@app.get("/api/universal/events")
async def query_universal_events(
    prefix: Optional[str] = Query(None),
    min_severity: Optional[str] = Query(None),
    limit: int = Query(50),
):
    """Queries chronological events from Universal EventBus."""
    from ghost.core.events import get_event_bus, EventSeverity
    bus = get_event_bus()
    sev = EventSeverity.from_str(min_severity) if min_severity else None
    events = bus.query(limit=limit, event_type_prefix=prefix, min_severity=sev)
    return {"events": [e.to_dict() for e in events], "count": len(events)}


# ============================================================
# Phase 5-8: Proactive Intelligence, Objectives & Cross-Domain API
# ============================================================

@app.get("/api/objectives")
async def list_objectives(status: Optional[str] = None, domain: Optional[str] = None):
    """Lists high-level operator objectives across all 7 intelligence domains."""
    from ghost.intelligence.objective_manager import get_objective_manager
    mgr = get_objective_manager()
    objs = mgr.list_objectives(status=status, domain=domain)
    return {"objectives": [o.to_dict() for o in objs], "count": len(objs)}


@app.post("/api/objectives")
async def create_objective(request: Request):
    """Creates a new long-term objective in ObjectiveManager."""
    from ghost.intelligence.objective_manager import get_objective_manager
    body = await request.json()
    mgr = get_objective_manager()
    obj = mgr.create_objective(
        title=body.get("title", "Operator Objective"),
        domain=body.get("domain", "system"),
        target_agent=body.get("target_agent", "system_agent"),
        description=body.get("description", ""),
        priority=int(body.get("priority", 3)),
        criteria=body.get("criteria", []),
    )
    return {"status": "created", "objective": obj.to_dict()}


@app.post("/api/objectives/{objective_id}/execute")
async def execute_objective(objective_id: str):
    """Decomposes an objective and submits tasks for autonomous background execution."""
    from ghost.autonomy.task_worker import get_autonomous_execution_engine
    engine = get_autonomous_execution_engine()
    res = await engine.execute_objective(objective_id)
    return res


@app.get("/api/intelligence/mode")
async def get_operating_mode():
    """Returns the current GHOST Operating Mode (AMBIENT, OBJECTIVE, REACTIVE)."""
    from ghost.core.system_state import get_system_state_manager
    mgr = get_system_state_manager()
    return {"mode": mgr.get_snapshot().operating_mode}


@app.post("/api/intelligence/mode")
async def set_operating_mode(request: Request):
    """Sets GHOST Operating Mode (AMBIENT, OBJECTIVE, REACTIVE)."""
    from ghost.core.system_state import get_system_state_manager
    body = await request.json()
    mode = body.get("mode", "AMBIENT")
    mgr = get_system_state_manager()
    mgr.set_operating_mode(mode)
    return {"status": "ok", "mode": mgr.get_snapshot().operating_mode}


@app.get("/api/intelligence/decisions")
async def get_proactive_decisions():
    """Returns recent proactive prioritization decisions and evidence."""
    from ghost.intelligence.proactive_orchestrator import get_proactive_orchestrator
    orch = get_proactive_orchestrator()
    decisions = orch.get_recent_decisions()
    return {"decisions": [d.to_dict() for d in decisions], "count": len(decisions)}


@app.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: str):
    """Approves a high-risk task waiting in WAITING_FOR_APPROVAL status."""
    from ghost.core.task_orchestrator import get_task_orchestrator, TaskStatus
    orch = get_task_orchestrator()
    try:
        updated = orch.transition(task_id, TaskStatus.QUEUED)
        return {"task_id": task_id, "approved": True, "status": updated.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tasks/{task_id}/reject")
async def reject_task(task_id: str, request: Request):
    """Rejects a task waiting for approval."""
    from ghost.core.task_orchestrator import get_task_orchestrator, TaskStatus
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    reason = body.get("reason", "Operator rejected task")
    orch = get_task_orchestrator()
    try:
        updated = orch.transition(task_id, TaskStatus.CANCELLED, error_reason=reason)
        return {"task_id": task_id, "rejected": True, "status": updated.status.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/physical/states")
async def get_physical_states():
    """Queries smart home entities and sensors from Home Assistant."""
    from ghost.integrations.homeassistant import HomeAssistantClient
    client = HomeAssistantClient()
    states = await client.get_states()
    return {"states": states, "configured": client.is_configured}


@app.post("/api/physical/call")
async def call_physical_service(request: Request):
    """Calls a Home Assistant service with state verification."""
    from ghost.integrations.homeassistant import HomeAssistantClient
    body = await request.json()
    client = HomeAssistantClient()
    domain = body.get("domain", "switch")
    service = body.get("service", "toggle")
    data = body.get("data", {})
    res = await client.call_service(domain, service, data)
    return res


@app.post("/api/creative/scad")
async def render_scad_model(request: Request):
    """Generates and renders an OpenSCAD 3D model."""
    from ghost.agents.creative_agent import CreativeIntelligenceAgent
    from ghost.core.task_orchestrator import Task, TaskStatus
    body = await request.json()
    agent = CreativeIntelligenceAgent()
    mock_task = Task(
        task_id="tsk-api-scad",
        objective_id="obj-api",
        assigned_agent="creative_agent",
        title="API CAD Render",
        description="",
        status=TaskStatus.RUNNING,
        input_payload={
            "action": "generate_scad_model",
            "model_name": body.get("model_name", "model"),
            "parameters": body.get("parameters", {}),
        },
    )
    result = await agent.execute_task(mock_task, {})
    evidence = await agent.verify_outcome(mock_task, result)
    return {"result": result, "evidence": evidence.to_dict()}


@app.get("/api/settings/operator")
async def get_operator_settings():
    """Returns configuration status of external services with masked secrets."""
    from ghost.core import secrets
    keys = [
        "HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN",
        "MQTT_BROKER_HOST", "MQTT_BROKER_PORT", "MQTT_USERNAME", "MQTT_PASSWORD",
        "OPENSCAD_BINARY_PATH", "BLENDER_BINARY_PATH",
    ]
    settings = {}
    for k in keys:
        val = secrets.get(k)
        if not val:
            settings[k] = {"configured": False, "masked": None}
        else:
            if "TOKEN" in k or "PASSWORD" in k:
                masked = val[:3] + "..." + val[-3:] if len(val) > 6 else "***"
            else:
                masked = val
            settings[k] = {"configured": True, "masked": masked}
    return {"settings": settings}


@app.post("/api/settings/operator")
async def update_operator_settings(request: Request):
    """Updates external service configuration in secrets vault."""
    from ghost.core import secrets
    body = await request.json()
    updated = []
    for k, v in body.items():
        if k in secrets.SETTABLE_SECRETS:
            if secrets.set_secret(k, str(v)):
                updated.append(k)
    return {"status": "ok", "updated": updated}


# --------------------- Deterministic Agent Routing & Context Isolation APIs ---------------------

@app.get("/api/router/agents")
async def get_router_agents():
    """Returns canonical agent descriptors, capabilities, and health status for all 7 domains."""
    from ghost.core.agent_registry import get_agent_registry
    registry = get_agent_registry()
    return {
        "agents": [d.to_dict() for d in registry.list_descriptors()],
        "count": len(registry.list_descriptors()),
    }


@app.post("/api/router/classify")
async def classify_router_intent(request: Request):
    """Deterministically classifies user intent and returns a structured RoutingDecision."""
    from ghost.core.task_router import get_task_router
    body = await request.json()
    query = body.get("query", "")
    router = get_task_router()
    decision = router.classify_and_route(query, context=body.get("context"))
    return decision.to_dict()


@app.post("/api/router/dispatch")
async def dispatch_routed_workflow(request: Request):
    """Dispatches a multi-agent or single-agent routed workflow in isolated context."""
    from ghost.core.task_router import get_task_router, IntentCategory
    body = await request.json()
    query = body.get("query", "")
    router = get_task_router()
    decision = router.classify_and_route(query, context=body.get("context"))

    if decision.intent_category == IntentCategory.CAPABILITY_QUERY:
        return {
            "status": "CAPABILITY_EXPLAINED",
            "decision": decision.to_dict(),
            "events": [{"type": "capability_explanation", "explanation": decision.capability_explanation}],
        }
    elif decision.intent_category == IntentCategory.TASK_PLANNING:
        return {
            "status": "PLAN_GENERATED",
            "decision": decision.to_dict(),
            "events": [{"type": "execution_plan", "plan": decision.execution_plan}],
        }
    elif decision.intent_category == IntentCategory.TASK_CLARIFICATION:
        return {
            "status": "CLARIFICATION_REQUIRED",
            "decision": decision.to_dict(),
            "events": [{"type": "clarification_question", "question": decision.clarification_question}],
        }

    events = []
    async for evt in router.dispatch_workflow(decision, context=body.get("context")):
        events.append(evt)

    return {
        "status": "COMPLETED" if any(e.get("status") == "COMPLETED" for e in events) else "INCOMPLETE",
        "decision": decision.to_dict(),
        "events": events,
    }


@app.get("/api/router/chains")
async def get_delegation_chains():
    """Returns active and recent parent-child delegation chains for UI visualization."""
    from ghost.core.task_orchestrator import get_task_orchestrator
    orch = get_task_orchestrator()
    tasks = orch.list_tasks(limit=50)

    # Group tasks by parent_task_id or objective_id
    chains: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        parent_id = t.input_payload.get("parent_task_id") or t.objective_id or "root"
        if parent_id not in chains:
            chains[parent_id] = []
        chains[parent_id].append({
            "task_id": t.task_id,
            "title": t.title,
            "status": t.status.value,
            "assigned_agent": t.assigned_agent,
            "created_at": t.created_at,
            "output": t.output_payload,
            "verification": (t.output_payload or {}).get("verification", {}),
        })

    return {"chains": chains, "total_tasks": len(tasks)}


# ============================================================ Autonomy routes
# The P0-P5 organs, exposed to the operator GUI. Halt is fail-closed server-side.

@app.get("/api/runtime/status")
async def runtime_status():
    """One-screen answer: is the body alive, halted, heartbeat fresh, queue state."""
    from ghost.autonomy.runtime import get_runtime
    rt = get_runtime()
    if not rt:
        return {"running": False, "note": "runtime not started"}
    return rt.status


@app.post("/api/halt")
async def api_halt(request: Request):
    """The throat-cut handle (§0.5): freeze ALL outbound now."""
    from ghost.core import halt as halt_mod
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    meta = halt_mod.trip(reason=body.get("reason") or halt_mod.REASON_OPERATOR, by="operator")
    return {"status": "halted", "meta": meta}


@app.post("/api/resume")
async def api_resume():
    from ghost.core import halt as halt_mod
    prev = halt_mod.resume(by="operator")
    return {"status": "resumed", "was_halted": bool(prev), "previous": prev}


@app.get("/api/digest")
async def api_digest(day: str = None):
    from ghost.autonomy.digest import build_digest
    from ghost.core.audit import AuditLedger
    return build_digest(day=day, ledger=AuditLedger.get_instance())


@app.get("/api/metrics")
async def api_metrics():
    from ghost.core.metrics import compute_metrics
    return compute_metrics()


@app.get("/api/ladder")
async def api_ladder():
    from ghost.autonomy.ladder import AutonomyLadder
    return AutonomyLadder().status()


@app.post("/api/ladder/raise")
async def api_ladder_raise(request: Request):
    """Operator-only promotion to an EARNED ceiling (§1.2). Ghost cannot call this itself."""
    from ghost.autonomy.ladder import AutonomyLadder
    body = await request.json()
    res = AutonomyLadder().raise_level(body.get("capability", ""), by="operator")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "cannot raise"))
    return res


@app.post("/api/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Real payment webhook: verify signature -> parse/dedupe -> amount-match
    -> chain contract+welcome. Unverified bodies are refused, never processed."""
    from ghost.workers.payments import get_payment_provider
    from ghost.workers.close_chain import process_payment_webhook
    from ghost.core.jobqueue import JobQueue
    from ghost.crm.db import CRMDatabase

    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    provider = get_payment_provider("razorpay")

    def invoice_lookup(invoice_number: str):
        crm = CRMDatabase.get_instance()
        doc = crm.get_document_by_invoice(invoice_number) if hasattr(crm, "get_document_by_invoice") else None
        if doc:
            return doc
        # Fall back to the invoice template contract record in documents table.
        return None

    result = process_payment_webhook(provider, headers, body,
                                     invoice_lookup, JobQueue().enqueue)
    status_code = 200 if result.get("status") in ("verified", "duplicate") else 400
    return JSONResponse(status_code=status_code, content=result)


@app.post("/api/leads/{slug}/contact")
async def lead_contact_form(slug: str, request: Request):
    """The leads inbox (the Webild steal): a visitor's inquiry from a
    Ghost-built site. Public but machine-local (verify_localhost — same rule
    that serves the demos). The message is UNTRUSTED: it goes through the T7
    quarantine (inside ingest_reply) before any bookkeeping, the submission is
    deduped, and the operator is pinged on every real one."""
    import hashlib
    from ghost.crm.db import CRMDatabase
    from ghost.autonomy.reply_loop import ingest_reply
    from ghost.workers.suppression import SuppressionManager
    from ghost.autonomy.ladder import AutonomyLadder
    from ghost.core.audit import audit as ledger_audit

    verify_localhost(request)
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise HTTPException(status_code=400, detail="bad slug")

    form = await request.form()
    if form.get("company_website"):
        return {"status": "ignored"}  # honeypot: bots get 200 and nothing else
    name = str(form.get("name", "")).strip()[:120]
    contact = str(form.get("contact", "")).strip()[:200]
    message = str(form.get("message", "")).strip()[:2000]
    if not message or not (name or contact):
        raise HTTPException(status_code=400, detail="name/contact and message are required")

    crm = CRMDatabase.get_instance()
    lead = crm.get_lead(slug)
    if not lead:
        raise HTTPException(status_code=404,
                            detail=f"No lead {slug!r} — a form names its own site; never invent one.")

    dedup_id = "form:" + hashlib.sha256(
        f"{slug}|{name}|{contact}|{message}".encode("utf-8")).hexdigest()
    if crm.has_processed_event(dedup_id):
        return {"status": "duplicate", "slug": slug}
    crm.record_processed_event(dedup_id, "web_form")

    body_text = f"Website inquiry from {name or 'a visitor'} <{contact}>: {message}"
    verdict_out = ingest_reply(slug, body_text,
                               suppression=SuppressionManager(crm=crm),
                               crm=crm, ladder=AutonomyLadder())
    quarantined = bool(verdict_out.get("quarantined"))

    # Remember the visitor's address for future matching — unless the text
    # was quarantined (untrusted content never writes into the CRM profile).
    if contact and "@" in contact and not quarantined:
        try:
            lead_data = dict(lead)
            if not lead_data.get("email"):
                lead_data["email"] = contact.strip()
                crm.add_or_update_lead(lead_data)
        except Exception:
            pass

    ledger_audit("lead.form_submission", subject=slug,
                 name=name, contact=contact, quarantined=quarantined,
                 ping="(c) unverifiable: new website form lead — review in Pipeline")
    try:
        from ghost.server.agency_api import broadcast_agency_event
        broadcast_agency_event("lead_form_submission", {"slug": slug, "name": name})
    except Exception:
        pass
    return {"status": "quarantined" if quarantined else "received", "slug": slug}


@app.get("/api/retainers")
async def api_retainers():
    from ghost.workers.retainers import RetainerStore
    store = RetainerStore()
    return {"sites": store.list_sites(),
            "open_incidents": store.open_incidents(),
            "over_slo": store.over_slo_incident_count()}


@app.post("/api/retainers/{slug}/monitor")
async def api_retainer_monitor(slug: str):
    """Run the full check battery on one retainer site now; open incidents for
    failures and dispatch auto-fix through the ladder gates."""
    from ghost.workers.retainers import RetainerStore, AutoFixer
    from ghost.workers.snapshots import SnapshotStore
    from ghost.workers.site_monitor import CHECKS
    from ghost.autonomy.ladder import AutonomyLadder
    from ghost.core.audit import audit

    store = RetainerStore()
    site = store.get_site(slug)
    if not site:
        raise HTTPException(status_code=404, detail=f"unknown retainer site '{slug}'")

    results = {}
    for name, check in CHECKS.items():
        try:
            if name == "uptime":
                results[name] = check(site["url"])
            elif name == "ssl":
                results[name] = check(site["url"])
            elif name == "seo_drift":
                results[name] = check(site["url"])
            elif name == "broken_links":
                results[name] = check(site["url"])
        except Exception as e:
            results[name] = {"ok": False, "detail": str(e)[:200], "evidence": {}}

    severity_map = {"uptime": "t1", "ssl": "t2", "broken_links": "t2", "seo_drift": "t3"}
    incidents = []
    for name, res in results.items():
        audit(f"monitor.{name}", subject=slug, ok=res.get("ok"),
              detail=res.get("detail", "")[:150])
        if not res.get("ok"):
            iid = store.open_incident(slug, name, severity_map.get(name, "t3"),
                                      res.get("detail", ""), res.get("evidence", {}))
            inc = [i for i in store.open_incidents(slug) if i["id"] == iid]
            if inc:
                fixer = AutoFixer(store, SnapshotStore(), AutonomyLadder())
                fix_result = fixer.dispatch(inc[0])
                incidents.append({"incident_id": iid, "check": name, "fix": fix_result})
    return {"slug": slug, "checks": results, "incidents_opened": incidents}


@app.post("/api/voice/interrupt")
async def api_voice_interrupt():
    """GUI barge-in: stop speaking AND stop working at the next yield (§14.2.6)."""
    runner = getattr(app.state, "task_runner", None)
    if runner:
        runner.interrupt()
        return {"status": "interrupted"}
    return {"status": "no_active_runner"}
