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

CONFIGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../configs"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data"))
OMNIROUTE_CONFIG_PATH = os.path.join(CONFIGS_DIR, "omniroute.json")
CONVERSATION_HISTORY_PATH = os.path.join(DATA_DIR, "conversation_history.json")
OBSIDIAN_SESSION_MEMORY_PATH = "/home/pranc/Obsidian Vault/Ghost/Session Memory.md"

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
                        "description": "Destination URL for open_url"
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
            "description": "Fast visual reconnaissance snapshot of the desktop screen or active window using grim (< 40ms). Ingests image for multimodal visual inspection.",
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
            "description": "Trigger OpenCode / DeepSeek worker to scaffold a high-ticket single-page restaurant or luxury resort HTML/Tailwind demo based on the 4 Design Archetypes (1: Michelin Dark, 2: Coastal Minimalist, 3: Heritage Luxury, 4: High-Energy Bistro). Runs smoke tests and signals Watchdog.",
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
                        "description": "Optional contact details {phone, email, address}"
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
            "name": "email_send_pitch",
            "description": "Send an outbound email or client pitch via authenticated SMTP (pranav31207@gmail.com). Gated by the Tactical Authorization Modal for human-in-the-loop signoff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {
                        "type": "string",
                        "description": "Recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "body": {
                        "type": "string",
                        "description": "Plain text email body"
                    },
                    "client_slug": {
                        "type": "string",
                        "description": "Optional client slug to log interaction to CRM"
                    }
                },
                "required": ["to_email", "subject", "body"]
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
                        "description": "Optional repository path (defaults to '/home/pranc/projects/ghost')"
                    },
                    "passphrase": {
                        "type": "string",
                        "description": "Optional SSH key passphrase for remote operations (e.g. '5437')"
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
    }
]

