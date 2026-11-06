"""FastAPI and WebSocket bridge for Ghost Desktop GUI with OmniRoute & Neural Audio Streaming."""
import asyncio
import json
import os
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse, HTMLResponse
from ghost.core.agent import GhostAgent
from ghost.core.brain import OmniRouteBrain
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

    # 1. Primary: Local Kokoro ONNX (British military persona bm_george)
    import time
    t0 = time.perf_counter()
    try:
        kokoro = KokoroSpeechEngine.get_instance()
        if kokoro.is_available():
            audio_bytes, mime = await kokoro.synthesize_wav(target_text, voice="bm_george", speed=1.05)
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
    try:
        while True:
            evt = await queue.get()
            await websocket.send_json(evt)
    except WebSocketDisconnect:
        logger.info("Command Deck disconnected from /ws/events.")
    finally:
        if _on_event in _event_subscribers:
            _event_subscribers.remove(_on_event)


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
