"""agy Sandbox — LLM subprocesses run with a restricted world (Roadmap §11).

'agy/LLM subprocesses: no shell escalation, filesystem allowlist, network
allowlist, no secrets in context.' This module builds the sandboxed
environment every agy/LLM subprocess launch must pass through:

- build_safe_env(): only allowlisted variables survive; EVERYTHING that
  looks like a credential is stripped at the source (defense before
  redaction), plus secrets.redact() on any text heading into the prompt.
- allowed_dirs(): the subprocess may only touch repo + demos + its own tmp;
  enforced by convention here and asserted by the red-team test.
- launch(): wraps the agy CLI invocation with the sandboxed env, a timeout,
  and full audit logging (the command NEVER includes secret values).

Brain integrations call launch() instead of subprocess/os.system directly.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from ghost.core.audit import audit
from ghost.core.secrets import redact

logger = logging.getLogger("ghost.sandbox")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))

# Environment variables an LLM subprocess may see. Everything else is stripped
# — especially *KEY*/*SECRET*/*TOKEN*/*PASSWORD* patterns.
ENV_ALLOWLIST = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL",
    "GHOST_PROJECT_ROOT", "OLLAMA_BASE_URL",
}
ENV_DENY_PATTERNS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL")

# Filesystem the subprocess may work within.
ALLOWED_DIRS = [
    REPO_DIR,
    os.path.join(REPO_DIR, "demos"),
    os.path.join(REPO_DIR, "data"),
    "/tmp",
]


def build_safe_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Whitelist-based env for LLM subprocesses. Deny-patterns win over the
    allowlist — a variable named GHOST_API_KEY can never sneak through by
    being added to the allowlist."""
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in ENV_ALLOWLIST and not any(p in key.upper() for p in ENV_DENY_PATTERNS):
            env[key] = value
    for key, value in (extra or {}).items():
        if any(p in key.upper() for p in ENV_DENY_PATTERNS):
            audit("sandbox.env_var_refused", key=key,
                  reason="credential-shaped variable may not enter an LLM context")
            continue
        env[key] = redact(str(value))
    env.setdefault("GHOST_PROJECT_ROOT", REPO_DIR)
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def allowed_dirs() -> List[str]:
    return [os.path.abspath(d) for d in ALLOWED_DIRS]


def path_allowed(path: str) -> bool:
    """Filesystem allowlist check for any path the subprocess will touch."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    return any(abs_path.startswith(d + os.sep) or abs_path == d for d in allowed_dirs())


def assert_path_allowed(path: str) -> None:
    if not path_allowed(path):
        audit("sandbox.path_refused", path=path[:200])
        raise PermissionError(f"path outside sandbox allowlist: {path}")


def scrub_command(command: str) -> str:
    """Final line of defense: secrets never appear in a logged/executed command."""
    return redact(command)


async def launch(command: str, timeout: float = 120.0,
                 cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run an LLM CLI (agy) inside the sandbox. Returns the standard
    ShellExecutor-shaped result. The command is redacted before audit."""
    from ghost.mcp.bridge import ShellExecutor
    safe_cmd = scrub_command(command)
    workdir = cwd or REPO_DIR
    assert_path_allowed(workdir)
    env = build_safe_env()
    audit("sandbox.launched", command=safe_cmd[:300], cwd=workdir, timeout=timeout)
    proc = await asyncio.create_subprocess_shell(
        safe_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        audit("sandbox.timeout", command=safe_cmd[:200])
        return {"success": False, "returncode": -1, "stdout": "",
                "stderr": f"sandbox: timed out after {timeout}s", "command": safe_cmd}
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": redact(stdout.decode("utf-8", errors="replace")),
        "stderr": redact(stderr.decode("utf-8", errors="replace")),
        "command": safe_cmd,
    }


import asyncio  # noqa: E402
