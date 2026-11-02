"""Project Lifecycle, Context Retention & Company Operations Framework for Ghost.
Loaded on-demand via desktop_control(action='read_context', target='all_tools/project.py').
"""
from typing import Dict, Any, List, Optional
import os

# 1. Project Data Schema & Lifecycle State Machine
# Status choices: 'ideation' | 'planning' | 'active_development' | 'testing' | 'deployed' | 'paused'
class ProjectSchema:
    def __init__(
        self,
        name: str,
        path: str,
        status: str = "active_development",
        repo: Optional[str] = None,
        stack: Optional[List[str]] = None,
        roadmap: Optional[List[Dict[str, Any]]] = None,
        tasks: Optional[List[Dict[str, Any]]] = None
    ):
        self.name = name
        self.path = os.path.abspath(os.path.expanduser(path))
        self.status = status
        self.repo = repo
        self.stack = stack or []
        self.roadmap = roadmap or []
        self.tasks = tasks or []

# 2. Company & Business Management Domains:
# - Product Engineering: Architecture, code sprints, issue triage, deployments
# - Finance & Budgeting: Monthly burn rate, API credit telemetry, server expenses
# - Operations & Workflow: Tooling, automation scripts, communication pipelines
# - Documentation & Strategy: Obsidian company logs, executive briefs, meeting notes

# 3. Required / Anticipated Tool Primitives for Company & Project Management:
# - File & Folder Management: desktop_control (scaffolding dirs, workspace setup, consent-gated file moves)
# - Code & Issue Tracking: github_action (repo queries, issues, PRs, milestones)
# - Live Market Recon: web_search (competitor research, API docs, library updates)
# - Database Analytics: database_query (inspect customer DBs, analytics SQLite/Postgres)
# - Comms & Messaging: browser_action (WhatsApp Web client/investor notifications)
# - Task Scheduling & Reminders: cron / agenda triggers, desktop_notify
# - Executive Reporting: Generating Markdown briefs into Obsidian Vault (`/Obsidian Vault/Company/`)
