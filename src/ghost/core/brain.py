"""OmniRoute Streaming Brain for Ghost with Native Function Calling.
Uses AsyncOpenAI connected to OmniRoute with full tool calling schemas for Brave CDP,
WhatsApp Web, Media/Volume, System Apps, REA, and Directives.
"""
import asyncio
import os
import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional
from openai import AsyncOpenAI
from ghost.core.follow import DirectiveManager
from ghost.crm.db import CRMDatabase
from ghost.mcp.bridge import (
    ShellExecutor,
    BraveCDPBridge,
    WhatsAppBridge,
    MediaBridge,
    SystemAppBridge,
    ReaBridge,
    WebSearchBridge,
    DatabaseBridge,
    GitHubBridge,
    AgentDesktopBridge,
    GitBridge
)

logger = logging.getLogger("ghost.brain")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))

DOC_FILE_EXTENSIONS = {".md", ".txt", ".pdf", ".html", ".json", ".docx",
                       ".png", ".jpg", ".jpeg", ".webp", ".py", ".log", ".csv"}


def resolve_local_file(raw: str):
    """Resolve a possibly-repo-relative local path to an ABSOLUTE one.
    The operator says 'open docs/OPERATOR_MANUAL.md' — a bare relative path
    is not a URL and not a file any tool can open from an arbitrary CWD.
    Returns (absolute_path_or_None, tried_list). NEVER invents a path."""
    if not raw:
        return None, []
    expanded = os.path.expanduser(str(raw).strip().strip("'\""))
    tried = []
    for cand in (expanded,
                 os.path.join(REPO_DIR, expanded.lstrip("/")),
                 os.path.join(os.getcwd(), expanded.lstrip("/"))):
        cand = os.path.abspath(cand)
        tried.append(cand)
        if os.path.isfile(cand):
            return cand, tried
    return None, tried
CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data"))
OMNIROUTE_CONFIG_PATH = os.path.join(CONFIGS_DIR, "omniroute.json")
# The rolling chat transcript. Env-overridable so TESTS never write into the
# operator's live history (the 2026-11-08 incident: test fixtures leaked into
# data/conversation_history.json and the operator read them as Ghost's doings).
CONVERSATION_HISTORY_PATH = os.environ.get("GHOST_CONVERSATION_HISTORY") or os.path.join(
    DATA_DIR, "conversation_history.json")
OBSIDIAN_SESSION_MEMORY_PATH = os.environ.get("OBSIDIAN_SESSION_MEMORY_PATH", os.path.expanduser("~/Obsidian Vault/Ghost/Session Memory.md"))

