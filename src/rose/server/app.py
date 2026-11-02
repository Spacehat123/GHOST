"""FastAPI and WebSocket bridge for Rose Desktop GUI."""
import asyncio
import json
import os
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from rose.core.agent import RoseAgent
from rose.voice.duplex import DuplexCoordinator, VoiceState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rose.server")

app = FastAPI(title="Rose Voice Assistant")

agent = RoseAgent()
duplex = DuplexCoordinator(on_interrupt=agent.interrupt)

# Mount static files for GUI
GUI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../gui"))
app.mount("/static", StaticFiles(directory=os.path.join(GUI_DIR, "static")), name="static")

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(GUI_DIR, "index.html"))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("GUI connected via WebSocket.")

    current_task = None

    try:
        # Broadcast initial state
        await websocket.send_json({"type": "state", "state": duplex.state.value})

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            if action == "speech_start":
                # User began speaking
                if duplex.handle_barge_in():
                    logger.info("Barge-in: Interrupting ongoing tasks!")
                    if current_task and not current_task.done():
                        current_task.cancel()
                    await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                    await asyncio.sleep(0.1)
                
                duplex.set_state(VoiceState.LISTENING)
                await websocket.send_json({"type": "state", "state": "LISTENING"})

            elif action == "speech_interim":
                # Real-time interim user speech transcript
                transcript = msg.get("transcript", "")
                await websocket.send_json({"type": "transcript_interim", "text": transcript})

            elif action == "speech_final":
                # User completed speech turn
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

                # Cancel previous generation task if still running
                if current_task and not current_task.done():
                    current_task.cancel()

                # Start agent stream
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
                # Manual interrupt button
                duplex.handle_barge_in()
                if current_task and not current_task.done():
                    current_task.cancel()
                await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                await asyncio.sleep(0.15)
                duplex.set_state(VoiceState.IDLE)
                await websocket.send_json({"type": "state", "state": "IDLE"})

    except WebSocketDisconnect:
        logger.info("GUI disconnected.")
        if current_task and not current_task.done():
            current_task.cancel()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
