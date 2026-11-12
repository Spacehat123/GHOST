"""Modular Built-in Workspace Framework & Native Module Manager for GHOST.

Supports 12 native internal modules:
1. pdf: PDF viewer, thumbnails, text extraction, page analysis.
2. editor: Text & Markdown editor, syntax highlighting, search/replace, save.
3. code: Multi-file code workspace, file tree, git status, syntax highlighting.
4. image: Image preview, zoom/pan, metadata, annotation, vision pipeline bridge.
5. camera: WebRTC camera preview, snapshot capture, vision pipeline bridge.
6. audio: Audio playback, microphone recording, waveform visualizer, voice preview.
7. video: Video playback, frame extraction, seeking.
8. browser: Internal research browser, URL navigation, reference extraction.
9. files: File manager, directory navigation, internal opening, safe file operations.
10. terminal: Integrated command runner, streaming output, permissions.
11. charts: CSV analysis, financial data charting (NSE/BSE/ADRs), interactive tables.
12. sysmon: Real-time system telemetry (CPU, RAM, GPU, storage, process inspection).
"""

import os
import mimetypes
import shutil
import time
import subprocess
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("ghost.workspace")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
ALLOWED_ROOTS = [
    REPO_DIR,
    os.path.expanduser("~"),
    "/tmp"
]

MODULE_MAP = {
    ".pdf": "pdf",
    ".txt": "editor",
    ".md": "editor",
    ".markdown": "editor",
    ".rst": "editor",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".html": "code",
    ".css": "code",
    ".json": "code",
    ".yaml": "code",
    ".yml": "code",
    ".sh": "code",
    ".toml": "code",
    ".rs": "code",
    ".c": "code",
    ".cpp": "code",
    ".h": "code",
    ".scad": "code",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".svg": "image",
    ".gif": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".mp4": "video",
    ".webm": "video",
    ".mkv": "video",
    ".mov": "video",
    ".csv": "charts",
    ".tsv": "charts",
}


@dataclass
class WorkspaceTab:
    id: str
    module: str
    title: str
    file_path: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=dict)
    opened_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "title": self.title,
            "file_path": self.file_path,
            "state": self.state,
            "opened_at": self.opened_at,
        }