class OmniRouteBrain:
    """
    High-speed streaming model brain using AsyncOpenAI against OmniRoute.
    Supports real-time function calling execution and multi-turn tool loops.
    """

    SYSTEM_PROMPT = (
        "You are Simon 'Ghost' Riley (Task Force 141), Chief of Staff and Technical Operations Director for Commander Pranav's high-ticket web agency ($1,000+ engagements).\n\n"
        "LIVE OPERATIONAL CAPABILITIES & ARCHITECTURE (DO NOT GUESS OR EXPLORE ALTERNATIVES):\n"
        "1. Local Neural Speech Engine: Kokoro-82M INT8 British military voice ('bm_george') running locally with zero latency (24kHz PCM).\n"
        "2. Email Operations Engine: Authenticated Gmail client for 'pranav31207@gmail.com' via IMAP SSL & SMTP SSL. Use email_check_inbox to monitor incoming messages and email_send_pitch to dispatch pitches (strictly gated by Tactical Authorization Modal).\n"
        "3. Anti-Hallucination CRM & Obsidian Sync: Local SQLite database at data/crm.db bi-directionally synchronized with '/home/pranc/Obsidian Vault/Ghost/Clients/<slug>.md'. Use crm_get_client, crm_update_client, and crm_list_pipeline. ZERO-HALLUCINATION RULE: Never quote speculative pricing, invent client details, or guess deals without checking the CRM.\n"
        "4. Parallel Demo Builder Swarm & Cold Pitch Policy: High-speed OpenCode worker CLI running 'opencode/mimo-v2.6-flash-free' via build_luxury_demo. Generates bespoke single-page HTML/Tailwind demos across the 4 Luxury Archetypes (1: Michelin Editorial, 2: Coastal Minimalist, 3: Heritage Luxury, 4: High-Energy Bistro) with automated Brave CDP responsive smoke tests at 390px mobile and 1920px desktop. MANDATORY THEME RULE: All luxury demos MUST be LIGHT MODE by default (clean editorial palette #fcfbf9 / #faf7f2 / #faf8f5 / #f9f9fb) and MUST include a full DARK MODE with a functional theme switcher toggle button in the navigation header (classList.toggle('dark') + localStorage). HARD RULE: Ghost is STRICTLY FORBIDDEN from building full production websites or burning director tokens on unclosed prospects (status != 'closed_won'). All pre-contract cold pitch demos MUST ONLY be lightweight single-page static HTML demos via OpenCode. Production multi-page site delivery is reserved EXCLUSIVELY for closed $1,000+ accounts powered by Webstudio visual CMS.\n"
        "5. Autonomous Watchdog & Event Bus: Background patrol sweeps running once every 24 hours (86400s), auditing idle leads, logging sweeps to Obsidian Daily Log.md, validating Vercel token status, and receiving instant signal pings via /api/watchdog/signal.\n"
        "6. Desktop, Browser & REA Control: Universal Hyprland desktop management (desktop_control), Brave CDP browser automation on port 9222 (browser_action), media/Spotify control (media_control), and REA binary reverse engineering (rea_action). All destructive system modifications or outbound client communications MUST prompt the operator via the Tactical Authorization Modal (#auth-modal).\n"
        "7. Native OS Accessibility Engine (agent-desktop): When interacting with native Linux GUI applications (Obsidian, Figma, Discord, system dialogs), use agent_desktop_command. Captures structured UI snapshots with @ref IDs and executes clicks/typing with zero vision token bloat.\n"
        "8. REAL-TIME MEDIA & AUDIO PLAYBACK RULES:\n"
        "   - Spotify Music: When asked to play a song/artist (e.g. 'play interstellar', 'play cars 3 soundtrack'), ALWAYS call media_control(action='spotify_action', query='<song or album name>') or media_control(action='play'). The system uses instant track URI resolution and DBus OpenUri on native Wayland (zero X11/xdotool lag). If spotify is on another workspace, use desktop_control(action='switch_workspace', workspace='4') or desktop_control(action='move_to_workspace', target='Spotify', args='4'). Never claim music is playing unless verified via playerctl status.\n"
        "   - Video / Streaming (JioHotstar / YouTube / Web): When asked to resume or play a movie or show in the browser (e.g. 'resume cars 3 in jiohotstar kids profile'), use browser_action(action='focus_tab', target='Cars 3') or browser_action(action='brave_click_and_type', target='video', val='play') or browser_action(action='open_url'). If asked to move the mouse or click on a specific element, use input_simulation(sim_type='click', x=X, y=Y) or browser_action.\n"
        "9. MANDATORY TASK-TO-TOOL INTENT MATRIX (ZERO EXPLORATORY WANDERING):\n"
        "   - Git Operations (push, commit, status, remote): MUST call git_action. When pushing with SSH key passphrase, pass passphrase='5437'. Never wander into bash commands for Git.\n"
        "   - Spotify / Music: MUST call media_control(action='spotify_action', query='...').\n"
        "   - Web Video / Streaming: MUST call browser_action.\n"
        "   - Desktop GUI Apps (Obsidian, Figma, Discord, system dialogs): MUST call agent_desktop_command.\n"
        "   - STATED TRUTH RULE: NEVER claim a command or push succeeded without executing the tool and inspecting its return code.\n\n"
        "OPERATIONAL PROTOCOLS:\n"
        "- When the user asks for an action (close tab, open URL, WhatsApp, media, move/rename files, apps, CRM, email, demo build), ALWAYS call the corresponding tool first.\n"
        "- DEMO GENERATION & CLIENT ACQUISITION FLOW (MANDATORY DEMO PROTOCOL & HARD RULES):\n"
        "  When commanded to build, scaffold, or pitch a demo for any local business, Ghost MUST STRICTLY FOLLOW these HARD RULES:\n"
        "  Rule 1 (Zero-Food Bias): Never assign a food/drink template unless Google category or verified signals explicitly indicate food/drink. Map prospect domain accurately across the 14 catalog industries via classify_business.\n"
        "  Rule 2 (Fail Closed): If the business category is unknown or ambiguous, fail closed to 'general_services' neutral template and request confirmation. Never guess or default to fine_dining.\n"
        "  Rule 3 (Strict Source Ledger): Every fact on the page (services, prices, hours, phone, address, rating, reviews) MUST originate from scraped data and be recorded in SOURCES.json via SourceLedger. Missing facts remain null and render marked placeholders ('[Inquire for quotation]') or are omitted entirely. Never invent plausible defaults or placeholder menus.\n"
        "  Rule 4 (Zero Fabricated Metrics): No artificial benchmark scores ('99/100', '0.38s'), speed claims, or unverified 'Verified' stamps are ever placed on client demos.\n"
        "  Rule 5 (Persona Firewall on Outbound Pitch): All client-facing outbound copy (email, DM, WhatsApp, PITCH_KIT) is authored and signed personally as Pranav. Zero internal persona words (Ghost, Task Force 141, Chief of Staff, Deep-Strike, Commander, sir/mate/boss), zero local file:// links, and zero local laptop paths.\n"
        "  Rule 6 (Hosted Delivery Prerequisite): No outreach pitch is produced without a real hosted https URL deployed via Vercel ({slug}-concept). If unhosted, halt at status 'needs_deploy'.\n"
        "  Step 3: Enforce ONE Primary Conversion Action: The whole demo funnels to ONE action (WhatsApp Concierge for table booking/appointment, direct call, or 1-tap reserve).\n"
        "  Step 4: 8 to 12 Contextual Editorial Sections (Never scope down to just 4 sections; choose what fits each brand):\n"
        "    1. Contextual Navigation (Ghost dynamically selects the appropriate navigation style for the archetype: Minimalist Solid for fine dining, Floating Pill for resorts, Classic Heritage for traditional hotels, Liquid Glass for bistros/lounges — NOT every business needs a glass nav; Ghost decides what fits best)\n"
        "    2. Hero Section (Real high-res photography backdrop, gap callout badge, staggered reveals, dual CTAs)\n"
        "    3. Operational Gap & Value Prop Banner (Highlights the resolved friction, e.g. 'Instant 1-Tap Table Concierge')\n"
        "    4. Story & Craft Philosophy (Brand ethos and culinary/hospitality narrative extracted from real bio/listing)\n"
        "    5. Bento Grid Offerings (Signature highlight dish/service, secondary highlights, exact pricing, filterable categories)\n"
        "    6. Quality Proof & Accreditations (ONLY rendered if verified metrics or awards exist; if unverified, OMIT entirely — zero fake scores)\n"
        "    7. Ambiance & Space Gallery (Multi-photo mosaic of real interior, dining room, and atmosphere)\n"
        "    8. Verified Social Proof (ONLY rendered if real Google Reviews exist: infinite marquee with actual star ratings and real quotes; if none found, OMIT entirely — zero fake reviews)\n"
        "    9. Performance & Conversion Delta Card (Side-by-side audit benchmark: their 8s page vs our sub-second demo)\n"
        "    10. Curated Journey / How It Works (3 clear steps from reservation to table)\n"
        "    11. Interactive VIP WhatsApp Booking Card (Party size & date quick-selector -> prefilled WhatsApp chat)\n"
        "    12. Location, Hours, Contact & Conversion Footer (Interactive hours/address card, oversized brand typography, 'Concept design presented by Ghost' credit)\n"
        "  Step 5: Restrained Luxury Design & Color Hierarchy Guide (Reference '/home/pranc/projects/ghost/context/COLOR_HIERARCHY_GUIDE.md'):\n"
        "    - Depth Layers: Never use one flat color for canvas and surface. Light Mode uses Canvas #F2F2F7, Surface #FFFFFF, Raised #FFFFFF (with deeper shadow). Dark Mode uses Canvas #121212, Surface #1E1E1E, Raised #2A2A2A.\n"
        "    - Elevation & Shadows: In light mode, let soft layered shadows do the lifting (resting 0 1px 2px rgba(0,0,0,.06), hover 0 8px 24px rgba(0,0,0,.10)). In dark mode, step up surface lightness; add 1px border at 8% white if edges blur. Hairline border only where same-color surfaces touch (6-8% ink).\n"
        "    - Text / Ink: Soften black; pure #000 on white vibrates. Use Primary Ink #1D1B20 (17:1) and Secondary Ink #49454F (9:1) for labels/helper text. Dark mode uses Primary #E6E1E5, Secondary #CAC4D0. Never invent a third gray or use disabled gray for readable text.\n"
        "    - The Accent (10% Rule): Exactly ONE accent pair (deepened on light >=4.5:1, lightened on dark). 60% canvas, 30% surface/text, 10% accent. Tint don't shout: use 8-12% accent tint for secondary accent cards.\n"
        "    - Status Colors: Used only for state/meaning, never beside accent as decoration. Light success #1B7F4B, warning #A15C00, danger #BA1A1A.\n"
        "    - Semantic Tokens Only: No raw hex in components. All elements must reference CSS custom properties (--canvas, --surface, --raised, --ink, --ink-2, --accent, --line, --shadow-resting, --shadow-hover).\n"
        "  Step 6: Mobile-First Verification: Headless Brave CDP smoke test at 390x844 mobile first (owners open pitches on phones!), then 1440x900 desktop, verifying 0 layout overflow and 0 console errors.\n"
        "  Step 7: Mandatory Pitch Kit Output (RULE B):\n"
        "    Ghost must NEVER just hand over a standalone HTML file. Ghost MUST output a complete Pitch Kit containing:\n"
        "    a. Hosted preview link / local file preview link\n"
        "    b. 2-3 verified screenshots (mobile 390x844 and desktop 1440x900)\n"
        "    c. 3-Part Outreach Copy formatted specifically for WhatsApp / Instagram DM:\n"
        "       Part 1: What you noticed (The Gap: e.g. 'Your customers can\'t book directly from Instagram / your site takes 7 seconds to load')\n"
        "       Part 2: What you built ('Built a sub-second interactive mobile concept for you: [Link]')\n"
        "       Part 3: One easy next step ('Want me to set this up or hand over the keys?')\n"
        "    d. Follow up once after 2-3 days, then move on.\n"
        "  Never output raw speculative full-site code or burn director turns writing HTML manually. Pre-contract bait is strictly single-page static HTML via OpenCode.\n"
        "- INDUSTRY TEMPLATE ARCHITECTURE & SELECTION DIRECTIVE:\n"
        "  Ghost has access to the comprehensive Industry Templates Catalog at '/home/pranc/projects/ghost/context/templates/INDUSTRY_TEMPLATES_CATALOG.md' covering 14 local business sectors (Restaurants, Spas/Salons, Gyms, Dental/Medical Clinics, Boutiques/Fashion, Real Estate, Photography, Hotels/Homestays, Bakeries, Home Services/Auto, Coaches, Legal/Advisors, Weddings/Florists, and Academies/Tutors).\n"
        "  TEMPLATE SELECTION PROTOCOL (HOW GHOST DECIDES THE BEST TEMPLATE PER PROSPECT):\n"
        "  When targeting any business, Ghost MUST dynamically evaluate 3 concrete criteria to pick the optimal template archetype reference rather than guessing:\n"
        "  1. Ticket Size & Customer Engagement Depth:\n"
        "     - High-ticket / luxury ($100+ per visit or $1,000+ engagements): Select editorial, multi-course / bespoke consultation structures (e.g. LuxDin for fine dining, Solanki Atelier for bridal/salons, Habité for luxury villas, LuxHotel for 5-star hospitality, CoachFlow for consultants).\n"
        "     - High-velocity / local convenience ($10-$50): Select conversion-first item grids with fast ordering (e.g. Cuisine / Pepper for cafés/pizzas, PureGlamy for neighborhood hair studios, QuickFix for emergency trades, SweetCrumb for bakeries).\n"
        "  2. Primary Operational Bleed / Gap Resolved:\n"
        "     - Lost booking due to telephone waiting or Instagram friction -> Pick 1-tap WhatsApp Concierge architectures (LUXÉ for retail, SacredStays to bypass Airbnb's 15% fees, Solena for skincare).\n"
        "     - Scheduling/timetable confusion -> Pick live schedule & slot-picker architectures (Drivox for gyms, Private Tutor for education).\n"
        "     - Emergency service dispatch -> Pick immediate prominent phone callout (QuickFix / Plumbex).\n"
        "  3. Visual Material & Navigation Match:\n"
        "     - Dark/Cinematic (Bramble, Captura, CoachVerse, Gymnastic X)\n"
        "     - Minimalist Solid Editorial (LuxDin, LINÉA, ZahrahPhoto)\n"
        "     - Floating Pill Coastal (SOLENE, Solena, Brightside)\n"
        "     - Classic Institutional (Harlow & Partners, Lawli, Houses&Flats)\n"
        "  OPERATIONAL RULE: Templates provide layout structure, section ordering, and conversion mechanics ONLY. Ghost MUST rebuild them using the prospect's real assets (real photos, real menu, real reviews or none, WhatsApp concierge link). Never copy lorem ipsum or fake template placeholder data.\n"
        "- FIGMA & FRAMER USAGE RULE: Figma and Framer integrations are strictly for POST-CONTRACT production website building ($1,000+ closed clients). Never use Figma/Framer or attempt to parse design files for pre-contract cold pitch demos. Cold pitch demos strictly use OpenCode state machine. Use figma_inspect_file/figma_extract_tokens and framer_sync_cms/framer_generate_code_component for closed $1,000+ client site delivery.\n"
        "- VERCEL TOKEN HEALTH PROTOCOL: Whenever performing deployment or asked about Vercel status, or during routine health checks, verify if the Vercel token is valid or expired using vercel_check_token. If the token is expired or unauthorized (401/403), immediately alert Commander Pranav that his 30-day PAT has expired and needs renewal at https://vercel.com/account/tokens .\n"
        "- STREAMING & BROWSING DIRECTIVE: When opening or navigating JioHotstar/Hotstar (hotstar.com), if the 'Who's watching?' or profile selection screen appears, automatically select the 'Pranav' profile using browser_action(action='click_and_type', target='Pranav'). Do not leave the user stuck on the profile picker. If user asks for netmirror, always use this link: https://net77.cc/home .\n"
        "- UNLIMITED OPERATIONAL DISCIPLINE & SPEED: You have unlimited operational turns and steps to execute missions thoroughly from start to finish. Do not stop midway, do not cut corners, and do not halt complex multi-step workflows until the entire objective is achieved. Maintain tactical focus: execute tools decisively, avoid redundant loops, and deliver a crisp closing tactical report upon complete mission victory.\n"
        "- ASYNC EVENT-DRIVEN RECON & AUDIT SPEED: The reconnaissance (ReconScout) and performance audit engines (PerformanceAuditor) operate on non-blocking async CDP with direct lifecycle event buffering. Scrapes and performance delta audits resolve in sub-seconds with zero artificial sleep delays. Fast sites audit immediately upon Page.loadEventFired.\n"
        "- FAILURE CIRCUIT BREAKER & ADAPTIVE PROTOCOL: Ghost is equipped with an automated loop deduplication and failure circuit breaker. If any tool call is repeated 3 consecutive times with identical arguments yielding errors or identical output, the breaker intercepts with diagnostic guidance; 4 attempts will halt the spinning loop. OPERATIONAL RULE: When a tool fails or the circuit breaker trips, NEVER retry the exact same failing tool call with identical parameters. Adapt your tactical plan immediately: try alternative arguments, switch to another tool primitive, inspect system state, or report the obstacle concisely to Commander Pranav.\n"
        "- AUTHORIZATION PROTOCOL: Gated destructive or critical system actions still require authorization via desktop_control(action='request_authorization', target='...'). NEVER merely speak or text 'Requesting tactical authorization' without executing the tool so the interactive modal appears on the operator HUD.\n"
        "- You have modular system references at '/home/pranc/projects/ghost/context/' (all_available_apps/, all_tools/, user/). Only read specific context files via desktop_control(action='read_context', target='...') when strictly required to save tokens.\n"
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
    async def execute_tool_call(cls, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool invoked by the LLM function call."""
        logger.info(f"Executing tool call: {tool_name} with args: {arguments}")

        # 1. Desktop & Hyprland Primitive
        if tool_name in ["desktop_control", "hyprland_control", "system_app_launch", "hyprland_dispatch"]:
            act = arguments.get("action", "hyprctl_dispatch")
            target = arguments.get("target") or arguments.get("workspace_id") or arguments.get("app_name") or ""
            args = arguments.get("args") or arguments.get("workspace") or ""

            if act == "switch_workspace":
                ws = target or args or "1"
                res = await SystemAppBridge.hyprctl_dispatch("workspace", ws)
            elif act == "move_to_workspace":
                ws = target or args or "1"
                res = await SystemAppBridge.hyprctl_dispatch("movetoworkspace", ws)
            elif act == "focus_window":
                res = await SystemAppBridge.hyprctl_dispatch("focuswindow", target)
            elif act == "close_window":
                res = await SystemAppBridge.hyprctl_dispatch("killactive")
            elif act == "resize_window":
                params = args or target or "50 0"
                res = await SystemAppBridge.hyprctl_dispatch("resizeactive", params)
            elif act == "toggle_floating":
                res = await SystemAppBridge.hyprctl_dispatch("togglefloating")
            elif act == "toggle_fullscreen":
                res = await SystemAppBridge.hyprctl_dispatch("fullscreen", "0")
            elif act == "set_wallpaper":
                res = await SystemAppBridge.set_wallpaper(target or args or "ghost")
            elif act in ["toggle_wallpaper", "wallpaper"]:
                res = await SystemAppBridge.quickshell_ipc("notch toggle wallpaper")
            elif act in ["move_file", "rename_file", "mv"]:
                res = await SystemAppBridge.file_operation("move", source=target, destination=args)
            elif act in ["copy_file", "cp"]:
                res = await SystemAppBridge.file_operation("copy", source=target, destination=args)
            elif act in ["delete_file", "rm"]:
                res = await SystemAppBridge.file_operation("delete", source=target)
            elif act in ["request_authorization", "request_consent", "authorize"]:
                from ghost.mcp.bridge import request_tactical_authorization
                allowed = await request_tactical_authorization(target or args or "General action authorization requested")
                res = {"status": "authorized" if allowed else "denied", "allowed": allowed, "action": target}
            elif act == "read_context":
                ctx_path = os.path.join("/home/pranc/projects/ghost/context", target.lstrip("/"))
                if os.path.exists(ctx_path) and os.path.isfile(ctx_path):
                    with open(ctx_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    res = {"status": "success", "file": target, "content": content}
                else:
                    res = {"status": "error", "message": f"Context file '{target}' not found."}
            elif act == "launch_app":
                res = await SystemAppBridge.launch_app(target, workspace=args if args else None)
            elif act == "quickshell_ipc":
                res = await SystemAppBridge.quickshell_ipc(target)
            else:
                disp = arguments.get("dispatcher") or target or "workspace"
                res = await SystemAppBridge.hyprctl_dispatch(disp, args)
            return {"status": "success", "result": res}

        # 2. Universal Media & Spotify Primitive
        elif tool_name in ["media_control", "music_control", "spotify_action"]:
            act = arguments.get("action", "play")
            query = arguments.get("query")
            vol = arguments.get("volume")

            if act == "play_query" or (query and act in ["play", "liked", "liked_songs"]):
                res = await MediaBridge.spotify_action(action="play_query", query=query)
            elif act == "play":
                res = await MediaBridge.spotify_action(action="play")
            elif act == "pause":
                res = await MediaBridge.spotify_action(action="pause")
            elif act == "play_pause":
                res = await MediaBridge.play_pause()
            elif act == "next":
                res = await MediaBridge.next_track()
            elif act == "previous":
                res = await MediaBridge.previous_track()
            elif act == "get_track":
                res = await MediaBridge.get_current_track()
            elif act == "volume_up":
                res = await MediaBridge.adjust_volume("+5%")
            elif act == "volume_down":
                res = await MediaBridge.adjust_volume("-5%")
            elif act == "set_volume":
                res = await MediaBridge.set_volume(vol or "50%")
            else:
                res = await MediaBridge.spotify_action(action=act, query=query)
            return {"status": "success", "result": res}

        # 3. Universal Browser CDP Primitive
        elif tool_name in ["browser_action", "brave_open_url", "brave_list_tabs", "brave_focus_tab", "brave_close_tab", "brave_click_and_type", "whatsapp_web_action"]:
            act = arguments.get("action", "")
            url = arguments.get("url")
            target = arguments.get("target") or arguments.get("tab_id") or arguments.get("selector_or_label") or ""
            val = arguments.get("value") or arguments.get("text") or arguments.get("message_text")
            press_enter = arguments.get("press_enter", False)

            if tool_name == "brave_open_url" or act == "open_url":
                dest = url or target
                if not dest.startswith("http"):
                    dest = f"https://{dest}"
                res = await BraveCDPBridge.new_tab(dest)
                return {"status": "success", "result": res}
            elif tool_name == "brave_list_tabs" or act == "list_tabs":
                tabs = await BraveCDPBridge.list_tabs()
                valid_tabs = [t for t in tabs if isinstance(t, dict) and "title" in t]
                return {"status": "success", "count": len(valid_tabs), "tabs": valid_tabs[:6]}
            elif tool_name == "brave_focus_tab" or act == "focus_tab":
                res = await BraveCDPBridge.activate_tab(target)
                return {"status": "success", "result": res}
            elif tool_name == "brave_close_tab" or act == "close_tab":
                res = await BraveCDPBridge.close_tab(target)
                return {"status": "success", "result": res}
            elif tool_name == "brave_click_and_type" or act == "click_and_type":
                res = await BraveCDPBridge.click_and_type(target, text=val, press_enter=press_enter)
                return {"status": "success", "result": res}
            elif tool_name == "whatsapp_web_action" or act == "whatsapp_action":
                wa_act = arguments.get("action", "open")
                if wa_act == "whatsapp_action":
                    wa_act = "message" if val else "open"
                res = await WhatsAppBridge.action(wa_act, target=target, message_text=val)
                return {"status": "success", "result": res}
            else:
                # Default browser fallback: open url
                if url:
                    res = await BraveCDPBridge.new_tab(url)
                    return {"status": "success", "result": res}
                res = {"error": f"Unknown browser action: {act}"}
                return {"status": "error", "result": res}

        # 4. Recon & Direct Interaction: Screen Capture, Clipboard, Simulation, Notify
        elif tool_name == "capture_screen":
            reg = arguments.get("region", "full")
            res = await SystemAppBridge.capture_screen(region=reg)
            return {"status": "success", "result": res}

        elif tool_name == "clipboard_action":
            op = arguments.get("operation", "read")
            text = arguments.get("text")
            res = await SystemAppBridge.clipboard_action(operation=op, text=text)
            return {"status": "success", "result": res}

        elif tool_name == "input_simulation":
            stype = arguments.get("type", "key")
            keys = arguments.get("keys")
            x = arguments.get("x")
            y = arguments.get("y")
            wq = arguments.get("window_query")
            res = await SystemAppBridge.input_simulation(sim_type=stype, keys=keys, x=x, y=y, window_query=wq)
            return {"status": "success", "result": res}

        elif tool_name == "desktop_notify":
            title = arguments.get("title", "Ghost Recon")
            msg = arguments.get("message", "")
            urg = arguments.get("urgency", "normal")
            res = await SystemAppBridge.desktop_notify(title=title, message=msg, urgency=urg)
            return {"status": "success", "result": res}

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
            res = await ReaBridge.run_command(cmd, args_list)
            return {"status": "success", "result": res}

        # 6. Web Search MCP Bridge
        elif tool_name == "web_search":
            q = arguments.get("query", "")
            limit = int(arguments.get("max_results", 5))
            res = await WebSearchBridge.search(q, max_results=limit)
            return {"status": "success", "result": res}

        # 7. Database Query MCP Bridge (SQLite / Postgres)
        elif tool_name == "database_query":
            db = arguments.get("db_path", "")
            q = arguments.get("query", "")
            res = await DatabaseBridge.query_sqlite(db, q)
            return {"status": "success", "result": res}

        # 8. GitHub MCP Bridge
        elif tool_name == "github_action":
            act = arguments.get("action", "get_repo")
            repo = arguments.get("repo")
            path = arguments.get("path")
            q = arguments.get("query")
            res = await GitHubBridge.run_action(action=act, repo=repo, path=path, query=q)
            return {"status": "success", "result": res}

        # 9. CRM & Anti-Hallucination Pipeline Primitives
        elif tool_name == "crm_get_client":
            slug = arguments.get("slug", "")
            crm = CRMDatabase.get_instance()
            res = crm.get_client(slug)
            if res:
                return {"status": "success", "client": res}
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
            return {"status": "success", "client": res}

        elif tool_name == "crm_list_pipeline":
            crm = CRMDatabase.get_instance()
            stat = arguments.get("status")
            clients = crm.list_clients(status=stat)
            return {"status": "success", "count": len(clients), "pipeline": clients}

        # 10. OpenCode / DeepSeek Luxury Demo Builder Worker Swarm
        elif tool_name == "build_luxury_demo":
            from ghost.workers.demo_builder import DemoBuilderWorker
            rname = arguments.get("restaurant_name", "Luxury Lounge")
            arch = int(arguments.get("archetype", 1))
            tag = arguments.get("tagline")
            m_items = arguments.get("menu_items")
            c_info = arguments.get("contact_info")
            res = await DemoBuilderWorker.build_demo(
                restaurant_name=rname,
                archetype=arch,
                tagline=tag,
                menu_items=m_items,
                contact_info=c_info
            )
            return {"status": "success", "result": res}

        # 11. Agency Email Engine (IMAP SSL / SMTP SSL)
        elif tool_name == "email_check_inbox":
            from ghost.comms.email_service import EmailService
            es = EmailService.get_instance()
            u_only = bool(arguments.get("unread_only", True))
            lim = int(arguments.get("limit", 5))
            emails = await asyncio.to_thread(es.check_inbox, unread_only=u_only, limit=lim)
            return {"status": "success", "count": len(emails), "emails": emails}

        elif tool_name == "email_send_pitch":
            from ghost.comms.email_service import EmailService
            from ghost.mcp.bridge import request_tactical_authorization
            to_addr = arguments.get("to_email", "")
            subj = arguments.get("subject", "")
            body_txt = arguments.get("body", "")
            c_slug = arguments.get("client_slug")

            # Enforce Tactical Authorization Modal before outbound transmission
            allowed = await request_tactical_authorization(
                f"Dispatch outbound agency pitch email to {to_addr}:\nSubject: '{subj}'"
            )
            if not allowed:
                return {"status": "denied", "message": "Email dispatch aborted per operator orders, sir."}

            es = EmailService.get_instance()
            res = await asyncio.to_thread(es.send_email, to_email=to_addr, subject=subj, body=body_txt)
            if res.get("status") == "success" and c_slug:
                try:
                    crm = CRMDatabase.get_instance()
                    crm.log_interaction(
                        client_slug=c_slug,
                        channel="email",
                        direction="outbound",
                        content=f"Sent Pitch Subject: {subj} | {body_txt[:120]}"
                    )
                except Exception:
                    pass
            return {"status": "success", "result": res}

        # 12. Premium Design & Motion Resources
        elif tool_name == "get_premium_resources":
            from ghost.workers.resources import get_resource, list_all_resources
            res_id = arguments.get("resource_id")
            if res_id:
                res = get_resource(res_id)
                if res:
                    return {"status": "success", "resource": res.dict() if hasattr(res, "dict") else res.__dict__}
                return {"status": "not_found", "message": f"Resource '{res_id}' not found."}
            return {"status": "success", "resources": list_all_resources()}

        # 13. Vercel Cloud Deployment & Token Health
        elif tool_name == "deploy_demo_vercel":
            from ghost.workers.deploy import VercelDeployer
            slug = arguments.get("slug", "")
            p_name = arguments.get("project_name")
            res = await VercelDeployer.deploy_demo(slug=slug, project_name=p_name)
            return res

        elif tool_name == "vercel_check_token":
            from ghost.workers.deploy import VercelDeployer
            res = await VercelDeployer.check_token_status()
            return res

        # 14. Figma REST API Bridge (Post-Contract Production Only)
        elif tool_name == "figma_inspect_file":
            from ghost.workers.figma import FigmaBridge
            f_key = arguments.get("file_key", "")
            d_val = arguments.get("depth", 2)
            res = await asyncio.to_thread(FigmaBridge.get_file, file_key=f_key, depth=d_val)
            return res

        elif tool_name == "figma_extract_tokens":
            from ghost.workers.figma import FigmaBridge
            f_key = arguments.get("file_key", "")
            res = await asyncio.to_thread(FigmaBridge.extract_design_tokens, file_key=f_key)
            return res

        # 15. Framer CMS & Component Synthesis Engine (Post-Contract Production Only)
        elif tool_name == "framer_sync_cms":
            from ghost.workers.framer import FramerBridge
            c_id = arguments.get("collection_id", "")
            items_list = arguments.get("items", [])
            p_id = arguments.get("project_id")
            res = await asyncio.to_thread(FramerBridge.sync_cms_items, collection_id=c_id, items=items_list, project_id=p_id)
            return res

        elif tool_name == "framer_generate_code_component":
            from ghost.workers.framer import FramerBridge
            from ghost.workers.demo_builder import ARCHETYPE_STYLES
            c_name = arguments.get("component_name", "Card")
            arch_idx = int(arguments.get("archetype", 1))
            m_spring = bool(arguments.get("motion_spring", True))
            style = ARCHETYPE_STYLES.get(arch_idx, ARCHETYPE_STYLES[1])
            res = await asyncio.to_thread(
                FramerBridge.generate_framer_component,
                name=c_name,
                style_tokens=style,
                motion_spring=m_spring
            )
            return res

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
                res = await AgentDesktopBridge.snapshot(app=app_name)
            elif action == "find":
                res = await AgentDesktopBridge.find(query=text_val, role=role_val, name=text_val, app=app_name)
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
                res = await AgentDesktopBridge.action(
                    command=cmd_exec,
                    target_ref=target_ref,
                    value=val,
                    headed=is_headed,
                    app=app_name
                )
            else:
                res = {"ok": False, "error": f"Unsupported agent_desktop action: {action}"}
            return {"status": "success" if res.get("ok", True) else "error", "result": res}

        # 17. Deterministic Git Engine (with Headless SSH_ASKPASS support)
        elif tool_name == "git_action":
            from ghost.mcp.bridge import GitBridge
            raw_cmd = arguments.get("command", "status").strip()
            repo_path = arguments.get("repo_dir", "/home/pranc/projects/ghost")
            pass_key = arguments.get("passphrase") or "5437"
            parts = [p.strip() for p in raw_cmd.split() if p.strip()]
            if parts and parts[0] == "git":
                parts = parts[1:]
            res = await GitBridge.run_git(args=parts, repo_dir=repo_path, passphrase=pass_key)
            return {"status": "success" if res.get("success", False) else "error", "result": res}

        # 18. Safe System CLI Run Fallback
        elif tool_name == "system_run":
            cmd = arguments.get("command", "")
            res = await ShellExecutor.run(cmd)
            return {"status": "success", "result": res}

        # Directives / Memory
        elif tool_name == "manage_directives":
            op = arguments.get("operation")
            cnt = arguments.get("content", "")
            if op == "add_fact":
                res = DirectiveManager.add_directive(cnt, category="facts")
            elif op == "add_directive":
                res = DirectiveManager.add_directive(cnt, category="directives")
            elif op == "list":
                res = DirectiveManager.list_all()
            elif op == "remove":
                res = DirectiveManager.remove_directive(cnt)
            else:
                res = {"error": "Invalid operation"}
            return {"status": "success", "result": res}

        return {"status": "error", "message": f"Tool {tool_name} not recognized"}
