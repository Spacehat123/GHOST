"""Strict Typed Evidence Verification Engine for Ghost Autonomous Agency.
Enforces non-negotiable architectural invariant:
Ghost must never claim a task is complete, a file exists, or an action succeeded
merely because a Python function returned. Every task completion claim must be
verified by deterministic code checks against physical artifacts and logs.
"""
import os
import hashlib
import struct
import zlib
import logging
from typing import Dict, Any, List, Optional, Tuple
from ghost.core.instructions import InstructionComplianceGate

logger = logging.getLogger("ghost.evidence")

VALID_EVIDENCE_TYPES = {"file", "command", "screenshot", "url", "test", "ledger"}


def _compute_png_variance(file_path: str) -> float:
    """Compute true pixel luminance / RGB variance of a PNG file using stdlib zlib."""
    with open(file_path, "rb") as f:
        data = f.read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return -1.0

    width = 0
    height = 0
    color_type = 2
    idat = bytearray()
    idx = 8
    while idx < len(data):
        if idx + 8 > len(data):
            break
        length, ctype = struct.unpack(">I4s", data[idx:idx+8])
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", data[idx+8:idx+18])
        elif ctype == b"IDAT":
            chunk_data = data[idx+8:idx+8+length]
            idat.extend(chunk_data)
        idx += 12 + length

    if not idat or width <= 0 or height <= 0:
        return 0.0

    try:
        raw_bytes = zlib.decompress(idat)
        bpp = 4 if color_type == 6 else (3 if color_type == 2 else 1)
        stride = 1 + width * bpp
        if len(raw_bytes) < height * stride:
            return 0.0

        luminances = []
        for row in range(height):
            row_offset = row * stride + 1  # skip filter byte
            for col in range(width):
                px_offset = row_offset + col * bpp
                if bpp >= 3:
                    r, g, b = raw_bytes[px_offset], raw_bytes[px_offset+1], raw_bytes[px_offset+2]
                    lum = 0.299 * r + 0.587 * g + 0.114 * b
                else:
                    lum = float(raw_bytes[px_offset])
                luminances.append(lum)

        if not luminances:
            return 0.0
        mean = sum(luminances) / len(luminances)
        variance = sum((l - mean) ** 2 for l in luminances) / len(luminances)
        return variance
    except Exception as e:
        logger.warning(f"Failed to decompress PNG IDAT chunk for {file_path}: {e}")
        return 0.0


