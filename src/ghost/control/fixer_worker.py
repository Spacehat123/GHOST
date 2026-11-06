"""THE LLM FIXER WORKER (Operator's 11 P7) — a STANDALONE process.

Runs under the sidecar sandbox env: no PYTHONPATH — ghost's own modules (the
effects plane included) are structurally unimportable from here, and a
static test enforces this file imports STDLIB ONLY. Its only network call is
Ghost's MODEL POOL (the same OmniRoute router the brain uses), reached with
a key passed via ENVIRONMENT ONLY (GHOST_MODEL_POOL_KEY) — the key is never
written to disk, and client credentials/grants never reach this process.

Input (argv[1]): JSON {worktree, files: {name: abs_path}, brief, model,
budget_s}
Behavior, per scoped file (one bounded pass, temperature 0, no tools):
  read the file copy -> ask the model pool for the corrected FULL file
  content -> write the patched copy into the worktree
Output (stdout, JSON):
  {"patch": {name: new_content}, "evidence": str, "tool_calls": n, "tokens": n}

The manager's verifier independently re-runs the check on a scratch copy —
this worker CANNOT mark itself done, cannot merge, and cannot touch anything
outside the worktree copies. A model that fails or answers garbage simply
produces no patch; nothing is invented.
"""
import json
import os
import sys
import time
import urllib.request

MAX_FILE_BYTES = 200_000      # an LLM cannot meaningfully fix huge files
MAX_PROMPT_BYTES = 60_000     # truncate oversized sources honestly

PROMPT_TEMPLATE = """You are a surgical file fixer. Fix ONLY the specific problem described.

PROBLEM REPORT:
{brief}

CURRENT FILE CONTENT:
```
{content}
```

Return the COMPLETE corrected file content and NOTHING else — no
explanations, no markdown fences, no commentary. If you are not certain of
the fix, return the file unchanged."""


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.rstrip() + "\n"


def _ask_pool(pool_url: str, api_key: str, model: str, prompt: str,
              timeout: float) -> str:
    """One OpenAI-compatible chat call against Ghost's model pool."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{pool_url.rstrip('/')}/chat/completions",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    choices = data.get("choices") or []
    return str((choices[0].get("message", {}) or {}).get("content", "")) if choices else ""


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"patch": {}, "evidence": "no brief path given",
                          "tool_calls": 0, "tokens": 0}))
        return 1
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        brief_in = json.load(f)

    worktree = brief_in["worktree"]
    files = brief_in.get("files") or {}
    brief_text = str(brief_in.get("brief", ""))[:2000]
    model = str(brief_in.get("model", ""))
    # the pool key arrives via ENVIRONMENT ONLY — it never touches the disk
    pool_url = os.environ.get("GHOST_MODEL_POOL_URL", "").rstrip("/")
    api_key = os.environ.get("GHOST_MODEL_POOL_KEY", "")
    if not pool_url or not api_key:
        print(json.dumps({"patch": {}, "tokens": 0, "tool_calls": 0,
                          "evidence": "model pool not configured in worker env — "
                                      "no fix attempted, nothing invented"}))
        return 0
    deadline = time.time() + float(brief_in.get("budget_s", 180))

    patch: dict = {}
    notes: list = []
    tokens = 0
    for name, abs_path in files.items():
        if time.time() > deadline:
            notes.append(f"budget exhausted before {name}")
            break
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(MAX_FILE_BYTES + 1)
            if len(content.encode()) > MAX_FILE_BYTES:
                notes.append(f"{name}: exceeds {MAX_FILE_BYTES} bytes — refused")
                continue
            prompt = PROMPT_TEMPLATE.format(brief=brief_text,
                                            content=content[:MAX_PROMPT_BYTES])
            answer = _ask_pool(pool_url, api_key, model, prompt,
                               timeout=max(10.0, deadline - time.time()))
            tokens += 1
            fixed = _strip_fences(answer)
            if not fixed.strip():
                notes.append(f"{name}: empty model answer — skipped")
                continue
            if fixed == content:
                notes.append(f"{name}: model returned the file unchanged")
                continue
            # write the patched copy INTO THE WORKTREE — nothing else is
            # writable from this process
            out_path = os.path.join(worktree, name)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(fixed)
            patch[name] = fixed
        except Exception as e:   # one bad file never aborts the pass
            notes.append(f"{name}: {str(e)[:150]}")

    print(json.dumps({"patch": patch,
                      "evidence": "; ".join(notes) or f"patched {len(patch)} file(s)",
                      "tool_calls": len(files) + 1,
                      "tokens": tokens}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
