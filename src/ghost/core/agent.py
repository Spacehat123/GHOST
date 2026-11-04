"""Ghost Agent Core integrating Native Function Calling Loop, OmniRoute Streaming, and Barge-In."""
import asyncio
import os
import json
import logging
import datetime
from typing import AsyncGenerator, Dict, Any, Optional, List

logger = logging.getLogger("ghost.core")

from ghost.core.brain import OmniRouteBrain, GHOST_TOOLS, CONFIGS_DIR
from ghost.core.follow import DirectiveManager

TOKEN_STATS_PATH = os.path.join(CONFIGS_DIR, "token_stats.json")

def _compute_relative_time(last_ts_str: Optional[str]) -> str:
    """Compute human-friendly relative time difference since last interaction."""
    if not last_ts_str:
        return "First deployment / fresh boot"
    try:
        # Expected format: %Y-%m-%d %H:%M:%S %Z or parse prefix
        parts = last_ts_str.split()
        if len(parts) >= 2:
            date_time_str = f"{parts[0]} {parts[1]}"
            last_dt = datetime.datetime.strptime(date_time_str, "%Y-%m-%d %H:%M:%S")
            now_dt = datetime.datetime.now()
            diff = now_dt - last_dt
            seconds = int(diff.total_seconds())
            if seconds < 0:
                return "Just now"
            if seconds < 60:
                return f"{seconds} seconds ago"
            minutes = seconds // 60
            if minutes < 60:
                return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            hours = minutes // 60
            if hours < 24:
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
            days = hours // 24
            if days == 1:
                return f"Yesterday at {parts[1][:5]}"
            return f"{days} days ago ({last_ts_str})"
    except Exception:
        pass
    return f"Last seen at {last_ts_str}"

