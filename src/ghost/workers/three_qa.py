"""Real visual QA for Three.js sites via Chrome DevTools Protocol.

Renders the site in a real browser, captures actual screenshots per stage and
viewport, measures FPS, and runs pixel-level legibility checks.

Honesty invariants (non-negotiable):
- A page that was never rendered in a browser can NEVER produce "passed: true".
- FPS that was never measured is never written as "verified: true".
- If no browser can be reached, the report is "inconclusive" — with an explicit
  notice that NO visual verification happened. Never a rubber stamp.
- Every pass verdict cites the screenshots it is based on; the report lists
  them so a human (or vision model) can review before deploy.
"""
import asyncio
import base64
import io
import json
import logging
import os
import shutil
import socket
import tempfile
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional

from PIL import Image

import websockets

logger = logging.getLogger("ghost.three_qa")

try:
    from ghost.mcp.bridge import BraveCDPBridge, CDPAutomationEngine
except ImportError:  # pragma: no cover - standalone usage
    BraveCDPBridge = None
    CDPAutomationEngine = None

DEFAULT_VIEWPORTS = [
    {"name": "desktop", "width": 1440, "height": 900, "mobile": False},
    {"name": "mobile", "width": 390, "height": 844, "mobile": True}
]

# Luminance (0-255) spread required between the 5th and 95th percentile pixels
# inside a hero text rect for the text to be considered legible.
MIN_TEXT_LUMINANCE_SPREAD = 40
STAGE_TRANSITION_MS = 1600   # > the 1200ms camera animation
RENDER_SETTLE_S = 2.5        # wait for three.js scene + model to load


def _luminance_spread(img: Image.Image) -> int:
    """5th..95th percentile luminance spread of a grayscale PIL image (0-255)."""
    hist = img.convert("L").histogram()
    total = sum(hist)
    if total == 0:
        return 0
    acc = 0
    p_lo = p_hi = None
    lo_cut = total * 0.05
    hi_cut = total * 0.95
    for value, count in enumerate(hist):
        acc += count
        if p_lo is None and acc >= lo_cut:
            p_lo = value
        if acc >= hi_cut:
            p_hi = value
            break
    if p_lo is None or p_hi is None:
        return 0
    return abs(p_hi - p_lo)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cdp_http(port: int, path: str, method: str = "GET", timeout: float = 5.0) -> Any:
    """Raw CDP HTTP endpoint call (/json/list, /json/new, ...)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except Exception:
        return body


class _CdpTab:
    """A single CDP-connected page used for one QA run."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url

    async def command(self, req_id: int, method: str, params: Optional[Dict] = None, timeout: float = 15.0) -> Dict:
        async with websockets.connect(self.ws_url, close_timeout=3, max_size=100 * 1024 * 1024) as ws:
            return await CDPAutomationEngine.send_command(ws, req_id, method, params, timeout=timeout)

    async def evaluate(self, expression: str, timeout: float = 15.0) -> Any:
        async with websockets.connect(self.ws_url, close_timeout=3, max_size=100 * 1024 * 1024) as ws:
            res = await CDPAutomationEngine.send_command(ws, 3001, "Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True
            }, timeout=timeout)
            return res.get("result", {}).get("result", {}).get("value")

    async def screenshot_png(self) -> Optional[bytes]:
        res = await self.command(5001, "Page.captureScreenshot", {"format": "png", "fromSurface": True})
        data = res.get("result", {}).get("data")
        return base64.b64decode(data) if data else None