class WorkspaceManager:
    """Singleton workspace manager tracking open tabs, active module, and document context."""

    _instance: Optional["WorkspaceManager"] = None

    @classmethod
    def get_instance(cls) -> "WorkspaceManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.tabs: List[WorkspaceTab] = []
        self.active_tab_id: Optional[str] = None
        self.active_module: str = "none"  # "none" when orbital core is full central
        self.active_file: Optional[str] = None
        self.active_context_summary: str = ""
        self.recent_actions: List[Dict[str, Any]] = []

    def resolve_module_for_path(self, file_path: str) -> str:
        """Determines the appropriate internal module for a file."""
        _, ext = os.path.splitext(file_path.lower())
        return MODULE_MAP.get(ext, "editor")

    def open_module(
        self,
        module: str,
        title: Optional[str] = None,
        file_path: Optional[str] = None,
        initial_state: Optional[Dict[str, Any]] = None
    ) -> WorkspaceTab:
        if file_path:
            file_path = os.path.abspath(os.path.expanduser(file_path))
            if not title:
                title = os.path.basename(file_path)

        if not title:
            title = module.upper()

        # Check if tab already open
        for tab in self.tabs:
            if tab.file_path and file_path and tab.file_path == file_path:
                self.active_tab_id = tab.id
                self.active_module = tab.module
                self.active_file = tab.file_path
                return tab

        tab_id = f"tab_{int(time.time() * 1000)}"
        tab = WorkspaceTab(
            id=tab_id,
            module=module,
            title=title,
            file_path=file_path,
            state=initial_state or {},
            opened_at=time.time()
        )
        self.tabs.append(tab)
        self.active_tab_id = tab_id
        self.active_module = module
        self.active_file = file_path
        self._update_context_summary(tab)

        self.recent_actions.append({
            "action": "open_module",
            "module": module,
            "file_path": file_path,
            "timestamp": time.time()
        })
        return tab

    def close_tab(self, tab_id: str) -> bool:
        idx = next((i for i, t in enumerate(self.tabs) if t.id == tab_id), None)
        if idx is None:
            return False

        closed_tab = self.tabs.pop(idx)
        if self.active_tab_id == tab_id:
            if self.tabs:
                next_tab = self.tabs[-1]
                self.active_tab_id = next_tab.id
                self.active_module = next_tab.module
                self.active_file = next_tab.file_path
                self._update_context_summary(next_tab)
            else:
                self.active_tab_id = None
                self.active_module = "none"
                self.active_file = None
                self.active_context_summary = ""

        return True

    def close_all(self) -> None:
        self.tabs.clear()
        self.active_tab_id = None
        self.active_module = "none"
        self.active_file = None
        self.active_context_summary = ""

    def set_active_tab(self, tab_id: str) -> Optional[WorkspaceTab]:
        tab = next((t for t in self.tabs if t.id == tab_id), None)
        if not tab:
            return None
        self.active_tab_id = tab.id
        self.active_module = tab.module
        self.active_file = tab.file_path
        self._update_context_summary(tab)
        return tab

    def _update_context_summary(self, tab: WorkspaceTab) -> None:
        if not tab.file_path or not os.path.exists(tab.file_path):
            self.active_context_summary = f"Module: {tab.module.upper()}"
            return

        try:
            size_kb = round(os.path.getsize(tab.file_path) / 1024, 1)
            summary = f"Module: {tab.module.upper()} | File: {tab.file_path} ({size_kb} KB)"

            if tab.module in ("editor", "code"):
                # Extract snippet for LLM context
                with open(tab.file_path, "r", encoding="utf-8", errors="ignore") as f:
                    preview = f.read(500).strip()
                    summary += f"\nPreview:\n{preview[:300]}..."
            elif tab.module == "pdf":
                summary += " | PDF document active in native viewer."
            elif tab.module == "image":
                summary += " | Image active in native inspection viewer."
            elif tab.module == "charts":
                summary += " | Data & Financial charts active."
            elif tab.module == "camera":
                summary += " | Live camera stream online."

            self.active_context_summary = summary
        except Exception as e:
            self.active_context_summary = f"Module: {tab.module} | File: {tab.file_path} ({e})"

    def get_state(self) -> Dict[str, Any]:
        return {
            "active_module": self.active_module,
            "is_open": self.active_module != "none",
            "active_tab_id": self.active_tab_id,
            "active_file": self.active_file,
            "context_summary": self.active_context_summary,
            "tabs": [t.to_dict() for t in self.tabs],
            "total_open": len(self.tabs)
        }

    # -------------------------------------------------------------
    # Secure File Operations
    # -------------------------------------------------------------
    def _is_safe_path(self, path: str) -> bool:
        abs_p = os.path.abspath(os.path.expanduser(path))
        # Check against allowed roots
        return any(abs_p.startswith(root) for root in ALLOWED_ROOTS)

    def read_file(self, file_path: str, max_bytes: int = 1024 * 1024 * 5) -> Dict[str, Any]:
        abs_p = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(abs_p):
            return {"ok": False, "error": f"File does not exist: {abs_p}"}

        stat = os.stat(abs_p)
        mime, _ = mimetypes.guess_type(abs_p)
        is_binary = False
        content = ""

        # Check explicit binary extensions
        ext = os.path.splitext(abs_p)[1].lower()
        if ext in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".mkv", ".zip", ".tar", ".gz", ".bin", ".iso"):
            is_binary = True
            if ext == ".pdf":
                # For PDF, extract real textual content using pdftotext so LLM and text inspection get authentic document text
                try:
                    res = subprocess.run(["pdftotext", abs_p, "-"], capture_output=True, text=True, timeout=8)
                    if res.returncode == 0:
                        content = res.stdout[:max_bytes]
                except Exception:
                    content = ""
        else:
            try:
                with open(abs_p, "rb") as f:
                    raw = f.read(min(stat.st_size, max_bytes))
                    # Check for null bytes
                    if b"\x00" in raw[:1024]:
                        is_binary = True
                        content = ""
                    else:
                        content = raw.decode("utf-8", errors="replace")
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {
            "ok": True,
            "path": abs_p,
            "name": os.path.basename(abs_p),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "mime": mime or "text/plain",
            "is_binary": is_binary,
            "content": content,
            "recommended_module": self.resolve_module_for_path(abs_p)
        }

    def save_file(self, file_path: str, content: str, create_backup: bool = True) -> Dict[str, Any]:
        abs_p = os.path.abspath(os.path.expanduser(file_path))
        if not self._is_safe_path(abs_p):
            return {"ok": False, "error": f"Path is outside permitted roots: {abs_p}"}

        try:
            os.makedirs(os.path.dirname(abs_p), exist_ok=True)
            if create_backup and os.path.exists(abs_p):
                backup_path = abs_p + ".bak"
                shutil.copy2(abs_p, backup_path)

            with open(abs_p, "w", encoding="utf-8") as f:
                f.write(content)

            stat = os.stat(abs_p)
            return {
                "ok": True,
                "path": abs_p,
                "size": stat.st_size,
                "mtime": stat.st_mtime
            }
        except Exception as e:
            logger.error(f"Failed to save file {abs_p}: {e}")
            return {"ok": False, "error": str(e)}

    def delete_file(self, file_path: str, confirmed: bool = False) -> Dict[str, Any]:
        """Deletes file or directory. REQUIRES confirmed=True."""
        abs_p = os.path.abspath(os.path.expanduser(file_path))
        if not self._is_safe_path(abs_p):
            return {"ok": False, "error": f"Path is outside permitted roots: {abs_p}"}

        if not confirmed:
            return {
                "ok": False,
                "status": "confirmation_required",
                "error": "Destructive operation requires explicit confirmation flag 'confirmed=True'."
            }

        if not os.path.exists(abs_p):
            return {"ok": False, "error": f"Path not found: {abs_p}"}

        try:
            if os.path.isdir(abs_p):
                shutil.rmtree(abs_p)
            else:
                os.remove(abs_p)
            return {"ok": True, "path": abs_p, "message": "Successfully deleted"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_directory(self, dir_path: Optional[str] = None) -> Dict[str, Any]:
        target = os.path.abspath(os.path.expanduser(dir_path or REPO_DIR))
        if not os.path.isdir(target):
            return {"ok": False, "error": f"Not a directory: {target}"}

        entries = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if entry.name.startswith(".git") and entry.is_dir():
                        continue
                    try:
                        stat = entry.stat()
                        is_dir = entry.is_dir()
                        entries.append({
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": is_dir,
                            "size": stat.st_size if not is_dir else 0,
                            "mtime": stat.st_mtime,
                            "module": self.resolve_module_for_path(entry.path) if not is_dir else "files"
                        })
                    except Exception:
                        continue

            # Sort dirs first, then files alphabetically
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return {
                "ok": True,
                "path": target,
                "parent": os.path.dirname(target) if target != "/" else None,
                "entries": entries,
                "count": len(entries)
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -------------------------------------------------------------
    # Integrated Terminal Execution
    # -------------------------------------------------------------
    def execute_terminal(self, command: str, cwd: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
        working_dir = os.path.abspath(os.path.expanduser(cwd or REPO_DIR))
        if not os.path.isdir(working_dir):
            working_dir = REPO_DIR

        # Security check: disallow destructive root commands
        disallowed = ("rm -rf /", "mkfs", ":(){ :|:& };:", "dd if=/dev/zero of=/dev/sd")
        if any(d in command for d in disallowed):
            return {"ok": False, "error": "Command blocked by security guardrails."}

        try:
            start_t = time.time()
            proc = subprocess.run(
                command,
                shell=True,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            elapsed = round(time.time() - start_t, 3)
            return {
                "ok": True,
                "command": command,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "elapsed": elapsed,
                "cwd": working_dir
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Command timed out after {timeout}s"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -------------------------------------------------------------
    # Native PDF Inspection & Extraction
    # -------------------------------------------------------------
    def get_pdf_info(self, file_path: str) -> Dict[str, Any]:
        abs_p = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(abs_p):
            return {"ok": False, "error": f"PDF does not exist: {abs_p}"}

        info: Dict[str, Any] = {
            "ok": True,
            "path": abs_p,
            "name": os.path.basename(abs_p),
            "size": os.path.getsize(abs_p),
            "pages": 1,
            "title": "",
            "author": "",
            "creator": "",
            "producer": "",
            "page_size": "",
            "full_text": "",
            "text_by_page": {}
        }

        # 1. Run pdfinfo
        try:
            res_info = subprocess.run(["pdfinfo", abs_p], capture_output=True, text=True, timeout=5)
            if res_info.returncode == 0:
                for line in res_info.stdout.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "pages":
                            try:
                                info["pages"] = int(v)
                            except ValueError:
                                pass
                        elif k == "title":
                            info["title"] = v
                        elif k == "author":
                            info["author"] = v
                        elif k == "page size":
                            info["page_size"] = v
                        elif k == "creator":
                            info["creator"] = v
                        elif k == "producer":
                            info["producer"] = v
        except Exception as e:
            logger.warning(f"pdfinfo failed: {e}")

        # 2. Run pdftotext
        try:
            res_txt = subprocess.run(["pdftotext", abs_p, "-"], capture_output=True, text=True, timeout=8)
            if res_txt.returncode == 0:
                info["full_text"] = res_txt.stdout
                pages_count = info["pages"]
                for p in range(1, min(pages_count + 1, 50)):
                    p_res = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), abs_p, "-"], capture_output=True, text=True, timeout=3)
                    if p_res.returncode == 0:
                        info["text_by_page"][str(p)] = p_res.stdout.strip()
        except Exception as e:
            logger.warning(f"pdftotext failed: {e}")

        return info

    # -------------------------------------------------------------
    # Real Host System Telemetry
    # -------------------------------------------------------------
    def get_system_telemetry(self) -> Dict[str, Any]:
        from ghost.collectors.system_collector import SystemCollector
        try:
            vitals = SystemCollector().sample()
            vitals_dict = vitals.to_dict()
        except Exception as e:
            vitals_dict = {"cpu_percent": 0.0, "ram_percent": 0.0, "error": str(e)}

        processes = []
        try:
            proc_out = subprocess.run(
                ["ps", "-eo", "pid,comm,%cpu,%mem,user", "--sort=-%cpu"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if proc_out.returncode == 0:
                lines = proc_out.stdout.strip().splitlines()
                for line in lines[1:26]:
                    parts = line.split(None, 4)
                    if len(parts) >= 5:
                        processes.append({
                            "pid": parts[0],
                            "name": parts[1],
                            "cpu": parts[2],
                            "mem": parts[3],
                            "user": parts[4]
                        })
        except Exception:
            pass

        uname = os.uname()
        return {
            "ok": True,
            "vitals": vitals_dict,
            "processes": processes,
            "host": {
                "sysname": uname.sysname,
                "nodename": uname.nodename,
                "release": uname.release,
                "machine": uname.machine,
                "loadavg": list(os.getloadavg()) if hasattr(os, "getloadavg") else []
            }
        }

    # -------------------------------------------------------------
    # Research Web Reference Extractor
    # -------------------------------------------------------------
    def extract_web_reference(self, url: str) -> Dict[str, Any]:
        import urllib.request
        from bs4 import BeautifulSoup
        target_url = url.strip()
        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            target_url = "https://" + target_url

        try:
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                html_data = response.read(1024 * 512).decode("utf-8", errors="replace")

            soup = BeautifulSoup(html_data, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else target_url

            for s in soup(["script", "style", "noscript", "svg"]):
                s.decompose()

            meta_desc = ""
            desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if desc_tag and desc_tag.get("content"):
                meta_desc = desc_tag["content"].strip()

            headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"])[:12] if h.get_text(strip=True)]
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")[:8] if len(p.get_text(strip=True)) > 20]

            links = []
            for a in soup.find_all("a", href=True)[:15]:
                href = a["href"]
                text = a.get_text(strip=True)
                if text and not href.startswith("#") and not href.startswith("javascript:"):
                    links.append({"text": text[:60], "href": href})

            return {
                "ok": True,
                "url": target_url,
                "title": title,
                "description": meta_desc,
                "headings": headings,
                "paragraphs": paragraphs,
                "links": links
            }
        except Exception as e:
            return {"ok": False, "url": target_url, "error": str(e)}

    # -------------------------------------------------------------
    # Real Image Metadata & Metrics
    # -------------------------------------------------------------
    def inspect_image_details(self, image_path: str) -> Dict[str, Any]:
        abs_p = os.path.abspath(os.path.expanduser(image_path))
        if not os.path.exists(abs_p):
            return {"ok": False, "error": f"Image file not found: {abs_p}"}

        try:
            from PIL import Image, ImageStat
            with Image.open(abs_p) as img:
                w, h = img.size
                mode = img.mode
                fmt = img.format or "UNKNOWN"
                stat = ImageStat.Stat(img)
                mean_lum = round(sum(stat.mean) / len(stat.mean), 1) if stat.mean else 0.0
                file_sz = os.path.getsize(abs_p)

            return {
                "ok": True,
                "path": abs_p,
                "name": os.path.basename(abs_p),
                "width": w,
                "height": h,
                "dimensions": f"{w} x {h}",
                "aspect_ratio": f"{round(w / h, 2)}:1" if h else "N/A",
                "format": fmt,
                "mode": mode,
                "mean_luminance": mean_lum,
                "size_kb": round(file_sz / 1024, 1),
                "meta_str": f"{w} x {h} px | {fmt} | {mode} | {round(file_sz / 1024, 1)} KB"
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