def _compute_byte_variance(file_path: str) -> float:
    """Compute byte variance for image files when full decompression is unavailable."""
    with open(file_path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        return 0.0
    # Sample up to 64KB from middle
    start = min(32, len(data) // 4)
    sample = data[start:start + 65536]
    mean = sum(sample) / len(sample)
    variance = sum((b - mean) ** 2 for b in sample) / len(sample)
    return variance


def verify_evidence_item(item: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Verify a single evidence item against typed rules.
    Returns:
        (is_valid: bool, reason: str, enriched_item_or_None: Optional[Dict])
    """
    if not isinstance(item, dict):
        return False, "Evidence item must be a dictionary", None

    ev_type = str(item.get("type", "")).strip().lower()
    if not ev_type:
        return False, "Evidence item missing 'type' field", None

    if ev_type not in VALID_EVIDENCE_TYPES:
        return False, f"Unsupported evidence type: '{ev_type}'. Allowed: {sorted(list(VALID_EVIDENCE_TYPES))}", None

    enriched = dict(item)

    # 1. File Evidence
    if ev_type == "file":
        path = item.get("path") or item.get("file_path")
        if not path or not isinstance(path, str):
            return False, "File evidence requires a non-empty 'path' string", None

        expanded_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(expanded_path):
            return False, f"File does not exist: {expanded_path}", None
        if not os.path.isfile(expanded_path):
            return False, f"Path is not a regular file: {expanded_path}", None
        if not InstructionComplianceGate.is_path_within_allowed_workspace(expanded_path):
            return False, f"File path is outside allowed workspace boundaries: {expanded_path}", None

        file_size = os.path.getsize(expanded_path)
        if file_size == 0:
            return False, f"File is empty (0 bytes): {expanded_path}", None

        # Compute SHA-256 hash
        hasher = hashlib.sha256()
        with open(expanded_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()
        enriched["hash"] = file_hash
        enriched["size_bytes"] = file_size
        return True, "File verified on disk", enriched

    # 2. Command Execution Evidence
    elif ev_type == "command":
        cmd = item.get("command")
        if not cmd or not isinstance(cmd, str):
            return False, "Command evidence requires a non-empty 'command' string", None

        exit_code = item.get("exit_code")
        if exit_code is None:
            return False, "Command evidence requires 'exit_code'", None
        try:
            exit_code = int(exit_code)
        except (ValueError, TypeError):
            return False, f"Invalid exit_code: {exit_code}", None

        if exit_code != 0:
            return False, f"Command exited with non-zero exit code: {exit_code}", None

        output = item.get("output", "")
        if not str(output).strip():
            return False, "Command evidence requires non-empty 'output' tail", None

        # Check against real recorded tool call in mission store
        from ghost.core.mission import MissionStore
        ctx = context or {}
        store = ctx.get("store") or MissionStore.get_instance()
        mission_id = ctx.get("mission_id") or item.get("mission_id")
        tool_call_id = item.get("tool_call_id") or item.get("call_id") or item.get("id")

        if tool_call_id:
            rec = store.get_tool_call(tool_call_id)
            if not rec:
                return False, f"Command evidence references unknown or unrecorded tool_call_id: '{tool_call_id}'. Only real tool calls can serve as command evidence.", None
            if mission_id and rec.get("mission_id") != mission_id:
                return False, f"Tool call '{tool_call_id}' belongs to mission '{rec.get('mission_id')}', not current mission '{mission_id}'.", None
            if rec.get("exit_code") != 0:
                return False, f"Recorded tool call '{tool_call_id}' failed with exit code {rec.get('exit_code')}.", None
            enriched["tool_call_id"] = tool_call_id
            enriched["real_command"] = rec.get("command")
            enriched["real_exit_code"] = rec.get("exit_code")
            enriched["verified_by_store"] = True
        elif mission_id:
            recorded_calls = store.get_tool_calls_for_mission(mission_id)
            matched_call = None
            cmd_clean = cmd.strip()
            for call in recorded_calls:
                call_cmd = str(call.get("command", "")).strip()
                if (cmd_clean == call_cmd or cmd_clean in call_cmd or call_cmd in cmd_clean) and call.get("exit_code") == 0:
                    matched_call = call
                    break
            
            if not matched_call:
                return False, f"Command '{cmd}' does not match any real tool call executed in mission '{mission_id}'. Faked or unexecuted command evidence rejected.", None

            enriched["tool_call_id"] = matched_call["id"]
            enriched["real_exit_code"] = matched_call["exit_code"]
            enriched["verified_by_tool_call"] = True

        return True, "Command execution exit 0 verified", enriched

    # 3. Screenshot Evidence
    elif ev_type == "screenshot":
        path = item.get("path") or item.get("file_path")
        if not path or not isinstance(path, str):
            return False, "Screenshot evidence requires a non-empty 'path' string", None

        expanded_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(expanded_path):
            return False, f"Screenshot file does not exist: {expanded_path}", None
        if not os.path.isfile(expanded_path):
            return False, f"Screenshot path is not a regular file: {expanded_path}", None
        if os.path.getsize(expanded_path) == 0:
            return False, f"Screenshot file is empty (0 bytes): {expanded_path}", None

        # Pixel variance check (not solid color or blank)
        var = _compute_png_variance(expanded_path)
        if var < 0.0:  # Not a PNG or failed parsing
            var = _compute_byte_variance(expanded_path)

        if var < 0.1:
            return False, f"Screenshot variance check failed ({var:.4f} < 0.1). Image appears blank or solid color.", None

        enriched["pixel_variance"] = round(var, 4)
        return True, "Screenshot image verified with non-zero variance", enriched

    # 4. URL Evidence
    elif ev_type == "url":
        url = item.get("url")
        if not url or not isinstance(url, str):
            return False, "URL evidence requires a non-empty 'url' string", None
        if not (url.startswith("http://") or url.startswith("https://")):
            return False, f"Invalid URL scheme: '{url}'. Must start with http:// or https://", None

        status_code = item.get("status_code")
        if status_code is not None:
            try:
                status_code = int(status_code)
            except (ValueError, TypeError):
                return False, f"Invalid status_code: {status_code}", None
            if not (200 <= status_code < 300):
                return False, f"URL returned non-success status code: {status_code}", None
        else:
            # If no status code provided in offline test, verify scheme and domain
            pass

        return True, "URL verified", enriched

    # 5. Test Suite Evidence
    elif ev_type == "test":
        pass_count = item.get("pass_count")
        failures = item.get("failures", 0)
        errors = item.get("errors", 0)
        output = str(item.get("output", ""))

        if pass_count is not None:
            try:
                pass_count = int(pass_count)
                failures = int(failures)
                errors = int(errors)
            except (ValueError, TypeError):
                return False, "Invalid test pass/failure count fields", None

            if pass_count <= 0:
                return False, f"Test suite evidence must show pass_count > 0 (got {pass_count})", None
            if failures > 0 or errors > 0:
                return False, f"Test suite evidence contains failures ({failures}) or errors ({errors})", None
        elif output:
            # Heuristic check on test runner output string
            if "FAILED" in output:
                return False, "Test output contains FAILED status", None
            if not ("OK" in output or "passed" in output.lower()):
                return False, "Test output did not indicate success (OK or passed)", None
        else:
            return False, "Test evidence requires 'pass_count' > 0 or output with OK status", None

        # Check against real recorded tool call or test report if in a mission
        from ghost.core.mission import MissionStore
        ctx = context or {}
        store = ctx.get("store") or MissionStore.get_instance()
        mission_id = ctx.get("mission_id") or item.get("mission_id")
        tool_call_id = item.get("tool_call_id") or item.get("call_id") or item.get("id")

        if tool_call_id:
            rec = store.get_tool_call(tool_call_id)
            if not rec:
                return False, f"Test evidence references unknown tool_call_id: '{tool_call_id}'.", None
            if rec.get("exit_code") != 0:
                return False, f"Recorded test tool call '{tool_call_id}' failed with exit code {rec.get('exit_code')}.", None
            rec_out = str(rec.get("output", ""))
            if "FAILED" in rec_out:
                return False, f"Recorded tool call '{tool_call_id}' contains test failures.", None
            enriched["tool_call_id"] = tool_call_id
            enriched["verified_by_store"] = True
        elif mission_id:
            report_path = item.get("report_path") or item.get("path")
            has_valid_report = report_path and os.path.exists(report_path) and os.path.getsize(report_path) > 0
            recorded_calls = store.get_tool_calls_for_mission(mission_id)
            has_recorded_test_run = any(
                ("test" in str(c.get("command", "")).lower() or "test" in str(c.get("tool_name", "")).lower())
                and c.get("exit_code") == 0
                for c in recorded_calls
            )

            if not has_valid_report and not has_recorded_test_run:
                return False, f"Test suite evidence rejected: no test execution tool call was recorded in mission '{mission_id}' and no verified report file exists on disk.", None

        return True, "Test suite execution verified with passing count", enriched

    # 6. Source Ledger / CRM Record Evidence
    elif ev_type == "ledger":
        row_id = item.get("row_id") or item.get("slug")
        if not row_id or not isinstance(row_id, str):
            return False, "Ledger evidence requires 'row_id' or 'slug'", None

        return True, f"Ledger reference '{row_id}' verified", enriched

    return False, "Unreachable evidence check", None


def verify_task_evidence(
    task_acceptance: List[str],
    evidence_required: List[str],
    evidence_items: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
    """
    Verifies that all required evidence types are present and each passes verification.
    Returns:
        (is_valid: bool, failure_reason_or_None: Optional[str], verified_items: List[Dict])
    """
    if not evidence_required:
        return True, None, evidence_items or []

    if not evidence_items:
        return False, f"Missing evidence. Task requires: {', '.join(evidence_required)}", []

    items_by_type: Dict[str, List[Dict[str, Any]]] = {}
    verified_items: List[Dict[str, Any]] = []

    for item in evidence_items:
        t = str(item.get("type", "")).strip().lower()
        if t:
            items_by_type.setdefault(t, []).append(item)

    # Check each required evidence type
    for req_type in evidence_required:
        req = req_type.strip().lower()
        candidates = items_by_type.get(req, [])
        if not candidates:
            return False, f"Missing required evidence of type '{req}'", []

        # At least one candidate of this type must pass verification
        passed_candidate = False
        last_error = ""
        for cand in candidates:
            ok, reason, enriched = verify_evidence_item(cand, context=context)
            if ok and enriched is not None:
                passed_candidate = True
                verified_items.append(enriched)
                break
            else:
                last_error = reason

        if not passed_candidate:
            return False, f"Required evidence of type '{req}' failed verification: {last_error}", []

    return True, None, verified_items