def _luminance_spread_of_region(png: bytes, rect: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """Contrast metrics for a hero text rect inside a PNG screenshot."""
    if not rect or rect.get("width", 0) < 4 or rect.get("height", 0) < 4:
        return {"measured": False, "spread": None, "pass": False, "reason": "element rect unavailable"}
    img = Image.open(io.BytesIO(png))
    scale = img.width / max(1.0, float(rect.get("viewport_width", img.width))) if rect.get("viewport_width") else 1.0
    left = max(0, int(rect["x"] * scale))
    top = max(0, int(rect["y"] * scale))
    right = min(img.width, int((rect["x"] + rect["width"]) * scale))
    bottom = min(img.height, int((rect["y"] + rect["height"]) * scale))
    if right - left < 4 or bottom - top < 4:
        return {"measured": False, "spread": None, "pass": False, "reason": "rect outside viewport"}
    spread = _luminance_spread(img.crop((left, top, right, bottom)))
    return {
        "measured": True,
        "spread": spread,
        "threshold": MIN_TEXT_LUMINANCE_SPREAD,
        "pass": spread >= MIN_TEXT_LUMINANCE_SPREAD
    }


# JS injected per stage: returns element rects + webgl health + current copy.
INSPECT_JS = """
(function() {
  const rect = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    return { x: r.x, y: r.y, width: r.width, height: r.height, viewport_width: window.innerWidth };
  };
  const canvas = document.querySelector('#webgl-container canvas');
  let webgl_ok = false;
  if (canvas) {
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    webgl_ok = !!gl && !gl.isContextLost() && canvas.width > 0;
  }
  const headline = document.querySelector('#stage-headline');
  return {
    webgl_ok: webgl_ok,
    headline_text: headline ? headline.textContent.trim() : null,
    rects: {
      headline: rect('#stage-headline'),
      description: rect('#stage-description'),
      cta: rect('#stage-ctas') || rect('main section a[href*="#"]')
    }
  };
})()
"""

FPS_JS = """
(new Promise((resolve) => {
  let frames = 0;
  const t0 = performance.now();
  const tick = () => {
    frames++;
    if (performance.now() - t0 < 1000) requestAnimationFrame(tick);
    else resolve({ frames: frames, ms: performance.now() - t0 });
  };
  requestAnimationFrame(tick);
}))
"""


class ThreeQA:
    """Renders, screenshots, measures and judges 3D site builds. For real."""

    @classmethod
    async def run_qa(
        cls,
        demo_dir: str,
        viewports: Optional[List[Dict[str, int]]] = None
    ) -> Dict[str, Any]:
        if not demo_dir or not os.path.exists(demo_dir):
            return {
                "ok": False,
                "status": "failed",
                "tool": "qa_3d_site",
                "error": f"Demo directory not found: '{demo_dir}'"
            }

        reports_dir = os.path.join(demo_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        shots_dir = os.path.join(reports_dir, "screenshots")

        index_html = os.path.join(demo_dir, "index.html")
        stages_json = os.path.join(demo_dir, "stages.json")
        model_glb = os.path.join(demo_dir, "assets", "model.glb")

        checks = {
            "index_html_exists": os.path.exists(index_html),
            "stages_json_exists": os.path.exists(stages_json),
            "model_glb_exists": os.path.exists(model_glb),
            "sources_json_exists": os.path.exists(os.path.join(demo_dir, "SOURCES.json"))
        }

        stages_count = 0
        stages_data: List[Dict] = []
        if checks["stages_json_exists"]:
            try:
                with open(stages_json, "r", encoding="utf-8") as f:
                    stages_data = json.load(f)
                if isinstance(stages_data, list):
                    stages_count = len(stages_data)
            except Exception:
                stages_count = 0

        if not all([checks["index_html_exists"], checks["stages_json_exists"]]) or stages_count < 1:
            report = {
                "tool": "qa_3d_site",
                "demo_dir": demo_dir,
                "checks": checks,
                "stages_count": stages_count,
                "passed": False,
                "status": "failed",
                "notice": "Missing index.html or stages.json. Site cannot be rendered; no visual verification attempted."
            }
            cls._write_reports(reports_dir, report, None)
            return {"ok": False, "status": "failed", "tool": "qa_3d_site", "qa_report": report, "report_file": os.path.join(reports_dir, "qa.json")}

        # ---- Render + inspect in a real browser -------------------------------
        browser = await cls._acquire_browser()
        server_proc = None
        tab = None
        opened_tab_id = None
        http_port = None

        try:
            if browser["mode"] == "none":
                report = {
                    "tool": "qa_3d_site",
                    "demo_dir": demo_dir,
                    "checks": checks,
                    "stages_count": stages_count,
                    "environment": {"mode": "none"},
                    "screenshots_captured": 0,
                    "passed": False,
                    "status": "inconclusive",
                    "notice": ("NO BROWSER AVAILABLE. No page was rendered, no screenshot was taken, "
                               "no FPS was measured. This report contains zero visual verification. "
                               "Do not treat this build as visually validated."),
                    "human_review": {"required": True, "screenshots": []}
                }
                cls._write_reports(reports_dir, report, None)
                return {"ok": False, "status": "inconclusive", "tool": "qa_3d_site", "qa_report": report, "report_file": os.path.join(reports_dir, "qa.json")}

            # Serve the demo over localhost HTTP (ES modules + importmaps need http).
            http_port = _free_port()
            server_proc = await asyncio.create_subprocess_exec(
                "python3", "-m", "http.server", str(http_port), "--bind", "127.0.0.1",
                cwd=demo_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.sleep(0.6)
            base_url = f"http://127.0.0.1:{http_port}/index.html"

            # Open a dedicated tab so we never hijack the user's current page.
            new_target = _cdp_http(browser["port"], f"/json/new?{urllib.parse.quote(base_url)}", method="PUT")
            opened_tab_id = new_target.get("id") if isinstance(new_target, dict) else None
            ws_url = new_target.get("webSocketDebuggerUrl") if isinstance(new_target, dict) else None
            if not ws_url:
                for t in _cdp_http(browser["port"], "/json/list"):
                    if t.get("id") == opened_tab_id:
                        ws_url = t.get("webSocketDebuggerUrl")
                        break
            if not ws_url:
                raise RuntimeError("Could not obtain CDP websocket for QA tab")

            tab = _CdpTab(ws_url)
            await tab.command(5000, "Page.enable")
            # Make the page behave as foregrounded regardless of window focus:
            # background tabs throttle requestAnimationFrame, which dead-locks
            # stage-transition animations and skews FPS measurement.
            await tab.command(5002, "Emulation.setFocusEmulationEnabled", {"enabled": True})
            if browser["mode"] == "brave_debug":
                try:
                    _cdp_http(browser["port"], f"/json/activate/{opened_tab_id}")
                except Exception:
                    pass

            v_ports = viewports or DEFAULT_VIEWPORTS
            per_viewport: Dict[str, Any] = {}
            screenshots: List[str] = []
            all_stage_checks: List[bool] = []
            webgl_ok_overall = True
            app_ready = False
            fps_measured: Dict[str, float] = {}

            for vp in v_ports:
                vp_name = vp["name"]
                await tab.command(5100, "Emulation.setDeviceMetricsOverride", {
                    "width": vp["width"], "height": vp["height"],
                    "deviceScaleFactor": 1, "mobile": bool(vp.get("mobile", False))
                })
                # Reload so layout, resize handlers and pixel ratio all re-run clean.
                await tab.command(5101, "Page.navigate", {"url": base_url})

                # Wait until the three.js app has actually booted (renderer canvas
                # mounted in the DOM). Fixed sleeps hide slow CDN / module failures.
                ready = False
                for _ in range(60):  # up to 15s
                    probe = await tab.evaluate(
                        "!!document.querySelector('#webgl-container canvas')"
                    )
                    if probe:
                        ready = True
                        break
                    await asyncio.sleep(0.25)
                app_ready = app_ready or ready
                await asyncio.sleep(RENDER_SETTLE_S)

                inspect = await tab.evaluate(INSPECT_JS)
                if not inspect or not inspect.get("webgl_ok"):
                    webgl_ok_overall = False

                vp_shots_dir = os.path.join(shots_dir, vp_name)
                os.makedirs(vp_shots_dir, exist_ok=True)
                stage_results = []

                for i in range(stages_count):
                    # Switch stage by clicking its dot (real event path through app code).
                    await tab.evaluate(
                        f"(function() {{ const d = document.querySelectorAll('.view-dot')[{i}]; "
                        f"if (d) d.click(); return !!d; }})()"
                    )
                    await asyncio.sleep(STAGE_TRANSITION_MS / 1000)

                    inspect = await tab.evaluate(INSPECT_JS) or {}
                    png = await tab.screenshot_png()
                    if not png:
                        stage_results.append({"stage": i + 1, "screenshot": None, "error": "screenshot failed"})
                        all_stage_checks.append(False)
                        continue

                    shot_name = f"stage-{i + 1}.png"
                    shot_path = os.path.join(vp_shots_dir, shot_name)
                    with open(shot_path, "wb") as f:
                        f.write(png)
                    rel_shot = os.path.relpath(shot_path, demo_dir)
                    screenshots.append(rel_shot)

                    rects = inspect.get("rects", {})
                    contrast = {
                        region: _luminance_spread_of_region(png, rects.get(region))
                        for region in ("headline", "description", "cta")
                    }
                    expected = stages_data[i].get("headline", "") if i < len(stages_data) else ""
                    got = (inspect.get("headline_text") or "").strip()
                    headline_match = bool(expected) and got.lower() == expected.strip().lower()

                    contrast_pass = all(c.get("pass") for c in contrast.values() if c.get("measured"))
                    stage_ok = bool(inspect.get("webgl_ok")) and headline_match and contrast_pass
                    all_stage_checks.append(stage_ok)

                    stage_results.append({
                        "stage": i + 1,
                        "screenshot": rel_shot,
                        "webgl_ok": inspect.get("webgl_ok"),
                        "headline_match": headline_match,
                        "text_contrast": contrast,
                        "pass": stage_ok
                    })

                fps = await tab.evaluate(FPS_JS)
                if fps and fps.get("frames"):
                    fps_measured[vp_name] = round(fps["frames"] / max(1.0, fps["ms"] / 1000.0), 1)

                per_viewport[vp_name] = {"viewport": vp, "stages": stage_results}

            screenshots_ok = len(screenshots) == stages_count * len(v_ports)
            passed = (
                all(checks.values())
                and app_ready
                and screenshots_ok
                and webgl_ok_overall
                and all_stage_checks
                and all(all_stage_checks)
            )

            perf_report = {
                "tool": "qa_3d_site",
                "target_fps_desktop": 55,
                "target_fps_mobile": 30,
                "measured_fps": fps_measured,
                "fps_measured": bool(fps_measured),
                "targets_met": {
                    "desktop": fps_measured.get("desktop", 0) >= 55,
                    "mobile": fps_measured.get("mobile", 0) >= 30
                } if fps_measured else None,
                "verified": bool(fps_measured),
                "notice": ("FPS measured live via requestAnimationFrame in a real browser."
                           if fps_measured else "FPS could not be measured; no target was verified.")
            }

            failures = []
            if not app_ready:
                failures.append("three.js app never booted (no renderer canvas mounted) — module load failure or missing vendor files")
            if not screenshots_ok:
                failures.append(f"only {len(screenshots)}/{stages_count * len(v_ports)} expected screenshots captured")
            if not webgl_ok_overall:
                failures.append("WebGL context missing or lost in at least one viewport")
            if not all(all_stage_checks):
                bad = [i + 1 for i, ok in enumerate(all_stage_checks) if not ok]
                failures.append(f"visual checks failed on stages {bad} (see per-stage detail)")
            if not all(checks.values()):
                failures.append(f"file checks failed: {[k for k, v in checks.items() if not v]}")

            report = {
                "tool": "qa_3d_site",
                "demo_dir": demo_dir,
                "checks": checks,
                "stages_count": stages_count,
                "environment": {
                    "mode": browser["mode"],
                    "browser": browser.get("browser"),
                    "url_rendered": base_url
                },
                "screenshots_captured": len(screenshots),
                "visual_checks": {"per_viewport": per_viewport},
                "failures": failures,
                "passed": passed,
                "status": "success" if passed else "failed",
                "notice": (
                    f"Rendered in a real browser ({browser.get('browser')}), captured "
                    f"{len(screenshots)} screenshots, measured FPS, and ran pixel-level "
                    "text-legibility checks. Verdicts cite the listed screenshots."
                ),
                "human_review": {
                    "required": True,
                    "note": "Automated pixel checks catch legibility and rendering failures. "
                            "A human or vision-model pass over the listed screenshots is still "
                            "required before client-facing deploy.",
                    "screenshots": screenshots
                }
            }

            cls._write_reports(reports_dir, report, perf_report)
            return {
                "ok": passed,
                "status": report["status"],
                "tool": "qa_3d_site",
                "qa_report": report,
                "perf_report": perf_report,
                "report_file": os.path.join(reports_dir, "qa.json")
            }

        except Exception as e:
            logger.exception("three_qa render failed")
            report = {
                "tool": "qa_3d_site",
                "demo_dir": demo_dir,
                "checks": checks,
                "stages_count": stages_count,
                "environment": {"mode": browser["mode"] if browser else "unknown"},
                "screenshots_captured": 0,
                "passed": False,
                "status": "inconclusive",
                "error": str(e),
                "notice": f"Visual QA crashed mid-run ({e}). Nothing was verified. Treat as unverified."
            }
            cls._write_reports(reports_dir, report, None)
            return {"ok": False, "status": "inconclusive", "tool": "qa_3d_site", "qa_report": report, "report_file": os.path.join(reports_dir, "qa.json")}

        finally:
            if opened_tab_id is not None and browser.get("mode") == "brave_debug":
                try:
                    _cdp_http(browser["port"], f"/json/close/{opened_tab_id}")
                except Exception:
                    pass
            if server_proc:
                try:
                    server_proc.terminate()
                except Exception:
                    pass
            if browser.get("cleanup"):
                try:
                    browser["cleanup"]()
                except Exception:
                    pass

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _write_reports(reports_dir: str, qa_report: Optional[Dict], perf_report: Optional[Dict]) -> None:
        with open(os.path.join(reports_dir, "qa.json"), "w", encoding="utf-8") as f:
            json.dump(qa_report or {}, f, indent=2)
        with open(os.path.join(reports_dir, "perf.json"), "w", encoding="utf-8") as f:
            json.dump(perf_report if perf_report is not None else {
                "verified": False,
                "notice": "No performance measurement was performed in this run."
            }, f, indent=2)

    @staticmethod
    async def _acquire_browser() -> Dict[str, Any]:
        """
        Prefer the operator's already-running CDP browser (port 9222); fall back to
        spawning a throwaway headless Chromium. Returns {"mode", "port", "browser", "cleanup"}.
        mode == "none" means no visual QA is possible and the report must say so.
        """
        # 1. Operator's Brave / Chrome on the standard debug port
        try:
            version = _cdp_http(9222, "/json/version", timeout=2.0)
            if isinstance(version, dict) and version.get("Browser"):
                return {"mode": "brave_debug", "port": 9222, "browser": version["Browser"], "cleanup": None}
        except Exception:
            pass
        if BraveCDPBridge is not None:
            version = await BraveCDPBridge.get_version()
            if isinstance(version, dict) and not version.get("error") and version.get("Browser"):
                return {"mode": "brave_debug", "port": 9222, "browser": version["Browser"], "cleanup": None}

        # 2. Spawn a throwaway headless browser
        for binary in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave"):
            bin_path = shutil.which(binary)
            if not bin_path:
                continue
            port = _free_port()
            profile = tempfile.mkdtemp(prefix="ghost-qa-profile-")
            try:
                proc = await asyncio.create_subprocess_exec(
                    bin_path,
                    "--headless=new",
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--hide-scrollbars",
                    "--enable-unsafe-swiftshader",
                    "--window-size=1440,900",
                    "about:blank",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
            except Exception:
                continue
            for _ in range(40):
                await asyncio.sleep(0.25)
                try:
                    version = _cdp_http(port, "/json/version", timeout=1.0)
                    if isinstance(version, dict) and version.get("Browser"):
                        def cleanup(proc=proc, profile=profile):
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            shutil.rmtree(profile, ignore_errors=True)
                        return {"mode": "headless_spawn", "port": port, "browser": version["Browser"], "cleanup": cleanup}
                except Exception:
                    continue
            try:
                proc.kill()
            except Exception:
                pass
            shutil.rmtree(profile, ignore_errors=True)

        return {"mode": "none", "port": None, "browser": None, "cleanup": None}