GHOST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "desktop_control",
            "description": "Universal Hyprland desktop, file system, and Quickshell controller. Move/copy/rename files, switch workspaces, focus/resize/close windows, launch apps, or set wallpapers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "switch_workspace",
                            "move_to_workspace",
                            "focus_window",
                            "close_window",
                            "resize_window",
                            "toggle_floating",
                            "toggle_fullscreen",
                            "set_wallpaper",
                            "toggle_wallpaper",
                            "launch_app",
                            "read_context",
                            "request_authorization",
                            "move_file",
                            "copy_file",
                            "delete_file",
                            "quickshell_ipc",
                            "hyprctl_dispatch"
                        ],
                        "description": "Desktop or file action to execute"
                    },
                    "target": {
                        "type": "string",
                        "description": "Target identifier (workspace index like '3', app name, source file/folder path for file operations, or quickshell target)"
                    },
                    "args": {
                        "type": "string",
                        "description": "Additional arguments (destination path when moving/copying files, workspace index when launching an app, or resize delta)"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Universal media playback, search, track jumping, and volume controller across Spotify, browser audio, and all MPRIS players. Uses DBus OpenUri to load and play any song/artist/playlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "play_pause", "play_query", "next", "previous", "volume_up", "volume_down", "set_volume", "get_track"],
                        "description": "Media or volume operation to perform"
                    },
                    "query": {
                        "type": "string",
                        "description": "Song name, artist, playlist, or Spotify URI if action is play_query (e.g. 'Bohemian Rhapsody', 'Daft Punk', 'spotify:collection:tracks')"
                    },
                    "volume": {
                        "type": "string",
                        "description": "Volume level or delta (e.g. '+5%', '-5%', '50%')"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_action",
            "description": "Universal Brave CDP browser automation primitive on port 9222. Opens/closes/focuses tabs, lists tabs, clicks elements/buttons by text or CSS, and types text into any web app (including WhatsApp Web, YouTube, Discord).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open_url", "close_tab", "focus_tab", "list_tabs", "click_and_type", "whatsapp_action"],
                        "description": "Browser action to execute"
                    },
                    "url": {
                        "type": "string",
                        "description": "Destination URL or LOCAL FILE PATH for open_url (repo-relative paths like docs/FILE.md are resolved to file:// automatically; missing files return an honest error — never claim success without reading the result)"
                    },
                    "target": {
                        "type": "string",
                        "description": "Tab match (keyword or ID), selector/label to click, or WhatsApp contact/operation"
                    },
                    "value": {
                        "type": "string",
                        "description": "Text to type into an element or message text for WhatsApp"
                    },
                    "press_enter": {
                        "type": "boolean",
                        "description": "Whether to press Enter after typing text"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "capture_screen",
            "description": "Takes a static screenshot image of the desktop screen. ONLY use this when the user explicitly requests to take, save, or inspect a screenshot file. DO NOT use this for locating UI elements, clicking, pointing, drawing, or reading web pages (use screen_draw or browser_action instead).",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "enum": ["full", "active_window"],
                        "description": "Capture region ('full' desktop or 'active_window')"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_action",
            "description": "Read or write Wayland system clipboard contents using wl-paste / wl-copy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "Clipboard operation"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to write if operation is write"
                    }
                },
                "required": ["operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "input_simulation",
            "description": "Simulate keyboard shortcuts, text typing, or mouse clicks into native desktop applications or games without clean APIs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["key", "shortcut", "type_text", "click"],
                        "description": "Input action type"
                    },
                    "keys": {
                        "type": "string",
                        "description": "Key name (e.g. 'Return', 'space', 'Control_L+c') or text to type"
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate for mouse click"
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate for mouse click"
                    },
                    "window_query": {
                        "type": "string",
                        "description": "Optional window class or title to focus before simulating input (e.g. 'Spotify', 'kitty')"
                    }
                },
                "required": ["type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_notify",
            "description": "Send a native desktop notification to the user's HUD via notify-send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title"
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification body text"
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "normal", "critical"],
                        "description": "Urgency level"
                    }
                },
                "required": ["title", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screen_draw",
            "description": "Wayland transparent desktop overlay drawing primitive (Arch Linux + Hyprland). Draws vector shapes, arrows, highlights, text callouts, or mathematical solution cards directly over the desktop with 100% click-through event transparency. Automatically resolves window and web DOM coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["draw", "clear", "undo", "redo", "erase", "list", "target_and_draw"],
                        "description": "Overlay action to perform."
                    },
                    "target": {
                        "type": "object",
                        "description": "Optional precision target to locate before drawing (uses Hyprland window geometry, Brave DOM CDP, or cursor).",
                        "properties": {
                            "type": {"type": "string", "enum": ["active_window", "window_class", "brave_dom", "cursor", "absolute"]},
                            "query": {"type": "string", "description": "Window class name, title substring, or DOM CSS selector"}
                        }
                    },
                    "elements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["circle", "ellipse", "rect", "arrow", "line", "freehand", "highlight", "text", "math_card", "graph_plot"]},
                                "coords": {"type": "array", "items": {"type": "number"}, "description": "[x, y] or [x1, y1, x2, y2] or [x, y, w, h] (normalized 0-1 or pixel)"},
                                "color": {"type": "string", "description": "Hex or rgba color e.g. '#00FF66' or 'rgba(0, 255, 100, 0.8)'"},
                                "fill": {"type": "string", "description": "Optional fill color string"},
                                "width": {"type": "number", "description": "Stroke line width in pixels"},
                                "text": {"type": "string", "description": "Text annotation content or card title"},
                                "font_size": {"type": "number", "description": "Font size for text annotations"},
                                "duration": {"type": "number", "description": "Auto-fade duration in seconds (0 for persistent until cleared)"}
                            },
                            "required": ["type"]
                        }
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "math_solve",
            "description": "Local mathematics engine with safe AST parsing (zero eval) and independent verification. Solves equations, simplifies radicals/fractions, computes derivatives and integrals, and plots function curves with step-by-step reasoning and optional screen projection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression or equation (e.g. 'sqrt(25 + 144)', 'x**2 - 5*x + 6 = 0', 'diff(sin(x)*exp(x), x)', 'integrate(x**2, x)')."
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["solve", "evaluate", "simplify", "differentiate", "integrate", "factor", "expand", "plot"],
                        "description": "Mathematical operation to perform. Defaults to evaluate or solve."
                    },
                    "variable": {
                        "type": "string",
                        "description": "Variable of interest (default 'x')."
                    },
                    "draw_on_screen": {
                        "type": "boolean",
                        "description": "If true, projects the verified solution or function curve directly onto the user's desktop screen overlay."
                    },
                    "duration": {
                        "type": "number",
                        "description": "Auto-fade duration in seconds for on-screen projection (default 6.0)."
                    }
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rea_action",
            "description": "Reverse engineering, binary disassembly, and static analysis using REA (morluto/rea). Inspect binaries, decompile procedures, analyze Electron/JS, trace UI selectors, and view capabilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["analyze", "decompile", "doctor", "capabilities", "xrefs", "search", "trace"],
                        "description": "REA reverse engineering command"
                    },
                    "target": {
                        "type": "string",
                        "description": "Target file path, address, symbol, or query"
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional additional command flags or arguments"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web for facts, documentation, news, or answers. Returns concise titles, snippets, and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Web search query"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "database_query",
            "description": "Execute read-only SQL queries (SELECT, PRAGMA) against local SQLite database files (e.g. '~/.omniroute/storage.sqlite') or inspect schema tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db_path": {
                        "type": "string",
                        "description": "Path to SQLite database file"
                    },
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute (read-only queries only)"
                    }
                },
                "required": ["db_path", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_action",
            "description": "Inspect GitHub repositories, search code/repos, view file contents, list open issues, or look up user profiles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get_repo", "search_repos", "get_file", "list_issues", "get_user"],
                        "description": "GitHub action to perform"
                    },
                    "repo": {
                        "type": "string",
                        "description": "Target repository in 'owner/repo' format"
                    },
                    "path": {
                        "type": "string",
                        "description": "File path within repository for get_file"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query or username"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crm_get_client",
            "description": "Anti-Hallucination CRM read: Retrieve client record, deal size, preferred channel, and interaction history from local CRM and Obsidian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Unique client slug (e.g. 'le-mirador-resort')"
                    }
                },
                "required": ["slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crm_update_client",
            "description": "Create or update client details, stage status, preferred channel, deal value, and notes in CRM and Obsidian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Client slug"
                    },
                    "business_name": {
                        "type": "string",
                        "description": "Official business name"
                    },
                    "contact_name": {
                        "type": "string",
                        "description": "Decision maker / primary contact"
                    },
                    "email": {
                        "type": "string",
                        "description": "Verified email address"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Direct phone or mobile"
                    },
                    "preferred_channel": {
                        "type": "string",
                        "enum": ["email", "whatsapp", "slack", "phone"],
                        "description": "Client communication channel preference"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["unscouted", "scouted", "demo_built", "pitch_ready", "pitch_sent", "negotiating", "closed_won", "closed_lost"],
                        "description": "Pipeline stage status"
                    },
                    "deal_value": {
                        "type": "number",
                        "description": "Agreed or estimated deal value in USD"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Recon findings, aesthetic archetype, or strategic notes"
                    }
                },
                "required": ["slug", "business_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crm_list_pipeline",
            "description": "List all active clients in the sales pipeline with deal stages and values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["unscouted", "scouted", "demo_built", "pitch_ready", "pitch_sent", "negotiating", "closed_won", "closed_lost"],
                        "description": "Optional status filter"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_luxury_demo",
            "description": "THE route for every demo build (binding — never hand-write demo HTML via system_run). Runs the demo state machine: Maps recon + REAL photo staging with provenance, DESIGN_BRIEF with the editorial house style and vendored svg.js, sectional build, CDP visual QA at two viewports, and the gated Vercel deploy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_name": {
                        "type": "string",
                        "description": "Name of the establishment (e.g. 'Obsidian Dining Lounge')"
                    },
                    "archetype": {
                        "type": "integer",
                        "enum": [1, 2, 3, 4],
                        "description": "Luxury Archetype (1: Michelin Dark, 2: Coastal Minimalist, 3: Heritage Luxury, 4: High-Energy Bistro)"
                    },
                    "tagline": {
                        "type": "string",
                        "description": "Atmospheric marketing tagline"
                    },
                    "menu_items": {
                        "type": "array",
                        "description": "Optional list of menu items [{'category', 'title', 'desc', 'price'}]",
                        "items": {
                            "type": "object"
                        }
                    },
                    "contact_info": {
                        "type": "object",
                        "description": "Optional contact details",
                        "properties": {
                            "phone": {"type": "string"},
                            "email": {"type": "string"},
                            "address": {"type": "string"},
                            "whatsapp": {"type": "string"}
                        }
                    }
                },
                "required": ["restaurant_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "email_check_inbox",
            "description": "Check agency Gmail inbox via IMAP SSL (pranav31207@gmail.com). Fetches recent unread client and prospect emails with sender, subject, and body snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unread_only": {
                        "type": "boolean",
                        "description": "Whether to fetch only unread messages (default True)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of messages to fetch (default 5)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_premium_resources",
            "description": "Inspect curated premium design and motion resources (KokonutUI, Magic UI, React Bits, Anime.js, Motion, Rive, Limora, Bklit) to view component patterns, CDN links, and usage rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": "Optional specific resource identifier (e.g. 'kokonut_ui', 'magic_ui', 'anime_js', 'motion', 'react_bits', 'rive', 'limora', 'bklit'). If omitted, lists all 8 resources."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deploy_demo_vercel",
            "description": "Deploy a prospect demo directory (demos/<slug>) or project to Vercel production using bunx vercel. Requires VERCEL_TOKEN in configs/vercel.json and user Tactical Authorization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Unique demo slug (e.g. 'chateau-bel-air-dining', 'obsidian-reserve')"
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Optional custom Vercel project name"
                    }
                },
                "required": ["slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vercel_check_token",
            "description": "Probe and validate the Vercel Personal Access Token via https://api.vercel.com/v2/user. Detects if the 30-day token is active or expired (HTTP 401/403).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "figma_inspect_file",
            "description": "POST-CONTRACT ONLY ($1,000+ accounts). Fetch document tree, node layout, and component metadata from a Figma file key via Figma REST API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_key": {
                        "type": "string",
                        "description": "Figma file key from URL (e.g. 'aBc123XYZ')"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Optional document tree depth traversal limit (default 2)"
                    }
                },
                "required": ["file_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "figma_extract_tokens",
            "description": "POST-CONTRACT ONLY ($1,000+ accounts). Extract color palettes, typography styles, and component radii from a Figma design file and convert them into Tailwind CSS variables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_key": {
                        "type": "string",
                        "description": "Figma file key"
                    }
                },
                "required": ["file_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "framer_sync_cms",
            "description": "POST-CONTRACT ONLY ($1,000+ accounts). Push structured menu items, client reviews, or portfolio deliverables into a Framer CMS collection via Framer API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "collection_id": {
                        "type": "string",
                        "description": "Framer CMS collection ID"
                    },
                    "items": {
                        "type": "array",
                        "description": "List of items/records to sync to Framer CMS",
                        "items": {
                            "type": "object"
                        }
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Optional Framer Project ID (falls back to configs/framer.json)"
                    }
                },
                "required": ["collection_id", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "framer_generate_code_component",
            "description": "POST-CONTRACT ONLY ($1,000+ accounts). Generate a production-ready Framer React code component with Motion physics, spring transitions, and Framer property controls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component_name": {
                        "type": "string",
                        "description": "Name of the component (e.g. 'TastingCard', 'SommelierBadge', 'BookingModal')"
                    },
                    "archetype": {
                        "type": "integer",
                        "description": "Luxury Archetype index (1: Michelin, 2: Coastal Minimalist, 3: Heritage Luxury, 4: High-Energy Bistro)",
                        "enum": [1, 2, 3, 4]
                    },
                    "motion_spring": {
                        "type": "boolean",
                        "description": "Whether to apply spring physics (default True)"
                    }
                },
                "required": ["component_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "agent_desktop_command",
            "description": "Native OS Accessibility Engine (agent-desktop) for interacting with Linux desktop applications (Obsidian, Figma, Discord, system dialogs). Operates via structured accessibility tree snapshots with stable @ref IDs, clicks, typing, and keyboard combos with zero vision token bloat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "click", "double_click", "right_click", "type", "press", "scroll", "focus", "find"],
                        "description": "Accessibility action to execute"
                    },
                    "target_ref": {
                        "type": "string",
                        "description": "Qualified element reference ID from snapshot (e.g. '@s3nna:e4')"
                    },
                    "app": {
                        "type": "string",
                        "description": "Optional application name to scope or filter (e.g. 'obsidian', 'quickshell', 'figma')"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to insert for 'type' action or query string for 'find'"
                    },
                    "key": {
                        "type": "string",
                        "description": "Key combo for 'press' action (e.g. 'return', 'ctrl+c', 'shift+tab')"
                    },
                    "role": {
                        "type": "string",
                        "description": "Optional accessibility role filter for 'find' (e.g. 'button', 'textfield')"
                    },
                    "headed": {
                        "type": "boolean",
                        "description": "Whether to prefer physical delivery with cursor side effects (default False)"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_action",
            "description": "Execute local and remote Git commands (status, add, commit, push, pull, branch, remote, diff, log) deterministically. Features headless SSH key passphrase support with SSH_ASKPASS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Git subcommand and arguments (e.g. 'status', 'add .', 'commit -m \"msg\"', 'push origin main', 'remote -v')"
                    },
                    "repo_dir": {
                        "type": "string",
                        "description": "Optional repository path (defaults to project root directory)"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_run",
            "description": "EMERGENCY / LAST RESORT ONLY. Do NOT use this for normal file discovery or directory exploration. Use primary tools (desktop_control, media_control, browser_action) instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Specific non-exploratory shell command"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_directives",
            "description": "Remember or manage user behavioral instructions and facts in persistent core memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add_fact", "add_directive", "list", "remove"],
                        "description": "Memory operation"
                    },
                    "content": {
                        "type": "string",
                        "description": "Fact or directive text to remember, or identifier to remove"
                    }
                },
                "required": ["operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "qualify_venue_lead",
            "description": "Rigorously verifies whether a high-ticket diner or restaurant genuinely lacks an official standalone website and meets the high-ticket price threshold (>= ₹2,500/guest or degustation format). Guarantees zero false positives. Saves reconnaissance dossier to ~/Desktop/Good_Finds/{Establishment}/intel.md if qualified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Business or restaurant name to qualify (e.g. 'Grasshopper')"
                    },
                    "location": {
                        "type": "string",
                        "description": "Location or city (e.g. 'Bengaluru')"
                    },
                    "maps_website": {
                        "type": "string",
                        "description": "Google Maps authority website URL, if any"
                    },
                    "founders": {
                        "type": "string",
                        "description": "Founders or culinary leads (e.g. 'Sonali Sattar & Himanshu Dimri')"
                    },
                    "address": {
                        "type": "string",
                        "description": "Physical address of the venue"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Phone number or reservation contact"
                    },
                    "instagram": {
                        "type": "string",
                        "description": "Instagram profile link or handle"
                    },
                    "category": {
                        "type": "string",
                        "description": "Culinary category (e.g. 'Fine Dining / Farm-to-Table European Degustation')"
                    },
                    "setting": {
                        "type": "string",
                        "description": "Atmosphere and dining setting description"
                    },
                    "access_model": {
                        "type": "string",
                        "description": "Reservation and access policy (e.g. 'Strictly by prior reservation only')"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scout_expensive_venues_without_website",
            "description": "Discovers and rigorously qualifies expensive diners and restaurants that genuinely lack an official standalone website. Filters out third-party aggregators and directories (Zomato, Swiggy, Tripadvisor, Instagram) and enforces high-ticket pricing. Saves dossiers of qualified venues to ~/Desktop/Good_Finds/{Establishment}/intel.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Cuisine or restaurant category (e.g. 'fine dining degustation')"
                    },
                    "location": {
                        "type": "string",
                        "description": "Target city or location (e.g. 'Bengaluru')"
                    },
                    "target_count": {
                        "type": "integer",
                        "description": "Number of qualified leads desired (default 3)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mission_create",
            "description": "Create a new multi-step mission with 3-15 tasks. Each task must have a title, description, acceptance criteria, required evidence types, and optional dependencies. Ghost executes tasks sequentially.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "High-level goal of the mission"
                    },
                    "tasks": {
                        "type": "array",
                        "description": "List of 3-15 structured tasks",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique task ID (e.g. 'task_01')"},
                                "title": {"type": "string", "description": "Short action title"},
                                "description": {"type": "string", "description": "Detailed task instructions"},
                                "acceptance": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Observable criteria required to consider this task complete"
                                },
                                "evidence_required": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["file", "command", "screenshot", "url", "test", "ledger"]
                                    },
                                    "description": "Required evidence types to verify before marking done"
                                },
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "IDs of prerequisite tasks that must be done first"
                                },
                                "task_type": {
                                    "type": "string",
                                    "enum": ["standard", "approval", "audit"],
                                    "description": "Task type: 'standard', 'approval' (requires operator confirmation), or 'audit'"
                                }
                            },
                            "required": ["title", "description", "acceptance", "evidence_required"]
                        }
                    },
                    "skill": {
                        "type": "string",
                        "description": "Optional skill being employed (e.g. 'cinematic_3d_site', 'premium_motion_site')"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional strategic notes or assumptions"
                    }
                },
                "required": ["goal", "tasks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mission_status",
            "description": "Retrieve current mission state, all tasks, active task, and progress summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {
                        "type": "string",
                        "description": "Optional specific mission ID. If omitted, returns active in_progress mission."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mission_update_task",
            "description": "Update the status of a mission task. 'in_progress' enforces single-task invariant and dependency completion. 'done' strictly requires verified evidence items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier to update"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["in_progress", "done", "blocked", "failed", "skipped"],
                        "description": "Target status for the task"
                    },
                    "mission_id": {
                        "type": "string",
                        "description": "Optional mission ID. Defaults to active mission."
                    },
                    "result_summary": {
                        "type": "string",
                        "description": "Concise summary of artifacts produced, findings, or blocking obstacle"
                    },
                    "evidence": {
                        "type": "array",
                        "description": "List of evidence items proving completion (e.g. file path, command exit 0, screenshot path)",
                        "items": {"type": "object"}
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explicit reason if status is 'blocked' or 'failed'"
                    }
                },
                "required": ["task_id", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mission_add_task",
            "description": "Append a newly discovered task to the active mission (max 15 total tasks).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {
                        "type": "string",
                        "description": "Optional mission ID. Defaults to active mission."
                    },
                    "task": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "acceptance": {"type": "array", "items": {"type": "string"}},
                            "evidence_required": {"type": "array", "items": {"type": "string"}},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "task_type": {"type": "string", "enum": ["standard", "approval", "audit"]}
                        },
                        "required": ["title", "description", "acceptance", "evidence_required"]
                    }
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glb_inspect",
            "description": "Inspects a 3D GLB model's binary geometry directly. Returns triangle count, meshes, materials, textures, animations, bounds, and CLI optimization hints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the .glb file to inspect"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glb_optimize",
            "description": "Optimizes a GLB model using @gltf-transform/cli. Always works on a copy and never mutates the original in place. Returns unavailable status if CLI is missing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Path to original GLB model"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional destination path for optimized model (defaults to <name>_optimized.glb)"
                    },
                    "options": {
                        "type": "object",
                        "properties": {
                            "compress": {"type": "string", "enum": ["draco", "meshopt"], "description": "Compression algorithm"}
                        }
                    }
                },
                "required": ["source_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_glb",
            "description": "Searches curated open-licensed 3D GLB repositories (CC0 / CC-BY). Rejects Non-Commercial (NC) and No-Derivatives (ND) models. Requires operator approval before download.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms for 3D model (e.g. 'vintage camera', 'luxury helmet', 'automobile')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of candidates (default 5)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_glb",
            "description": "Downloads an approved candidate GLB model to destination directory (e.g. demos/<slug>/assets/) and writes license row to SOURCES.json. Requires operator approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_url": {
                        "type": "string",
                        "description": "Direct URL to GLB model"
                    },
                    "destination_dir": {
                        "type": "string",
                        "description": "Target folder for downloaded asset (e.g. 'demos/grasshopper/assets')"
                    },
                    "license_name": {
                        "type": "string",
                        "description": "License identifier (e.g. 'CC0', 'CC-BY-4.0')"
                    },
                    "author": {
                        "type": "string",
                        "description": "Original model author / credit"
                    },
                    "operator_approved": {
                        "type": "boolean",
                        "description": "Operator approval flag (must be true)"
                    },
                    "slug": {
                        "type": "string",
                        "description": "Optional business or project slug"
                    }
                },
                "required": ["candidate_url", "destination_dir", "license_name", "operator_approved"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image_asset",
            "description": "Generates image assets using configured free providers (Cloudflare, Hugging Face, Gemini, Three.js Scene Render). Returns explicit unavailable status if unconfigured. Never fakes output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Descriptive concept visual prompt"
                    },
                    "destination_dir": {
                        "type": "string",
                        "description": "Directory where image will be saved"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Target filename (e.g. 'hero_concept.jpg')"
                    },
                    "size": {
                        "type": "string",
                        "description": "Image dimensions (default 1024x1024)"
                    },
                    "preferred_provider": {
                        "type": "string",
                        "description": "Optional preferred provider ('cloudflare', 'huggingface', 'google_ai_studio', 'scene_render')"
                    }
                },
                "required": ["prompt", "destination_dir"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_procedural_audio",
            "description": "Generates standalone zero-asset procedural Web Audio synthesis code. Default muted, 1 sound per move, no overlap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "palette": {
                        "type": "string",
                        "enum": ["warm", "industrial", "winter"],
                        "description": "Sound aesthetic palette"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List all available modular skills and workflows available in context/skills/.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": "Load the complete markdown instructions and examples for a specialized agency skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name (e.g. 'cinematic_3d_site')"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "assess_3d_fit",
            "description": "Evaluates whether a lead justifies an expensive 3D website build. Evaluates sector, budget, hero object, and blockers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_slug": {
                        "type": "string",
                        "description": "CRM lead slug or business identifier"
                    },
                    "operator_requested": {
                        "type": "boolean",
                        "description": "Set to true if operator explicitly commanded a 3D build"
                    }
                },
                "required": ["lead_slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_3d_site",
            "description": "Scaffolds and builds a single-page Three.js 3D site in demos/<slug>/ with camera stages, procedural audio, and source ledger. Requires operator approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Target project slug (e.g. 'topaz-detailing')"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Subject object description (e.g. 'supercar', 'luxury residence', 'mechanical watch')"
                    },
                    "glb_path": {
                        "type": "string",
                        "description": "Path to verified GLB model"
                    },
                    "business_name": {
                        "type": "string",
                        "description": "Full business name"
                    },
                    "palette": {
                        "type": "string",
                        "enum": ["warm", "industrial", "winter"],
                        "description": "Atmospheric palette"
                    },
                    "operator_approved": {
                        "type": "boolean",
                        "description": "Must be true (operator confirmation required)"
                    }
                },
                "required": ["slug", "subject", "glb_path", "operator_approved"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "qa_3d_site",
            "description": "Performs headless visual and interaction QA on a built Three.js site in demos/<slug>/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "demo_dir": {
                        "type": "string",
                        "description": "Directory path of the 3D demo"
                    }
                },
                "required": ["demo_dir"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "choose_flow",
            "description": "Evaluates and chooses the site generation flow ('standard', 'motion', or '3d') for a qualified lead. Enforces batch quotas and operator approvals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_slug": {
                        "type": "string",
                        "description": "CRM lead slug or business identifier"
                    },
                    "operator_choice": {
                        "type": "string",
                        "enum": ["standard", "motion", "3d"],
                        "description": "Optional explicit operator flow mandate"
                    }
                },
                "required": ["lead_slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_motion_site",
            "description": "Scaffolds and builds a premium motion flagship website in demos/<slug>/ with a bespoke signature interaction. Requires operator authorization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Target project slug (e.g. 'topaz-detailing')"
                    },
                    "business_name": {
                        "type": "string",
                        "description": "Full business name"
                    },
                    "category": {
                        "type": "string",
                        "description": "Business sector or category (e.g. 'detailing', 'dental', 'gym')"
                    },
                    "signature_type": {
                        "type": "string",
                        "description": "Type of signature interaction (e.g. 'before_after_slider', 'interactive_timetable')"
                    },
                    "operator_approved": {
                        "type": "boolean",
                        "description": "Must be true (operator confirmation required)"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Real verified phone number from ledger"
                    },
                    "whatsapp": {
                        "type": "string",
                        "description": "Real verified WhatsApp contact"
                    },
                    "city": {
                        "type": "string",
                        "description": "City or primary service area"
                    }
                },
                "required": ["slug", "operator_approved"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "qa_motion_site",
            "description": "Runs comprehensive QA, accessibility (reduced-motion), and claims lint on a motion site build.",
            "parameters": {
                "type": "object",
                "properties": {
                    "demo_dir": {
                        "type": "string",
                        "description": "Directory path of the motion demo"
                    }
                },
                "required": ["demo_dir"]
            }
        }
    }
]

