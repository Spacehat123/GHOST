"""Ghost Agent Core integrating Native Function Calling Loop, OmniRoute Streaming, and Barge-In.
Implements non-negotiable architectural principles:
1. Strict Tool Argument Validation (Zero guessing or silent repairs).
2. Four-Dimensional Bounded Loops (Max model rounds, tool calls, elapsed time, token ceilings).
3. Deterministic Pre-Execution Instruction Compliance Gate.
4. Three-Layer Memory Integration & Cumulative Multi-Turn Task Token Accounting.
"""
import asyncio
import os
import json
import logging
import datetime
import time
from typing import AsyncGenerator, Dict, Any, Optional, List

logger = logging.getLogger("ghost.core")

from ghost.core.brain import OmniRouteBrain, GHOST_TOOLS, CONFIGS_DIR
from ghost.core.follow import DirectiveManager
from ghost.core.validator import ToolArgumentValidator
from ghost.core.token_tracker import TaskTokenTracker, TOKEN_STATS_PATH
from ghost.core.memory import WorkingMemory, EpisodicMemoryStore
from ghost.core.instructions import InstructionComplianceGate

def _compute_relative_time(last_ts_str: Optional[str]) -> str:
    """Compute human-friendly relative time difference since last interaction."""
    if not last_ts_str:
        return "First deployment / fresh boot"
    try:
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
    2. Validates arguments strictly against schema (no guessing or silent repair).
    3. Evaluates deterministic instruction compliance gate before execution.
    4. Enforces 4D bounded loops (rounds, tools, time, tokens).
    5. Accumulates cumulative multi-turn token accounting.
    6. Streams final spoken Simon Riley tactical response.
    """

    def __init__(self, system_prompt: Optional[str] = None):
        self._interrupt_event = asyncio.Event()

    def interrupt(self):
        """Immediately abort ongoing streaming or tool execution."""
        logger.info("GhostAgent: Barge-in interruption triggered")
        self._interrupt_event.set()

    async def ask_stream(
        self,
        user_text: str,
        task_context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
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

        # Initialize Bounded Loop Guard Parameters (4 Dimensions)
        task_id = f"task_{int(time.time() * 1000)}"
        task_start_time = time.time()
        max_model_rounds = int(os.environ.get("GHOST_MAX_MODEL_ROUNDS", "10"))
        max_tool_executions = int(os.environ.get("GHOST_MAX_TOOL_EXECUTIONS", "15"))
        max_task_elapsed_seconds = float(os.environ.get("GHOST_MAX_TASK_ELAPSED_SECONDS", "120.0"))
        max_task_total_tokens = int(os.environ.get("GHOST_MAX_TASK_TOTAL_TOKENS", "60000"))

        # Initialize Working Memory & Token Tracker
        working_memory = WorkingMemory(task_id=task_id, active_goal=user_text)
        task_tracker = TaskTokenTracker(task_id=task_id, model=model)

        # Load active persistent directives (Core Memory)
        directives_context = DirectiveManager.get_snapshot()

        # Retrieve relevant lessons from Episodic Memory
        episodic_store = EpisodicMemoryStore()
        relevant_episodes = episodic_store.retrieve_relevant(user_text, limit=2)
        episodic_banner = ""
        if relevant_episodes:
            ep_lines = []
            for ep in relevant_episodes:
                q = ep.get("query", "")
                outcome = ep.get("outcome", "")
                lesson = ep.get("lessons_learned", "")
                if lesson or outcome:
                    ep_lines.append(f"- Past: '{q[:40]}...' -> Outcome: {outcome[:40]}, Lesson: {lesson[:50]}")
            if ep_lines:
                episodic_banner = "\n\n[EPISODIC MEMORY - RELEVANT LESSONS & PAST OUTCOMES]\n" + "\n".join(ep_lines)

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

        system_content = OmniRouteBrain.SYSTEM_PROMPT + temporal_banner + episodic_banner
        if directives_context:
            system_content += f"\n\n{directives_context}"

        # Token categorization breakdown attribution
        task_tracker.record_category_tokens("memory", max(1, len(directives_context + episodic_banner) // 4))
        task_tracker.record_category_tokens("tool_definitions", max(1, len(json.dumps(GHOST_TOOLS)) // 4))

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        # Append rolling conversational history (last 8 turns, extracting standard role & content)
        for h in OmniRouteBrain._history[-8:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_text})

        # Loop to handle function calling and multi-step tool execution
        total_model_rounds = 0
        total_tool_executions = 0
        operator_aborted = False
        limit_breached: Optional[str] = None
        tool_call_history: List[Dict[str, Any]] = []

        while True:
            # 4-Dimensional Bounded Agent Loop Check
            if total_model_rounds >= max_model_rounds:
                limit_breached = f"Maximum model rounds ({max_model_rounds}) reached."
            elif total_tool_executions >= max_tool_executions:
                limit_breached = f"Maximum tool executions ({max_tool_executions}) reached."
            elif (time.time() - task_start_time) >= max_task_elapsed_seconds:
                limit_breached = f"Task execution time limit ({max_task_elapsed_seconds}s) exceeded."
            elif task_tracker.total_tokens >= max_task_total_tokens:
                limit_breached = f"Task token ceiling ({max_task_total_tokens}) reached."

            if limit_breached:
                logger.warning(f"GhostAgent Bounded Loop Ceiling Reached: {limit_breached}")
                break

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
                round_prompt_tokens = 0
                round_completion_tokens = 0
                round_total_tokens = 0

                async for chunk in stream:
                    if self._interrupt_event.is_set():
                        yield {"type": "state", "state": "INTERRUPTED"}
                        return

                    if hasattr(chunk, "usage") and chunk.usage:
                        round_prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                        round_completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0
                        round_total_tokens = getattr(chunk.usage, "total_tokens", 0) or 0

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

                # Update cumulative task token accounting
                total_model_rounds += 1
                if round_total_tokens == 0:
                    round_prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
                    round_completion_tokens = len("".join(content_accumulator)) // 4
                    round_total_tokens = max(1, round_prompt_tokens + round_completion_tokens)

                task_tracker.add_turn_usage(round_prompt_tokens, round_completion_tokens, round_total_tokens)

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

                        # 4D Bounds Pre-Execution Check
                        if total_tool_executions >= max_tool_executions:
                            limit_breached = f"Maximum tool executions ({max_tool_executions}) reached."
                            break
                        if (time.time() - task_start_time) >= max_task_elapsed_seconds:
                            limit_breached = f"Task execution time limit ({max_task_elapsed_seconds}s) exceeded."
                            break
                        if task_tracker.total_tokens >= max_task_total_tokens:
                            limit_breached = f"Task token ceiling ({max_task_total_tokens}) reached."
                            break

                        raw_args_str = tc_info.get("arguments", "")
                        try:
                            initial_args = json.loads(raw_args_str) if raw_args_str else {}
                            if not isinstance(initial_args, dict):
                                initial_args = {}
                        except Exception:
                            initial_args = {}

                        # 1. DETERMINISTIC INSTRUCTION COMPLIANCE GATE (Pre-Execution Safety Firewall)
                        is_allowed, violation = InstructionComplianceGate.check_pre_execution(
                            tool_name=tool_name,
                            arguments=initial_args,
                            task_context=task_context
                        )
                        if not is_allowed and violation:
                            logger.warning(
                                f"GhostAgent Instruction Compliance Gate Violation: "
                                f"{tool_name} blocked by {violation.get('compliance_rule')}"
                            )
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "arguments": initial_args
                            }
                            yield {
                                "type": "tool_end",
                                "tool": tool_name,
                                "result": violation
                            }
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps(violation)
                            })
                            continue

                        # 2. STRICT TOOL ARGUMENT SCHEMA VALIDATION (Zero Guesses, No Silent Repairs)
                        is_valid, parsed_args, val_err = ToolArgumentValidator.validate(tool_name, raw_args_str)
                        if not is_valid:
                            logger.warning(f"GhostAgent Tool Argument Schema Violation [{tool_name}]: {val_err}")
                            schema_err_result = {
                                "ok": False,
                                "status": "failed",
                                "tool": tool_name,
                                "action": "validate_arguments",
                                "error": val_err,
                                "verified": False,
                                "schema_violation": True
                            }
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "arguments": {}
                            }
                            yield {
                                "type": "tool_end",
                                "tool": tool_name,
                                "result": schema_err_result
                            }
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps(schema_err_result)
                            })
                            continue

                        args = parsed_args or {}

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

                        # Deduplication Guard & Failure Circuit Breaker
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
                                if r.get("status") in ["error", "failed", "blocked"]:
                                    return True
                                if r.get("success") is False or r.get("ok") is False or r.get("allowed") is False:
                                    return True
                                if "returncode" in r and r.get("returncode") != 0:
                                    return True
                            elif isinstance(r, str):
                                if r.strip().lower().startswith("error"):
                                    return True
                            return False

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

                                if consecutive_identical >= 3:
                                    logger.warning("GhostAgent: Halting spinning execution loop to force operator consultation.")
                                    operator_aborted = True

                                continue

                        # Yield UI state update showing tool execution
                        yield {
                            "type": "tool_start",
                            "tool": tool_name,
                            "arguments": args
                        }

                        # Execute Python primitive natively
                        res = await OmniRouteBrain.execute_tool_call(tool_name, args)
                        total_tool_executions += 1
                        task_tracker.record_tool_call()
                        task_tracker.record_category_tokens("tool_outputs", max(1, len(json.dumps(res)) // 4))
                        working_memory.add_tool_result(tool_name, str(args.get("action", "")), res)

                        # Record execution in deduplication history
                        tool_call_history.append({
                            "name": tool_name,
                            "args_key": args_key,
                            "result": res
                        })

                        # Yield UI event for completion
                        yield {
                            "type": "tool_end",
                            "tool": tool_name,
                            "result": res
                        }

                        # Interactive Authorization Modal Handling
                        if tool_name == "desktop_control" and args.get("action") in ["request_authorization", "request_consent", "authorize"]:
                            allowed = res.get("result", {}).get("allowed") if isinstance(res.get("result"), dict) else res.get("allowed")
                            if allowed is False:
                                operator_aborted = True
                                logger.info("Operator denied authorization. Aborting tool loop to final synthesis.")

                        # Feed REAL tool output back into conversation messages
                        data_uri = res.get("data_uri") if isinstance(res, dict) else None
                        if not data_uri and isinstance(res, dict) and isinstance(res.get("result"), dict):
                            data_uri = res["result"].get("data_uri")

                        if tool_name == "capture_screen" and isinstance(res, dict) and (res.get("status") == "success" or res.get("ok")) and data_uri:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_info["id"],
                                "content": json.dumps({"status": "success", "info": "Screen captured successfully. Visual snapshot attached below."})
                            })
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

                    if limit_breached or operator_aborted:
                        break

                    # Loop back to generate model's spoken response with tool results
                    yield {"type": "state", "state": "THINKING"}
                    continue

                else:
                    # Normal completion finished without tool calls
                    assistant_text = "".join(content_accumulator).strip()
                    if assistant_text:
                        turn_ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                        OmniRouteBrain.record_turn(role="user", content=user_text, timestamp=turn_ts)
                        OmniRouteBrain.record_turn(role="assistant", content=assistant_text, timestamp=turn_ts)
                        episodic_store.record_episode(
                            task_id=task_id,
                            query=user_text,
                            summary=assistant_text[:100],
                            outcome="completed",
                            importance=0.7
                        )

                    # Persist cumulative task token metrics
                    global_stats = task_tracker.save_to_disk()
                    yield {
                        "type": "token_stats",
                        "last": task_tracker.total_tokens,
                        "today": global_stats["today"],
                        "cost_usd": task_tracker.cost_usd,
                        "tracker": task_tracker.to_dict()
                    }

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

        # Final Synthesis Pass: Invoked when bounded loop ceiling is reached or operator aborted
        try:
            yield {"type": "state", "state": "THINKING"}

            if limit_breached:
                halt_instruction = (
                    f"[TASK HALTED: BOUNDED LOOP CEILING BREACHED]\n"
                    f"Ceiling condition triggered: {limit_breached}\n"
                    f"Summarize what was completed and explicitly detail what remains unfinished (remaining_work)."
                )
                messages.append({"role": "user", "content": halt_instruction})

            final_stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True}
            )

            content_accumulator = []
            is_speaking = False
            syn_prompt = 0
            syn_comp = 0
            syn_total = 0

            async for chunk in final_stream:
                if self._interrupt_event.is_set():
                    yield {"type": "state", "state": "INTERRUPTED"}
                    return

                if hasattr(chunk, "usage") and chunk.usage:
                    syn_prompt = getattr(chunk.usage, "prompt_tokens", 0) or 0
                    syn_comp = getattr(chunk.usage, "completion_tokens", 0) or 0
                    syn_total = getattr(chunk.usage, "total_tokens", 0) or 0

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
                episodic_store.record_episode(
                    task_id=task_id,
                    query=user_text,
                    summary=assistant_text[:100],
                    outcome="halted_limit_breached" if limit_breached else "aborted",
                    error=limit_breached or "operator_aborted",
                    importance=0.8
                )

            if syn_total == 0:
                syn_prompt = sum(len(str(m.get("content", ""))) for m in messages) // 4
                syn_comp = len(assistant_text) // 4
                syn_total = max(1, syn_prompt + syn_comp)

            task_tracker.add_turn_usage(syn_prompt, syn_comp, syn_total)
            task_tracker.record_category_tokens("final_synthesis", syn_total)

            global_stats = task_tracker.save_to_disk()
            yield {
                "type": "token_stats",
                "last": task_tracker.total_tokens,
                "today": global_stats["today"],
                "cost_usd": task_tracker.cost_usd,
                "tracker": task_tracker.to_dict()
            }

        except Exception as e:
            logger.error(f"Final synthesis pass error: {e}", exc_info=True)
            yield {"type": "state", "state": "SPEAKING"}
            yield {"type": "token", "text": "Tactical action completed, boss."}

        yield {"type": "state", "state": "IDLE"}