def _update_token_stats(turn_tokens: int) -> Dict[str, int]:
    """Track last and total tokens consumed in configs/token_stats.json."""
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    today_tokens = 0
    if os.path.exists(TOKEN_STATS_PATH):
        try:
            with open(TOKEN_STATS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                today_tokens = data.get("today", 0)
        except Exception:
            today_tokens = 0

    today_tokens += turn_tokens
    stats = {"last": turn_tokens, "today": today_tokens}
    try:
        with open(TOKEN_STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist token_stats: {e}")
    return stats

class GhostAgent:
    """
    Agent orchestrator running full OpenAI function-calling loops with OmniRoute:
    1. Sends user input with GHOST_TOOLS function definitions.
    2. If tool_calls emitted: yields tool_start -> executes Python tool -> sends tool result back.
    3. Streams final spoken Simon Riley tactical response.
    """

    def __init__(self, system_prompt: Optional[str] = None):
        self._interrupt_event = asyncio.Event()

    def interrupt(self):
        """Immediately abort ongoing streaming or tool execution."""
        logger.info("GhostAgent: Barge-in interruption triggered")
        self._interrupt_event.set()

    async def ask_stream(self, user_text: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream conversational agent turn with real-time tool calls and token delivery.
        """
        self._interrupt_event.clear()

        # Step 1: Thinking state
        yield {"type": "state", "state": "THINKING"}
        await asyncio.sleep(0.02)

        OmniRouteBrain.load_config()
        # Load persistent conversational history from disk
        OmniRouteBrain.load_history()

        api_key = OmniRouteBrain._config.get("api_key", "").strip()
        if not api_key:
            yield {"type": "state", "state": "SPEAKING"}
            msg = "OmniRoute API key not configured yet, sir. Click settings in the top-right corner to link your key."
            yield {"type": "token", "text": msg}
            yield {"type": "state", "state": "IDLE"}
            return

        client = OmniRouteBrain.get_client()
        model = OmniRouteBrain._config.get("model", "antigravity/gemini-3.8-flash-low")

        # Load active persistent directives
        directives_context = DirectiveManager.get_snapshot()

        # Compute live temporal context
        now_dt = datetime.datetime.now().astimezone()
        now_str = now_dt.strftime("%A, %b %-d, %Y, %H:%M %Z")
        last_ts = OmniRouteBrain.get_last_interaction_time()
        relative_last = _compute_relative_time(last_ts)

        temporal_banner = (
            f"\n\n[LIVE TEMPORAL & SYSTEM CONTEXT]\n"
            f"- Current Local Date & Time: {now_str}\n"
            f"- Last Operator Interaction: {relative_last}\n"
            f"- State: Resumed active mission from persistent memory ledger ({len(OmniRouteBrain._history)} turns recorded)\n"
        )

        system_content = OmniRouteBrain.SYSTEM_PROMPT + temporal_banner
        if directives_context:
            system_content += f"\n\n{directives_context}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        # Append rolling conversational history (last 8 turns, extracting standard role & content)
        for h in OmniRouteBrain._history[-8:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_text})

        # Loop to handle function calling and multi-step tool execution (unlimited operational turns)
        current_turn = 0
        latest_turn_tokens = 0
        operator_aborted = False
        tool_call_history: List[Dict[str, Any]] = []

        while True:
            current_turn += 1

            if self._interrupt_event.is_set():
                yield {"type": "state", "state": "INTERRUPTED"}
                return

            try:
                # Stream the completion from OmniRoute
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=GHOST_TOOLS,
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True}
                )

                tool_calls_accumulator = {}
                content_accumulator = []
                is_speaking = False

                async for chunk in stream:
                    if self._interrupt_event.is_set():
                        yield {"type": "state", "state": "INTERRUPTED"}
                        return

                    if hasattr(chunk, "usage") and chunk.usage:
                        # Take the latest turn usage snapshot (not compounding total_tokens across multiple turns)
                        latest_turn_tokens = (chunk.usage.total_tokens or 0)

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    # Accumulate tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.id or f"call_{idx}",
                                    "name": tc.function.name if tc.function and tc.function.name else "",
                                    "arguments": tc.function.arguments if tc.function and tc.function.arguments else ""
                                }
                            else:
                                if tc.function:
                                    if tc.function.name:
                                        tool_calls_accumulator[idx]["name"] += tc.function.name
                                    if tc.function.arguments:
                                        tool_calls_accumulator[idx]["arguments"] += tc.function.arguments

                    # Stream text tokens directly to GUI
                    if delta.content:
                        if not is_speaking:
                            is_speaking = True
                            yield {"type": "state", "state": "SPEAKING"}

                        clean_token = delta.content.replace("**", "").replace("`", "")
                        content_accumulator.append(clean_token)
                        yield {"type": "token", "text": clean_token}

                # Check if model requested tool execution
                if tool_calls_accumulator:
                    # Construct assistant message with tool calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": "".join(content_accumulator) or None,
                        "tool_calls": [
                            {
                                "id": tc_info["id"],
                                "type": "function",
                                "function": {
                                    "name": tc_info["name"],
                                    "arguments": tc_info["arguments"]
                                }
                            }
                            for tc_info in tool_calls_accumulator.values()
                        ]
                    }
                    messages.append(assistant_msg)

                    # Execute each tool call
                    for tc_info in tool_calls_accumulator.values():
                        tool_name = tc_info["name"]
                        try:
                            args = json.loads(tc_info["arguments"]) if tc_info["arguments"] else {}
                        except Exception:
                            args = {}

                        # Serialize arguments deterministically for deduplication check
                        try:
                            args_key = json.dumps(args, sort_keys=True)
                        except Exception:
                            args_key = str(args)

                        if operator_aborted:
                            res = {
                                "status": "aborted",
                                "error": "Execution halted: circuit breaker tripped on preceding repeated call."
                            }
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "arguments": args
                            }
                            yield {
                                "type": "tool_end",
                                "tool": tool_name,
                                "result": res
                            }
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps(res)
                            })
                            continue

                        # Deduplication Guard & Failure Circuit Breaker:
                        # Check consecutive attempts of the identical tool call and arguments
                        consecutive_identical = 0
                        prev_results = []
                        for prev in reversed(tool_call_history):
                            if prev["name"] == tool_name and prev["args_key"] == args_key:
                                consecutive_identical += 1
                                prev_results.append(prev["result"])
                            else:
                                break

                        def _is_error_result(r: Any) -> bool:
                            if isinstance(r, dict):
                                if r.get("error"):
                                    return True
                                if r.get("status") in ["error", "failed"]:
                                    return True
                                if r.get("success") is False or r.get("ok") is False or r.get("allowed") is False:
                                    return True
                                if "returncode" in r and r.get("returncode") != 0:
                                    return True
                            elif isinstance(r, str):
                                if r.strip().lower().startswith("error"):
                                    return True
                            return False

                        # Circuit Breaker: If called 3 consecutive times with identical args
                        # and prior executions yielded errors or identical results, intercept immediately
                        if consecutive_identical >= 2:
                            has_error = any(_is_error_result(r) for r in prev_results)
                            has_identical = (
                                len(prev_results) >= 2 and
                                all(r == prev_results[0] for r in prev_results[1:])
                            )

                            if has_error or has_identical:
                                logger.warning(
                                    f"GhostAgent Circuit Breaker: Intercepted repeated tool call '{tool_name}' "
                                    f"with identical arguments ({consecutive_identical + 1} consecutive attempts)."
                                )
                                res = {
                                    "status": "circuit_breaker_tripped",
                                    "error": f"Circuit breaker tripped: '{tool_name}' called {consecutive_identical + 1} consecutive times with identical arguments yielding identical/failing results.",
                                    "diagnostic": (
                                        f"Execution loop deduplication guard activated for tool '{tool_name}'. "
                                        f"You MUST NOT retry this exact tool call again with the same arguments. "
                                        f"Adapt your strategy immediately: try an alternative tool/parameter, or report the situation and ask the operator for guidance."
                                    ),
                                    "consecutive_attempts": consecutive_identical + 1
                                }

                                yield {
                                    "type": "tool_start",
                                    "tool": tool_name,
                                    "arguments": args
                                }
                                yield {
                                    "type": "tool_end",
                                    "tool": tool_name,
                                    "result": res
                                }

                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc_info["id"],
                                    "content": json.dumps(res)
                                })

                                tool_call_history.append({
                                    "name": tool_name,
                                    "args_key": args_key,
                                    "result": res
                                })

                                # If repeated beyond 3 attempts, halt the loop and force spoken synthesis to operator
                                if consecutive_identical >= 3:
                                    logger.warning("GhostAgent: Halting spinning execution loop to force operator consultation.")
                                    operator_aborted = True

                                continue

                        # 1. Yield UI state update showing tool execution
                        yield {
                            "type": "tool_start",
                            "tool": tool_name,
                            "arguments": args
                        }

                        # 2. Execute Python primitive natively
                        res = await OmniRouteBrain.execute_tool_call(tool_name, args)

                        # Record execution in deduplication history
                        tool_call_history.append({
                            "name": tool_name,
                            "args_key": args_key,
                            "result": res
                        })

                        # 3. Yield UI event for completion
                        yield {
                            "type": "tool_end",
                            "tool": tool_name,
                            "result": res
                        }

                        # Interactive Authorization Modal Handling
                        # If operator explicitly denies authorization on a gated action
                        if tool_name == "desktop_control" and args.get("action") in ["request_authorization", "request_consent", "authorize"]:
                            allowed = res.get("result", {}).get("allowed") if isinstance(res.get("result"), dict) else res.get("allowed")
                            if allowed is False:
                                operator_aborted = True
                                logger.info("Operator denied authorization. Aborting tool loop to final synthesis.")

                        # 4. Feed REAL tool output back into conversation messages
                        data_uri = res.get("data_uri") if isinstance(res, dict) else None
                        if not data_uri and isinstance(res, dict) and isinstance(res.get("result"), dict):
                            data_uri = res["result"].get("data_uri")

                        if tool_name == "capture_screen" and isinstance(res, dict) and (res.get("status") == "success" or res.get("ok")) and data_uri:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps({"status": "success", "info": "Screen captured successfully. Visual snapshot attached below."})
                            })
                            # Append user vision payload containing base64 JPEG
                            messages.append({
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Here is the visual snapshot of the screen right now. Analyze and report what is on it:"},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": data_uri
                                        }
                                    }
                                ]
                            })
                        else:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps(res)
                            })

                    # If operator explicitly denied authorization, exit tool loop immediately to final synthesis pass
                    if operator_aborted:
                        break

                    # Loop back to generate model's spoken response with tool results!
                    yield {"type": "state", "state": "THINKING"}
                    continue

                else:
                    # Normal completion finished without tool calls
                    assistant_text = "".join(content_accumulator).strip()
                    if assistant_text:
                        turn_ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                        OmniRouteBrain.record_turn(role="user", content=user_text, timestamp=turn_ts)
                        OmniRouteBrain.record_turn(role="assistant", content=assistant_text, timestamp=turn_ts)

                    # Calculate / estimate token metrics accurately using the peak turn tokens
                    if latest_turn_tokens == 0:
                        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
                        resp_chars = len(assistant_text)
                        latest_turn_tokens = max(1, (prompt_chars + resp_chars) // 4)

                    token_stats = _update_token_stats(latest_turn_tokens)
                    yield {"type": "token_stats", "last": token_stats["last"], "today": token_stats["today"]}

                    yield {"type": "state", "state": "IDLE"}
                    return

            except Exception as e:
                logger.error(f"OmniRoute turn error: {e}", exc_info=True)
                yield {"type": "state", "state": "SPEAKING"}
                err_msg = str(e)
                if "Authentication required" in err_msg or "invalid_api_key" in err_msg:
                    yield {"type": "token", "text": "OmniRoute authentication error, sir. Verify your API key in settings."}
                else:
                    yield {"type": "token", "text": f"Tactical comms breakdown: {err_msg[:80]}."}
                yield {"type": "state", "state": "IDLE"}
                return

        # If the tool loop exhausted max turns, execute a mandatory final synthesis pass with NO tools
        # to ensure Ghost speaks a closing response to the user instead of going silent/IDLE.
        try:
            yield {"type": "state", "state": "THINKING"}
            final_stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True}
            )

            content_accumulator = []
            is_speaking = False

            async for chunk in final_stream:
                if self._interrupt_event.is_set():
                    yield {"type": "state", "state": "INTERRUPTED"}
                    return

                if hasattr(chunk, "usage") and chunk.usage:
                    latest_turn_tokens = max(latest_turn_tokens, (chunk.usage.total_tokens or 0))

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    if not is_speaking:
                        is_speaking = True
                        yield {"type": "state", "state": "SPEAKING"}

                    clean_token = delta.content.replace("**", "").replace("`", "")
                    content_accumulator.append(clean_token)
                    yield {"type": "token", "text": clean_token}

            assistant_text = "".join(content_accumulator).strip()
            if assistant_text:
                turn_ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                OmniRouteBrain.record_turn(role="user", content=user_text, timestamp=turn_ts)
                OmniRouteBrain.record_turn(role="assistant", content=assistant_text, timestamp=turn_ts)

            if latest_turn_tokens == 0:
                prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
                resp_chars = len(assistant_text)
                latest_turn_tokens = max(1, (prompt_chars + resp_chars) // 4)

            token_stats = _update_token_stats(latest_turn_tokens)
            yield {"type": "token_stats", "last": token_stats["last"], "today": token_stats["today"]}

        except Exception as e:
            logger.error(f"Final synthesis pass error: {e}", exc_info=True)
            yield {"type": "state", "state": "SPEAKING"}
            yield {"type": "token", "text": "Tactical action completed, boss."}

        yield {"type": "state", "state": "IDLE"}