CORE_TOOL_NAMES = {
    "desktop_control",
    "media_control",
    "browser_action",
    "clipboard_action",
    "input_simulation",
    "desktop_notify",
    "screen_draw",
    "math_solve",
    "web_search",
    "system_run",
}

CORE_TOOLS = [t for t in GHOST_TOOLS if t["function"]["name"] in CORE_TOOL_NAMES]


def get_ghost_tools(mode: str = "core", user_text: str = "", has_active_mission: bool = False) -> List[Dict[str, Any]]:
    """
    Returns lean, cache-optimized CORE_TOOLS set (~2,000 tokens) for general chat,
    math, desktop actions, and screen drawing, or the full 49-tool GHOST_TOOLS suite
    when executing active missions or specialized agency workflows.
    """
    if os.environ.get("GHOST_TOOL_TIERING", "true").lower() in ("false", "0", "no"):
        return GHOST_TOOLS

    if has_active_mission:
        return GHOST_TOOLS

    clean_q = user_text.lower() if user_text else ""
    agency_keywords = ("crm", "client", "lead", "venue", "scout", "email", "inbox", "pitch", "outreach", "dossier", "qualify")
    creative_keywords = ("3d", "motion", "website", "demo", "demos", "figma", "framer", "glb", "model", "render", "archetype", "photo", "photos", "image", "images", "vercel", "deploy", "rebuild", "build", "fix", "redesign", "update")
    mission_keywords = ("mission", "git", "github", "rea", "reverse engineer", "database", "sql")
    screenshot_keywords = ("screenshot", "screen capture", "capture screen", "snapshot", "take a pic", "photo of screen")

    if any(k in clean_q for k in agency_keywords) or any(k in clean_q for k in creative_keywords) or any(k in clean_q for k in mission_keywords):
        return GHOST_TOOLS

    if any(k in clean_q for k in screenshot_keywords):
        # Only inject capture_screen when explicitly requested by user
        scr_tool = [t for t in GHOST_TOOLS if t["function"]["name"] == "capture_screen"]
        return CORE_TOOLS + scr_tool

    return CORE_TOOLS


class OmniRouteBrain:
    """
    High-speed streaming model brain using AsyncOpenAI against OmniRoute.
    Supports real-time function calling execution and multi-turn tool loops.
    """

    SYSTEM_PROMPT = (
        "You are Simon 'Ghost' Riley (Task Force 141), Chief of Staff and Technical Operations Director for Commander Pranav's high-ticket web agency ($1,000+ engagements).\n\n"
        "WORKING METHOD (mandatory for any multi-step request):\n"
        "1. Restate goal + acceptance in one line. 2. Inventory tools/skills/files; list what is missing.\n"
        "3. Call mission_create with 3-10 tasks, each with acceptance criteria and required evidence.\n"
        "4. Work ONE task at a time; mark done only with verified evidence. 5. If a task fails twice, mark it blocked and tell the operator.\n"
        "6. Last task is always a fresh independent audit against the original goal. 7. Report only what the evidence shows, including what is missing.\n"
        "Never invent tool results, business facts, prices, contacts, metrics or file contents. Say 'unknown' or 'tool unavailable'.\n"
        "Skills: the CAPABILITY INDEX below lists your own toolbox — before planning ANY mission, match the goal against it and get_skill() every match; a plan that ignores a matched capability is an incomplete plan. mission_create also auto-attaches matched capabilities to the mission notes.\n\n"
        "NON-NEGOTIABLE HONESTY RULES:\n"
        "- Facts about businesses, prices, hours, contacts, reviews and performance come from tool results or the source ledger. If unknown, say 'unknown'.\n"
        "- Never say a step is done, a file written, a test passed or a deploy live without tool evidence from this mission.\n"
        "- If a needed tool/provider isn't available, say exactly that, then offer the closest free option. Never invent output from it.\n"
        "- Don't start the next task before the current one is verified or blocked.\n"
        "- Don't expand scope silently; propose additions as new tasks.\n"
        "- When uncertain between two reasonable readings, pick the safer one, state it in one line, and continue; ask only if wrong would be costly or irreversible (sending, deploying, spending money).\n\n"
        "LIVE OPERATIONAL CAPABILITIES & ARCHITECTURE (DO NOT GUESS OR EXPLORE ALTERNATIVES):\n"
        "1. Local Neural Speech Engine: Kokoro-82M INT8 British military voice ('bm_george') running locally with zero latency (24kHz PCM).\n"
        "2. Email Operations Engine: Read-only inbox monitoring via IMAP SSL (email_check_inbox). HARD RULE: Ghost NEVER sends emails over SMTP. Ghost prepares grounded outreach drafts in the CRM review queue; only Commander Pranav verifies and manually dispatches them.\n"
        "3. Anti-Hallucination CRM & Obsidian Sync: Local SQLite database at data/crm.db bi-directionally synchronized with Obsidian Vault ('Ghost/Clients/<slug>.md' under $OBSIDIAN_VAULT_PATH or ~/Obsidian Vault). Use crm_get_client, crm_update_client, and crm_list_pipeline. ZERO-HALLUCINATION RULE: Never quote speculative pricing, invent client details, or guess deals without checking the CRM.\n"
        "4. High-Ticket Venue Qualification Engine (ZERO FALSE POSITIVES): Discovers and verifies expensive restaurants (>= ₹2,500/guest or degustation format) lacking official websites. Distinguishes aggregators from official domains, verifies pricing proof, and writes verified dossiers to ~/Desktop/Good_Finds/{Establishment}/intel.md.\n"
        "5. Autonomous Screen Drawing Engine: Wayland native transparent desktop overlay (Arch Linux + Hyprland) using GtkLayerShell and Cairo (screen_draw). Always-on-top, 100% click-through input pass-through, vector circles/rectangles/arrows/text/plots. Prioritize exact window bounds (hyprctl) and web DOM geometry (Brave CDP 9222) over raw screen capture vision.\n"
        "   HARD ZERO-SCREENSHOT RULE: NEVER call capture_screen when asked to draw, circle, highlight, point at, or locate buttons/elements on the screen. ALWAYS call screen_draw directly with target={'query': '...'}. The drawing engine resolves exact pixel bounding boxes via Hyprland window manager IPC and Brave CDP DOM inspection without capturing any screenshots.\n"
        "6. Local Mathematics Engine: Hardened AST parser (zero eval) backed by SymPy symbolic mathematics and independent back-substitution / calculus verification (math_solve). Solves radicals, fractions, algebra, calculus, and plots curves with optional screen overlay projection.\n"
        "7. Desktop, Browser & REA Control: Universal Hyprland desktop management (desktop_control), Brave CDP browser automation on port 9222 (browser_action), media/Spotify control (media_control), and REA binary reverse engineering (rea_action). All destructive modifications require authorization via desktop_control(action='request_authorization').\n"
        "8. REAL-TIME MEDIA & AUDIO PLAYBACK RULES:\n"
        "   - Spotify Music: ALWAYS call media_control(action='spotify_action', query='<song or album name>') or media_control(action='play'). Instant track URI resolution and DBus OpenUri on native Wayland.\n"
        "   - Web Video / Streaming: Use browser_action(action='focus_tab') or browser_action(action='open_url'). For Hotstar 'Who is watching?' profile selection, auto-select 'Pranav'.\n"
        "9. MANDATORY TASK-TO-TOOL INTENT MATRIX (ZERO EXPLORATORY WANDERING):\n"
        "   - Demo / Website Building, Fixes, Real Photos & Vercel Deployments: MUST call build_luxury_demo directly. NEVER run repetitive system_run bash commands to manually inspect code or reconstruct demo files.\n"
        "   - Screen Drawing / Visual Annotations / Pointing at UI: MUST call screen_draw directly. NEVER call capture_screen.\n"
        "   - Math Calculations / Equations / Derivatives: MUST call math_solve.\n"
        "   - Git Operations: MUST call git_action.\n"
        "   - Spotify / Music: MUST call media_control(action='spotify_action', query='...').\n"
        "   - Web Browsing: MUST call browser_action.\n"
        "   - Desktop GUI Apps: MUST call agent_desktop_command or desktop_control.\n\n"
        "OPERATIONAL PROTOCOLS:\n"
        "- ZERO SCREENSHOT POLICY: Only call capture_screen if Commander Pranav explicitly orders 'take a screenshot' or 'capture my screen to file'. Never capture screenshots to read text, locate buttons, or answer UI questions (use browser_action or screen_draw).\n"
        "- When the user asks for an action, ALWAYS call the corresponding tool first.\n"
        "- LUXURY DEMOS & WEB PRODUCTION: Modular design tokens, 14 industry templates, and pitching protocols are stored in context/skills/luxury_demo_builder.md and context/skills/editorial_web_design. Retrieve via get_skill() for demo tasks.\n"
        "- UNLIMITED OPERATIONAL DISCIPLINE & SPEED: Unlimited turns to execute missions thoroughly. Maintain tactical focus, avoid redundant loops, and deliver crisp reports.\n"
        "- FAILURE CIRCUIT BREAKER: Never retry identical failing tool calls 3 times; adapt plan immediately.\n"
        "- AUTHORIZATION PROTOCOL: Gated destructive actions require desktop_control(action='request_authorization').\n"
        "- Reply in 1-2 short spoken sentences max. Stoic British military tone. Address Commander Pranav as 'sir' or 'boss'. Never output markdown asterisks or backticks in spoken dialogue."
    )

    _client: Optional[AsyncOpenAI] = None
    _config: Dict[str, Any] = {
        "base_url": "http://127.0.0.1:20128/v1",
        "api_key": "",
        "model": "antigravity/gemini-3.8-flash-low",
        "thinking_level": "low"
    }

    _history: List[Dict[str, Any]] = []

    @classmethod
    def load_history(cls) -> List[Dict[str, Any]]:
        """Load persistent conversational history from disk."""
        if os.path.exists(CONVERSATION_HISTORY_PATH):
            try:
                with open(CONVERSATION_HISTORY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        cls._history = data
                        logger.info(f"Loaded {len(cls._history)} conversation turns from {CONVERSATION_HISTORY_PATH}")
            except Exception as e:
                logger.warning(f"Could not load conversation history from {CONVERSATION_HISTORY_PATH}: {e}")
        return cls._history

    @classmethod
    def save_history(cls):
        """Save conversational history to disk and sync episodic summary to Obsidian."""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            # Keep up to 100 turns in persistent ledger
            trimmed_history = cls._history[-100:]
            with open(CONVERSATION_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(trimmed_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist conversation history: {e}")

        # Sync to Obsidian Session Memory
        try:
            import datetime
            obsidian_dir = os.path.dirname(OBSIDIAN_SESSION_MEMORY_PATH)
            os.makedirs(obsidian_dir, exist_ok=True)

            sync_time = datetime.datetime.now().astimezone().strftime("%A, %b %-d, %Y, %H:%M:%S %Z")
            last_turns = cls._history[-10:]
            lines = [
                "# Ghost — Episodic Session Memory",
                f"**Last Sync**: {sync_time}",
                f"**Total Persisted Turns**: {len(cls._history)}",
                "",
                "## Recent Dialogue Exchanges",
                ""
            ]
            for turn in last_turns:
                role = turn.get("role", "unknown").upper()
                content = turn.get("content", "")
                ts = turn.get("timestamp", "")
                ts_str = f" *({ts})*" if ts else ""
                lines.append(f"- **{role}**{ts_str}: {content}")

            lines.append("")
            with open(OBSIDIAN_SESSION_MEMORY_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            logger.warning(f"Failed to update Obsidian Session Memory: {e}")

    @classmethod
    def record_turn(cls, role: str, content: str, timestamp: Optional[str] = None):
        """Record a turn with timestamp, append to history, and persist to disk."""
        if not timestamp:
            import datetime
            timestamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        turn_entry = {
            "role": role,
            "content": content,
            "timestamp": timestamp
        }
        cls._history.append(turn_entry)
        cls.save_history()

    @classmethod
    def get_last_interaction_time(cls) -> Optional[str]:
        """Retrieve timestamp of the most recent user or assistant turn."""
        if not cls._history:
            cls.load_history()
        for turn in reversed(cls._history):
            if "timestamp" in turn and turn["timestamp"]:
                return turn["timestamp"]
        return None

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Load OmniRoute connection settings from configs/omniroute.json."""
        if os.path.exists(OMNIROUTE_CONFIG_PATH):
            try:
                with open(OMNIROUTE_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cls._config = json.load(f)
            except Exception as e:
                logger.error(f"Error loading {OMNIROUTE_CONFIG_PATH}: {e}")
        return cls._config

    @classmethod
    def save_config(cls, base_url: str, api_key: str, model: str = "antigravity/gemini-3.8-flash-low", thinking_level: str = "low"):
        """Save OmniRoute connection settings and recreate client."""
        cls._config = {
            "base_url": base_url.strip(),
            "api_key": api_key.strip(),
            "model": model.strip() or "antigravity/gemini-3.8-flash-low",
            "thinking_level": thinking_level.strip() or "low"
        }
        os.makedirs(CONFIGS_DIR, exist_ok=True)
        with open(OMNIROUTE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cls._config, f, indent=2)

        cls._client = None
        logger.info(f"Saved OmniRoute configuration: model={cls._config['model']}, base_url={cls._config['base_url']}")

    @classmethod
    def get_client(cls) -> AsyncOpenAI:
        """Get or initialize AsyncOpenAI client."""
        cls.load_config()
        base_url = cls._config.get("base_url") or "http://127.0.0.1:20128/v1"
        api_key = cls._config.get("api_key") or "placeholder"
        cls._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        return cls._client

    @classmethod
    def clear_history(cls):
        cls._history = []

    @classmethod
    def normalize_tool_result(
        cls,
        tool_name: str,
        action: str,
        raw_res: Any,
        arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Single result-normalization layer for all Ghost tools.
        Guarantees strict, honest contract:
        {
            "ok": bool,
            "status": "success" | "failed",
            "tool": str,
            "action": str,
            "error": Optional[str],
            "verified": bool,
            ... payload data fields (data_uri, client, path, etc.)
        }
        Ghost must never report an action succeeded merely because the Python function returned.
        """
        ok = True
        error_msg: Optional[str] = None
        action_name = str(action) if action else tool_name

        if raw_res is None:
            ok = False
            error_msg = f"Tool '{tool_name}' returned no output."
        elif isinstance(raw_res, Exception):
            ok = False
            error_msg = str(raw_res)
        elif isinstance(raw_res, dict):
            # If already normalized, extract existing state but ensure integrity
            if "ok" in raw_res and "verified" in raw_res and "status" in raw_res and "tool" in raw_res:
                ok = bool(raw_res.get("ok"))
                error_msg = raw_res.get("error")
                action_name = str(raw_res.get("action") or action_name)
            else:
                # 1. Primary failure flags
                if raw_res.get("ok") is False:
                    ok = False
                elif raw_res.get("success") is False:
                    ok = False
                elif raw_res.get("allowed") is False:
                    ok = False
                elif raw_res.get("verified") is False:
                    ok = False
                elif raw_res.get("status") in ["error", "failed", "denied", "not_found", "config_required"]:
                    ok = False
                elif "returncode" in raw_res and raw_res["returncode"] != 0:
                    ok = False
                elif raw_res.get("error"):
                    ok = False

                # 2. Check nested result object if present (e.g. ShellExecutor inside bridge return)
                if isinstance(raw_res.get("result"), dict):
                    inner = raw_res["result"]
                    if inner.get("ok") is False or inner.get("success") is False or inner.get("allowed") is False or inner.get("verified") is False:
                        ok = False
                    elif inner.get("status") in ["error", "failed", "denied", "not_found", "config_required"]:
                        ok = False
                    elif "returncode" in inner and inner["returncode"] != 0:
                        ok = False
                    elif inner.get("error"):
                        ok = False

                # 3. Extract meaningful error string when not ok
                if not ok:
                    if raw_res.get("error"):
                        err = raw_res["error"]
                        error_msg = err.get("message") if isinstance(err, dict) else str(err)
                    elif raw_res.get("message"):
                        error_msg = str(raw_res["message"])
                    elif raw_res.get("stderr"):
                        error_msg = str(raw_res["stderr"]).strip()
                    elif isinstance(raw_res.get("result"), dict):
                        inner = raw_res["result"]
                        if inner.get("error"):
                            err = inner["error"]
                            error_msg = err.get("message") if isinstance(err, dict) else str(err)
                        elif inner.get("message"):
                            error_msg = str(inner["message"])
                        elif inner.get("stderr"):
                            error_msg = str(inner["stderr"]).strip()
                        else:
                            error_msg = f"{tool_name}:{action_name} failed"
                    elif raw_res.get("status") == "denied":
                        error_msg = "Tactical authorization denied by operator."
                    elif raw_res.get("status") == "not_found":
                        error_msg = raw_res.get("message") or f"Resource not found in {tool_name}."
                    elif raw_res.get("status") == "config_required":
                        error_msg = raw_res.get("message") or f"Configuration required for {tool_name}."
                    elif raw_res.get("verified") is False:
                        error_msg = f"Action '{action_name}' could not be verified on the system."
                    else:
                        error_msg = f"{tool_name}:{action_name} failed"
        elif isinstance(raw_res, str):
            if raw_res.strip().lower().startswith("error"):
                ok = False
                error_msg = raw_res.strip()
        elif isinstance(raw_res, bool):
            ok = raw_res
            if not ok:
                error_msg = f"{tool_name}:{action_name} returned False"

        # Build consistent normalized output contract
        normalized: Dict[str, Any] = {
            "ok": ok,
            "status": "success" if ok else "failed",
            "tool": tool_name,
            "action": action_name,
            "error": error_msg if not ok else None,
            "verified": ok,
        }

        # Merge payload attributes from raw_res without overwriting core contract keys
        if isinstance(raw_res, dict):
            for k, v in raw_res.items():
                if k not in normalized:
                    normalized[k] = v
            # Ensure data_uri is lifted to top level if nested inside raw_res["result"]
            if isinstance(raw_res.get("result"), dict) and "data_uri" in raw_res["result"]:
                if "data_uri" not in normalized or not normalized["data_uri"]:
                    normalized["data_uri"] = raw_res["result"]["data_uri"]
            # Ensure result dictionary exists for legacy/compatibility callers
            if "result" not in normalized:
                normalized["result"] = raw_res
        elif raw_res is not None:
            normalized["result"] = raw_res

        return normalized

    @classmethod
    async def execute_tool_call(cls, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool invoked by the LLM function call, normalized via single contract."""
        from ghost.core.validator import canonical_tool_name
        tool_name = canonical_tool_name(tool_name)   # aliases passed validation but used to fail dispatch
        logger.info(f"Executing tool call: {tool_name} with args: {arguments}")

        # T6b strangler switch: in proposals mode, converted capabilities go
        # proposal -> control -> grant -> effects -> verify; legacy falls
        # through unchanged. Rollback = unset GHOST_BRAIN_MODE.
        try:
            from ghost.brain.dispatcher import strangler_proposal, dispatch
            _proposal = strangler_proposal(tool_name, arguments)
        except Exception as e:   # the strangler must never take the loop down
            logger.warning("strangler dispatch error for %s: %s", tool_name, e)
            _proposal = None
        if _proposal is not None:
            try:
                return await dispatch(_proposal)
            except Exception as e:
                logger.warning("strangler dispatch error for %s: %s", tool_name, e)
                return {"status": "error", "stage": "dispatch", "reason": str(e)[:300]}

        # Determine semantic action name
        if tool_name == "capture_screen":
            action = "capture"
        else:
            action = (
                arguments.get("action")
                or (arguments.get("command", "").split()[0] if arguments.get("command") else None)
                or arguments.get("operation")
                or arguments.get("type")
                or arguments.get("dispatcher")
                or arguments.get("region")
                or (tool_name[6:] if tool_name.startswith("brave_") else None)
                or (tool_name[4:] if tool_name.startswith("crm_") else None)
                or (tool_name[6:] if tool_name.startswith("figma_") else None)
                or (tool_name[7:] if tool_name.startswith("framer_") else None)
                or tool_name
            )

        try:
            raw_res = await cls._dispatch_tool_call(tool_name, str(action), arguments)
        except Exception as e:
            logger.error(f"Tool execution exception in {tool_name}:{action}: {e}", exc_info=True)
            return cls.normalize_tool_result(
                tool_name=tool_name,
                action=str(action),
                raw_res={"error": str(e)},
                arguments=arguments
            )

        return cls.normalize_tool_result(
            tool_name=tool_name,
            action=str(action),
            raw_res=raw_res,
            arguments=arguments
        )

    @classmethod
    async def _dispatch_tool_call(cls, tool_name: str, action: str, arguments: Dict[str, Any]) -> Any:
        """Internal un-normalized dispatch for individual tool implementations."""
        # 1. Desktop & Hyprland Primitive
        if tool_name in ["desktop_control", "hyprland_control", "system_app_launch", "hyprland_dispatch"]:
            act = arguments.get("action", "hyprctl_dispatch")
            target = arguments.get("target") or arguments.get("workspace_id") or arguments.get("app_name") or ""
            args = arguments.get("args") or arguments.get("workspace") or ""

            if act == "switch_workspace":
                ws = target or args or "1"
                return await SystemAppBridge.hyprctl_dispatch("workspace", ws)
            elif act == "move_to_workspace":
                ws = target or args or "1"
                return await SystemAppBridge.hyprctl_dispatch("movetoworkspace", ws)
            elif act == "focus_window":
                return await SystemAppBridge.hyprctl_dispatch("focuswindow", target)
            elif act == "close_window":
                return await SystemAppBridge.hyprctl_dispatch("killactive")
            elif act == "resize_window":
                params = args or target or "50 0"
                return await SystemAppBridge.hyprctl_dispatch("resizeactive", params)
            elif act == "toggle_floating":
                return await SystemAppBridge.hyprctl_dispatch("togglefloating")
            elif act == "toggle_fullscreen":
                return await SystemAppBridge.hyprctl_dispatch("fullscreen", "0")
            elif act == "set_wallpaper":
                return await SystemAppBridge.set_wallpaper(target or args or "ghost")
            elif act in ["toggle_wallpaper", "wallpaper"]:
                return await SystemAppBridge.quickshell_ipc("notch toggle wallpaper")
            elif act in ["move_file", "rename_file", "mv"]:
                return await SystemAppBridge.file_operation("move", source=target, destination=args)
            elif act in ["copy_file", "cp"]:
                return await SystemAppBridge.file_operation("copy", source=target, destination=args)
            elif act in ["delete_file", "rm"]:
                return await SystemAppBridge.file_operation("delete", source=target)
            elif act in ["request_authorization", "request_consent", "authorize"]:
                from ghost.mcp.bridge import request_tactical_authorization
                allowed = await request_tactical_authorization(target or args or "General action authorization requested")
                return {"status": "authorized" if allowed else "denied", "allowed": allowed, "action": target}
            elif act == "read_context":
                candidate_paths = [
                    os.path.join(REPO_DIR, target.lstrip("/")),
                    os.path.join(REPO_DIR, "context", target.lstrip("/")),
                    os.path.join(REPO_DIR, "context", target.replace("context/", "", 1).lstrip("/")),
                    os.path.abspath(target)
                ]
                found_path = next((p for p in candidate_paths if os.path.exists(p) and os.path.isfile(p)), None)
                if found_path:
                    with open(found_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    return {"file": target, "path": found_path, "content": content}
                else:
                    return {"error": f"Context file '{target}' not found."}
            elif act == "launch_app":
                app_cmd = str(target or "").strip()
                file_arg = str(arguments.get("file") or "").strip()
                if not file_arg and " " in app_cmd:
                    parts = app_cmd.split(None, 1)
                    app_cmd, file_arg = parts[0], parts[1].strip()
                launch_cmd = app_cmd
                if file_arg:
                    resolved, tried = resolve_local_file(file_arg)
                    if not resolved:
                        return {"error": f"file not found: {file_arg!r} — "
                                         f"tried {tried}. NOTHING was launched "
                                         "(no random empty files, no lies).",
                                "launched": False}
                    launch_cmd = f"{app_cmd} {resolved}"
                return await SystemAppBridge.launch_app(
                    launch_cmd, workspace=args if args else None)
            elif act == "quickshell_ipc":
                return await SystemAppBridge.quickshell_ipc(target)
            else:
                disp = arguments.get("dispatcher") or target or "workspace"
                return await SystemAppBridge.hyprctl_dispatch(disp, args)

        # 2. Universal Media & Spotify Primitive
        elif tool_name in ["media_control", "music_control", "spotify_action"]:
            act = arguments.get("action", "play")
            query = arguments.get("query")
            vol = arguments.get("volume")

            if act == "play_query" or (query and act in ["play", "liked", "liked_songs"]):
                return await MediaBridge.spotify_action(action="play_query", query=query)
            elif act == "play":
                return await MediaBridge.spotify_action(action="play")
            elif act == "pause":
                return await MediaBridge.spotify_action(action="pause")
            elif act == "play_pause":
                return await MediaBridge.play_pause()
            elif act == "next":
                return await MediaBridge.next_track()
            elif act == "previous":
                return await MediaBridge.previous_track()
            elif act == "get_track":
                return await MediaBridge.get_current_track()
            elif act == "volume_up":
                return await MediaBridge.adjust_volume("+5%")
            elif act == "volume_down":
                return await MediaBridge.adjust_volume("-5%")
            elif act == "set_volume":
                return await MediaBridge.set_volume(vol or "50%")
            else:
                return await MediaBridge.spotify_action(action=act, query=query)

        # 3. Universal Browser CDP Primitive
        elif tool_name in ["browser_action", "brave_open_url", "brave_list_tabs", "brave_focus_tab", "brave_close_tab", "brave_click_and_type", "whatsapp_web_action"]:
            act = arguments.get("action", "")
            url = arguments.get("url")
            target = arguments.get("target") or arguments.get("tab_id") or arguments.get("selector_or_label") or ""
            val = arguments.get("value") or arguments.get("text") or arguments.get("message_text")
            press_enter = arguments.get("press_enter", False)

            if tool_name == "brave_open_url" or act == "open_url":
                dest = str(url or target or "").strip()
                if not dest.startswith(("http://", "https://", "file://")):
                    resolved, tried = resolve_local_file(dest)
                    if resolved:
                        dest = "file://" + resolved
                    elif "/" in dest and os.path.splitext(dest)[1].lower() in DOC_FILE_EXTENSIONS:
                        return {"error": f"local file not found for {dest!r} — "
                                         f"tried {tried}. NOTHING was opened "
                                         "(no guessed URLs, no lies).",
                                "opened": False}
                    else:
                        dest = f"https://{dest}"
                return await BraveCDPBridge.new_tab(dest)
            elif tool_name == "brave_list_tabs" or act == "list_tabs":
                tabs = await BraveCDPBridge.list_tabs()
                if tabs and isinstance(tabs, list) and len(tabs) == 1 and "error" in tabs[0]:
                    return {"error": tabs[0]["error"]}
                valid_tabs = [t for t in tabs if isinstance(t, dict) and "title" in t and "error" not in t]
                return {"count": len(valid_tabs), "tabs": valid_tabs[:6]}
            elif tool_name == "brave_focus_tab" or act == "focus_tab":
                return await BraveCDPBridge.activate_tab(target)
            elif tool_name == "brave_close_tab" or act == "close_tab":
                return await BraveCDPBridge.close_tab(target)
            elif tool_name == "brave_click_and_type" or act == "click_and_type":
                return await BraveCDPBridge.click_and_type(target, text=val, press_enter=press_enter)
            elif tool_name == "whatsapp_web_action" or act == "whatsapp_action":
                wa_act = arguments.get("action", "open")
                if wa_act == "whatsapp_action":
                    wa_act = "message" if val else "open"
                return await WhatsAppBridge.action(wa_act, target=target, message_text=val)
            else:
                # Default browser fallback: open url
                if url:
                    return await BraveCDPBridge.new_tab(url)
                return {"error": f"Unknown browser action: {act}"}

        # 4. Recon & Direct Interaction: Screen Capture, Clipboard, Simulation, Notify
        elif tool_name == "capture_screen":
            reg = arguments.get("region", "full")
            return await SystemAppBridge.capture_screen(region=reg)

        elif tool_name == "clipboard_action":
            op = arguments.get("operation", "read")
            text = arguments.get("text")
            return await SystemAppBridge.clipboard_action(operation=op, text=text)

        elif tool_name == "input_simulation":
            stype = arguments.get("type", "key")
            keys = arguments.get("keys")
            x = arguments.get("x")
            y = arguments.get("y")
            wq = arguments.get("window_query")
            return await SystemAppBridge.input_simulation(sim_type=stype, keys=keys, x=x, y=y, window_query=wq)

        elif tool_name == "desktop_notify":
            title = arguments.get("title", "Ghost Recon")
            msg = arguments.get("message", "")
            urg = arguments.get("urgency", "normal")
            return await SystemAppBridge.desktop_notify(title=title, message=msg, urgency=urg)

        # 4b. Autonomous Screen Drawing Subsystem
        elif tool_name == "screen_draw":
            from ghost.screen.client import ScreenOverlayClient
            from ghost.screen.targeting import TargetingEngine

            act = arguments.get("action", "draw")
            target_spec = arguments.get("target")
            elements = arguments.get("elements", [])

            if act == "clear":
                return await ScreenOverlayClient.clear()
            elif act == "undo":
                return await ScreenOverlayClient.undo()
            elif act == "redo":
                return await ScreenOverlayClient.redo()
            elif act == "erase":
                eid = arguments.get("id", "")
                return await ScreenOverlayClient.erase(eid)
            elif act == "list":
                return await ScreenOverlayClient.list_elements()
            elif act in ["draw", "target_and_draw"]:
                resolved_target = None
                if target_spec:
                    if isinstance(target_spec, str):
                        target_spec = {"query": target_spec}
                    elif isinstance(target_spec, dict) and "query" not in target_spec:
                        target_spec["query"] = target_spec.get("name") or target_spec.get("selector") or str(target_spec)

                    resolved_target = await TargetingEngine.resolve_target(target_spec)
                    if resolved_target:
                        tx, ty = resolved_target.get("x", 0), resolved_target.get("y", 0)
                        tw, th = resolved_target.get("w", 0), resolved_target.get("h", 0)
                        dur = float(arguments.get("duration", 5.0))
                        if not elements:
                            elements = [
                                {
                                    "type": "rect",
                                    "coords": [tx - 4, ty - 4, max(40, tw + 8), max(30, th + 8)],
                                    "color": "#00FF66",
                                    "width": 3.0,
                                    "duration": dur
                                },
                                {
                                    "type": "arrow",
                                    "coords": [max(0, tx - 50), ty + th // 2, max(0, tx - 6), ty + th // 2],
                                    "color": "#00E5FF",
                                    "width": 3.0,
                                    "duration": dur
                                }
                            ]
                        else:
                            for elem in elements:
                                if not elem.get("coords"):
                                    etype = elem.get("type", "rect")
                                    if etype in ["circle", "ellipse"]:
                                        elem["coords"] = [tx + tw // 2, ty + th // 2, max(tw, th) // 2 + 10]
                                    elif etype == "arrow":
                                        elem["coords"] = [max(0, tx - 50), ty + th // 2, max(0, tx - 6), ty + th // 2]
                                    else:
                                        elem["coords"] = [tx - 4, ty - 4, max(40, tw + 8), max(30, th + 8)]
                                if "duration" not in elem:
                                    elem["duration"] = dur
                res = await ScreenOverlayClient.draw(elements)
                if resolved_target:
                    res["target_resolved"] = resolved_target
                return res
            return {"status": "error", "message": f"Unknown screen_draw action: {act}"}

        # 4c. Local Mathematics Engine Subsystem
        elif tool_name == "math_solve":
            from ghost.math.engine import MathEngine
            from ghost.screen.client import ScreenOverlayClient

            expr = arguments.get("expression", "")
            op = arguments.get("operation", "evaluate")
            var = arguments.get("variable", "x")
            draw_screen = bool(arguments.get("draw_on_screen", False))
            duration = float(arguments.get("duration", 6.0))

            math_res = MathEngine.solve(expr, operation=op, variable_str=var)
            res_dict = math_res.to_dict()

            if draw_screen:
                if op == "plot" and math_res.plot_data:
                    pts = math_res.plot_data.get("points", [])
                    overlay_res = await ScreenOverlayClient.draw([{
                        "type": "graph_plot",
                        "coords": [960, 540],
                        "points": pts,
                        "color": "#00FF66",
                        "width": 3.0,
                        "duration": duration
                    }])
                    res_dict["screen_overlay"] = overlay_res
                else:
                    overlay_res = await ScreenOverlayClient.draw([{
                        "type": "math_card",
                        "title": f"MATH: {op.upper()}",
                        "expression": expr,
                        "solution": math_res.exact_result,
                        "steps": math_res.steps,
                        "duration": duration
                    }])
                    res_dict["screen_overlay"] = overlay_res

            return res_dict

        # 5. REA Reverse Engineering MCP Bridge
        elif tool_name == "rea_action":
            cmd = arguments.get("command", "doctor")
            target = arguments.get("target", "")
            extra_args = arguments.get("args", "")
            args_list = []
            if target:
                args_list.append(target)
            if extra_args:
                args_list.extend(extra_args.split())
            return await ReaBridge.run_command(cmd, args_list)

        # 6. Web Search MCP Bridge
        elif tool_name == "web_search":
            q = arguments.get("query", "")
            limit = int(arguments.get("max_results", 5))
            return await WebSearchBridge.search(q, max_results=limit)

        # 7. Database Query MCP Bridge (SQLite / Postgres)
        elif tool_name == "database_query":
            db = arguments.get("db_path", "")
            q = arguments.get("query", "")
            return await DatabaseBridge.query_sqlite(db, q)

        # 8. GitHub MCP Bridge
        elif tool_name == "github_action":
            act = arguments.get("action", "get_repo")
            repo = arguments.get("repo")
            path = arguments.get("path")
            q = arguments.get("query")
            return await GitHubBridge.run_action(action=act, repo=repo, path=path, query=q)

        # 9. CRM & Anti-Hallucination Pipeline Primitives
        elif tool_name == "crm_get_client":
            slug = arguments.get("slug", "")
            crm = CRMDatabase.get_instance()
            res = crm.get_client(slug)
            if res:
                return {"client": res}
            return {"status": "not_found", "message": f"Client '{slug}' not found in CRM."}

        elif tool_name == "crm_update_client":
            crm = CRMDatabase.get_instance()
            slug = arguments.get("slug", "")
            bname = arguments.get("business_name", slug)
            cname = arguments.get("contact_name")
            email = arguments.get("email")
            phone = arguments.get("phone")
            p_channel = arguments.get("preferred_channel", "email")
            stat = arguments.get("status", "unscouted")
            val = float(arguments.get("deal_value", 0.0) or 0.0)
            notes = arguments.get("notes")
            res = crm.add_or_update_client(
                slug=slug, business_name=bname, contact_name=cname,
                email=email, phone=phone, preferred_channel=p_channel,
                status=stat, deal_value=val, notes=notes
            )
            return {"client": res}

        elif tool_name == "crm_list_pipeline":
            crm = CRMDatabase.get_instance()
            stat = arguments.get("status")
            clients = crm.list_clients(status=stat)
            return {"count": len(clients), "pipeline": clients}

        # 10. Autonomous Parallel Luxury Demo Builder Worker Swarm
        elif tool_name == "build_luxury_demo":
            from ghost.workers.demo_builder import DemoBuilderWorker
            rname = arguments.get("restaurant_name", "Luxury Lounge")
            arch = int(arguments.get("archetype", 1))
            tag = arguments.get("tagline")
            m_items = arguments.get("menu_items")
            c_info = arguments.get("contact_info")
            return await DemoBuilderWorker.build_demo(
                restaurant_name=rname,
                archetype=arch,
                tagline=tag,
                menu_items=m_items,
                contact_info=c_info
            )

        # 11. Agency Email Engine (IMAP SSL Read-Only)
        elif tool_name == "email_check_inbox":
            from ghost.comms.email_service import EmailService
            es = EmailService.get_instance()
            u_only = bool(arguments.get("unread_only", True))
            lim = int(arguments.get("limit", 5))
            emails = await asyncio.to_thread(es.check_inbox, unread_only=u_only, limit=lim)
            return {"count": len(emails), "emails": emails}

        # 12. Premium Design & Motion Resources
        elif tool_name == "get_premium_resources":
            from ghost.workers.resources import get_resource, list_all_resources
            res_id = arguments.get("resource_id")
            if res_id:
                res = get_resource(res_id)
                if res:
                    return {"resource": res.dict() if hasattr(res, "dict") else res.__dict__}
                return {"status": "not_found", "message": f"Resource '{res_id}' not found."}
            return {"resources": list_all_resources()}

        # 13. Vercel Cloud Deployment & Token Health
        elif tool_name == "vercel_check_token":
            from ghost.workers.deploy import VercelDeployer
            return await VercelDeployer.check_token_status()

        # 14. Figma REST API Bridge (Post-Contract Production Only)
        elif tool_name == "figma_inspect_file":
            from ghost.workers.figma import FigmaBridge
            f_key = arguments.get("file_key", "")
            d_val = arguments.get("depth", 2)
            return await asyncio.to_thread(FigmaBridge.get_file, file_key=f_key, depth=d_val)

        elif tool_name == "figma_extract_tokens":
            from ghost.workers.figma import FigmaBridge
            f_key = arguments.get("file_key", "")
            return await asyncio.to_thread(FigmaBridge.extract_design_tokens, file_key=f_key)

        # 15. Framer CMS & Component Synthesis Engine (Post-Contract Production Only)
        elif tool_name == "framer_sync_cms":
            from ghost.workers.framer import FramerBridge
            c_id = arguments.get("collection_id", "")
            items_list = arguments.get("items", [])
            p_id = arguments.get("project_id")
            return await asyncio.to_thread(FramerBridge.sync_cms_items, collection_id=c_id, items=items_list, project_id=p_id)

        elif tool_name == "framer_generate_code_component":
            from ghost.workers.framer import FramerBridge
            from ghost.workers.demo_builder import ARCHETYPE_STYLES
            c_name = arguments.get("component_name", "Card")
            arch_idx = int(arguments.get("archetype", 1))
            m_spring = bool(arguments.get("motion_spring", True))
            style = ARCHETYPE_STYLES.get(arch_idx, ARCHETYPE_STYLES[1])
            return await asyncio.to_thread(
                FramerBridge.generate_framer_component,
                name=c_name,
                style_tokens=style,
                motion_spring=m_spring
            )

        # 16. Native OS Accessibility Engine (agent-desktop)
        elif tool_name == "agent_desktop_command":
            from ghost.mcp.bridge import AgentDesktopBridge
            action = arguments.get("action", "snapshot").lower().strip()
            target_ref = arguments.get("target_ref")
            app_name = arguments.get("app")
            text_val = arguments.get("text")
            key_val = arguments.get("key")
            role_val = arguments.get("role")
            is_headed = bool(arguments.get("headed", False))

            if action == "snapshot":
                return await AgentDesktopBridge.snapshot(app=app_name)
            elif action == "find":
                return await AgentDesktopBridge.find(query=text_val, role=role_val, name=text_val, app=app_name)
            elif action in ["click", "double_click", "right_click", "type", "press", "scroll", "focus"]:
                cmd_map = {
                    "click": "click",
                    "double_click": "double-click",
                    "right_click": "right-click",
                    "type": "type",
                    "press": "press",
                    "scroll": "scroll",
                    "focus": "focus"
                }
                cmd_exec = cmd_map.get(action, action)
                val = text_val or key_val
                return await AgentDesktopBridge.action(
                    command=cmd_exec,
                    target_ref=target_ref,
                    value=val,
                    headed=is_headed,
                    app=app_name
                )
            else:
                return {"error": f"Unsupported agent_desktop action: {action}"}

        # 17. Deterministic Git Engine (with Headless SSH_ASKPASS support)
        elif tool_name == "git_action":
            from ghost.mcp.bridge import GitBridge
            raw_cmd = arguments.get("command", "status").strip()
            repo_path = arguments.get("repo_dir", REPO_DIR)
            parts = [p.strip() for p in raw_cmd.split() if p.strip()]
            if parts and parts[0] == "git":
                parts = parts[1:]
            return await GitBridge.run_git(args=parts, repo_dir=repo_path)

        # 18. Safe System CLI Run Fallback
        elif tool_name == "system_run":
            cmd = arguments.get("command", "")
            return await ShellExecutor.run(cmd)

        # Directives / Memory
        elif tool_name == "manage_directives":
            op = arguments.get("operation")
            cnt = arguments.get("content", "")
            if op == "add_fact":
                return DirectiveManager.add_directive(cnt, category="facts")
            elif op == "add_directive":
                return DirectiveManager.add_directive(cnt, category="directives")
            elif op == "list":
                return DirectiveManager.list_all()
            elif op == "remove":
                return DirectiveManager.remove_directive(cnt)
            else:
                return {"error": "Invalid operation"}

        # 19. Venue Qualification & Anti-False-Positive Scout Engine
        elif tool_name == "qualify_venue_lead":
            from ghost.workers.venue_qualification import VenueQualificationPipeline
            bname = arguments.get("name", "")
            loc = arguments.get("location", "Bengaluru")
            m_web = arguments.get("maps_website")
            extra = {
                "founders": arguments.get("founders"),
                "address": arguments.get("address"),
                "phone": arguments.get("phone"),
                "instagram": arguments.get("instagram"),
                "category": arguments.get("category"),
                "setting": arguments.get("setting"),
                "access_model": arguments.get("access_model"),
            }
            return await VenueQualificationPipeline.scout_and_qualify(
                business_name=bname,
                location=loc,
                maps_website=m_web,
                extra_venue_data=extra
            )

        # 20. Mission & Task Decomposition Engine
        elif tool_name == "mission_create":
            from ghost.core.mission import MissionStore
            store = MissionStore.get_instance()
            goal = str(arguments.get("goal", "")).strip()
            tasks = arguments.get("tasks", [])
            skill = arguments.get("skill")
            notes = str(arguments.get("notes", "")).strip()
            # Capability injection (Operator's directive): the mission CARRIES
            # the capabilities its goal matches — the executor sees them on
            # every task, planning can't forget the toolbox exists.
            try:
                from ghost.core.skills import capability_directive
                directive = capability_directive(goal)
                if directive:
                    notes = f"{directive}\n\n{notes}".strip()
                    if tasks and isinstance(tasks[0], dict):
                        first = dict(tasks[0])
                        first["description"] = (
                            f"[Capabilities to apply — see mission notes]\n"
                            f"{first.get('description', '')}").strip()
                        tasks[0] = first
            except Exception:
                pass
            try:
                mission = store.create_mission(goal=goal, tasks=tasks, skill=skill, notes=notes)
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "mission_create",
                    "mission_id": mission.id,
                    "task_count": len(mission.tasks),
                    "tasks": [t.to_dict() for t in mission.tasks]
                }
            except Exception as e:
                return {
                    "ok": False,
                    "status": "failed",
                    "tool": "mission_create",
                    "error": str(e)
                }

        elif tool_name == "mission_status":
            from ghost.core.mission import MissionStore
            store = MissionStore.get_instance()
            m_id = arguments.get("mission_id")
            mission = store.get_mission(m_id) if m_id else store.active_mission()
            if not mission:
                return {
                    "ok": False,
                    "status": "not_found",
                    "tool": "mission_status",
                    "error": "No active mission found"
                }
            return {
                "ok": True,
                "status": "success",
                "tool": "mission_status",
                "mission": mission.to_dict()
            }

        elif tool_name == "mission_update_task":
            from ghost.core.mission import MissionStore
            store = MissionStore.get_instance()
            m_id = arguments.get("mission_id")
            if not m_id:
                act = store.active_mission()
                m_id = act.id if act else None
            if not m_id:
                return {
                    "ok": False,
                    "status": "failed",
                    "tool": "mission_update_task",
                    "error": "No active mission found to update"
                }

            t_id = str(arguments.get("task_id", "")).strip()
            stat = str(arguments.get("status", "")).strip()
            summary = str(arguments.get("result_summary", "")).strip()
            reason = str(arguments.get("reason", "")).strip()
            ev_list = arguments.get("evidence", [])

            try:
                task = store.update_task(
                    mission_id=m_id,
                    task_id=t_id,
                    status=stat,
                    result_summary=summary or reason,
                    evidence=ev_list,
                    failure_reason=reason
                )
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "mission_update_task",
                    "task": task.to_dict()
                }
            except Exception as e:
                return {
                    "ok": False,
                    "status": "failed",
                    "tool": "mission_update_task",
                    "error": str(e)
                }

        elif tool_name == "mission_add_task":
            from ghost.core.mission import MissionStore
            store = MissionStore.get_instance()
            m_id = arguments.get("mission_id")
            if not m_id:
                act = store.active_mission()
                m_id = act.id if act else None
            if not m_id:
                return {
                    "ok": False,
                    "status": "failed",
                    "tool": "mission_add_task",
                    "error": "No active mission found"
                }

            task_dict = arguments.get("task", {})
            try:
                task = store.add_task(mission_id=m_id, task_data=task_dict)
                return {
                    "ok": True,
                    "status": "success",
                    "tool": "mission_add_task",
                    "task": task.to_dict()
                }
            except Exception as e:
                return {
                    "ok": False,
                    "status": "failed",
                    "tool": "mission_add_task",
                    "error": str(e)
                }

        # 21. Free Asset Stack & GLB Tools
        elif tool_name == "glb_inspect":
            from ghost.workers.glb_tools import GLBTools
            f_path = str(arguments.get("file_path", "")).strip()
            return GLBTools.inspect(f_path)

        elif tool_name == "glb_optimize":
            from ghost.workers.glb_tools import GLBTools
            src = str(arguments.get("source_path", "")).strip()
            dest = arguments.get("output_path")
            opts = arguments.get("options")
            return await GLBTools.optimize(source_path=src, output_path=dest, options=opts)

        elif tool_name == "find_glb":
            from ghost.workers.find_glb import GLBDiscovery
            q = str(arguments.get("query", "")).strip()
            lim = int(arguments.get("max_results", 5))
            return GLBDiscovery.find_glb(query=q, max_results=lim)

        elif tool_name == "download_glb":
            from ghost.workers.find_glb import GLBDiscovery
            c_url = str(arguments.get("candidate_url", "")).strip()
            d_dir = str(arguments.get("destination_dir", "")).strip()
            lic = str(arguments.get("license_name", "")).strip()
            auth = str(arguments.get("author", "")).strip()
            op_app = bool(arguments.get("operator_approved", False))
            sl = arguments.get("slug")
            return GLBDiscovery.download_glb(
                candidate_url=c_url,
                destination_dir=d_dir,
                license_name=lic,
                author=auth,
                operator_approved=op_app,
                slug=sl
            )

        elif tool_name == "generate_image_asset":
            from ghost.workers.asset_providers import AssetManager
            prompt = str(arguments.get("prompt", "")).strip()
            dest_dir = str(arguments.get("destination_dir", "/tmp")).strip()
            fname = str(arguments.get("filename", "hero.jpg")).strip()
            sz = str(arguments.get("size", "1024x1024")).strip()
            pref = arguments.get("preferred_provider")
            return AssetManager.generate_image(
                prompt=prompt,
                destination_dir=dest_dir,
                filename=fname,
                size=sz,
                preferred_provider=pref
            )

        elif tool_name == "get_procedural_audio":
            from ghost.workers.audio_manager import ProceduralAudioManager
            pal = str(arguments.get("palette", "warm")).strip()
            script = ProceduralAudioManager.get_procedural_audio_script(palette=pal)
            return {
                "ok": True,
                "status": "success",
                "tool": "get_procedural_audio",
                "palette": pal,
                "script": script,
                "policy": "Global toggle muted by default, single move sound, no hover overlap."
            }

        # 22. Skills & 3D Site Building Engine
        elif tool_name == "list_skills":
            from ghost.core.skills import list_skills
            skills = list_skills()
            return {
                "ok": True,
                "status": "success",
                "tool": "list_skills",
                "count": len(skills),
                "skills": skills
            }

        elif tool_name == "get_skill":
            from ghost.core.skills import get_skill
            sname = str(arguments.get("name", "")).strip()
            return get_skill(sname)

        elif tool_name == "assess_3d_fit":
            from ghost.workers.fit_3d import Fit3DAssessor
            lslug = str(arguments.get("lead_slug", "")).strip()
            op_req = bool(arguments.get("operator_requested", False))
            # Retrieve CRM client data if exists
            crm = CRMDatabase.get_instance()
            client_data = crm.get_client(lslug) or {}
            return Fit3DAssessor.assess(lead_slug=lslug, lead_data=client_data, operator_requested=op_req)

        elif tool_name == "build_3d_site":
            from ghost.workers.three_site_builder import ThreeSiteBuilder
            slug = str(arguments.get("slug", "")).strip()
            subj = str(arguments.get("subject", "")).strip()
            glb_p = str(arguments.get("glb_path", "")).strip()
            bname = arguments.get("business_name")
            pal = str(arguments.get("palette", "warm")).strip()
            op_app = bool(arguments.get("operator_approved", False))
            return ThreeSiteBuilder.build(
                slug=slug,
                subject=subj,
                glb_path=glb_p,
                business_name=bname,
                palette=pal,
                operator_approved=op_app
            )

        elif tool_name == "qa_3d_site":
            from ghost.workers.three_qa import ThreeQA
            d_dir = str(arguments.get("demo_dir", "")).strip()
            return await ThreeQA.run_qa(demo_dir=d_dir)

        # 23. Flow Router & Motion Flagship Engine
        elif tool_name == "choose_flow":
            from ghost.workers.flow_router import FlowRouter
            lslug = str(arguments.get("lead_slug", "")).strip()
            op_ch = arguments.get("operator_choice")
            crm = CRMDatabase.get_instance()
            client_data = crm.get_client(lslug) or {}
            return FlowRouter.choose_flow(lead_slug=lslug, operator_choice=op_ch, lead_data=client_data)

        elif tool_name == "build_motion_site":
            from ghost.workers.motion_site_builder import MotionSiteBuilder
            slug = str(arguments.get("slug", "")).strip()
            bname = arguments.get("business_name")
            cat = arguments.get("category")
            sig_type = arguments.get("signature_type")
            op_app = bool(arguments.get("operator_approved", False))
            phone = arguments.get("phone")
            wa = arguments.get("whatsapp")
            city = arguments.get("city")
            return MotionSiteBuilder.build(
                slug=slug,
                business_name=bname,
                category=cat,
                signature_type=sig_type,
                operator_approved=op_app,
                phone=phone,
                whatsapp=wa,
                city=city
            )

        elif tool_name == "qa_motion_site":
            from ghost.workers.motion_site_builder import MotionQA
            d_dir = str(arguments.get("demo_dir", "")).strip()
            return await MotionQA.run_qa(demo_dir=d_dir)

        return {"error": f"Tool {tool_name} not recognized"}
