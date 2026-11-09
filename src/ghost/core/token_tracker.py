"""Comprehensive Task Token & Cost Tracker for Ghost Autonomous Agency Engine.
Accumulates multi-turn token consumption, model calls, tool calls, category breakdown,
and estimated costs across the entire lifecycle of an autonomous task.
"""
import os
import json
import logging
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("ghost.token_tracker")

REPO_DIR = os.path.abspath(os.environ.get("GHOST_PROJECT_ROOT") or os.path.join(os.path.dirname(__file__), "../../.."))
CONFIGS_DIR = os.path.join(REPO_DIR, "configs")
TOKEN_STATS_PATH = os.path.join(CONFIGS_DIR, "token_stats.json")

# Model pricing table per 1,000,000 tokens (USD)
MODEL_PRICING = {
    "antigravity/gemini-3.8-flash-low": {"prompt": 0.10, "completion": 0.40},
    "gemini-2.0-flash": {"prompt": 0.10, "completion": 0.40},
    "gemini-1.5-pro": {"prompt": 1.25, "completion": 5.00},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "claude-3-5-sonnet": {"prompt": 3.00, "completion": 15.00},
    "default": {"prompt": 0.15, "completion": 0.60}
}

@dataclass
class TaskTokenTracker:
    task_id: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    breakdown: Dict[str, int] = field(default_factory=lambda: {
        "memory": 0,
        "tool_definitions": 0,
        "tool_outputs": 0,
        "final_synthesis": 0,
    })
    cost_usd: float = 0.0
    model: str = "antigravity/gemini-3.8-flash-low"
    created_at: float = field(default_factory=time.time)

    def add_turn_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
        cached_tokens: int = 0
    ) -> None:
        """Accumulates usage from a single model call round, tracking cached tokens."""
        self.model_calls += 1
        self.prompt_tokens += max(0, prompt_tokens)
        self.cached_tokens += max(0, cached_tokens)
        self.completion_tokens += max(0, completion_tokens)
        calc_total = prompt_tokens + completion_tokens
        if total_tokens is not None and total_tokens > calc_total:
            self.total_tokens += total_tokens
        else:
            self.total_tokens += calc_total
        self.compute_cost()

    def record_tool_call(self) -> None:
        """Increment count of executed tools."""
        self.tool_calls += 1

    def record_category_tokens(self, category: str, token_count: int) -> None:
        """Attribution breakdown: memory, tool_definitions, tool_outputs, final_synthesis."""
        count = max(0, token_count)
        self.breakdown[category] = self.breakdown.get(category, 0) + count

    def compute_cost(self) -> float:
        """Compute estimated USD cost based on model pricing table, applying provider cache discount."""
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING["default"])
        uncached_prompt = max(0, self.prompt_tokens - self.cached_tokens)
        # Provider-side cached prompt tokens are billed at 25% or free depending on model
        cached_rate = pricing["prompt"] * 0.25
        prompt_cost = ((uncached_prompt * pricing["prompt"] + self.cached_tokens * cached_rate) / 1_000_000.0)
        completion_cost = (self.completion_tokens / 1_000_000.0) * pricing["completion"]
        self.cost_usd = round(prompt_cost + completion_cost, 6)
        return self.cost_usd

    def to_dict(self) -> Dict[str, Any]:
        """Convert tracker metrics to dictionary."""
        return {
            "task_id": self.task_id,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "breakdown": dict(self.breakdown),
            "cost_usd": self.cost_usd,
            "model": self.model,
            "created_at": self.created_at
        }

    def save_to_disk(self, filepath: Optional[str] = None) -> Dict[str, Any]:
        """Persist aggregated token stats to disk and return global summary."""
        target_path = filepath or TOKEN_STATS_PATH
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        stats = {
            "last": self.total_tokens,
            "last_cached": self.cached_tokens,
            "last_cost": self.cost_usd,
            "today": self.total_tokens,
            "today_cached": self.cached_tokens,
            "today_cost": self.cost_usd,
            "history": []
        }
        if os.path.exists(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    stats["today"] = existing.get("today", 0) + self.total_tokens
                    stats["today_cached"] = existing.get("today_cached", 0) + self.cached_tokens
                    stats["today_cost"] = round(existing.get("today_cost", 0.0) + self.cost_usd, 6)
                    stats["history"] = existing.get("history", [])
            except Exception as e:
                logger.warning(f"Could not read existing token stats: {e}")

        stats["history"].append(self.to_dict())
        # Keep rolling 100 tasks in history
        if len(stats["history"]) > 100:
            stats["history"] = stats["history"][-100:]

        try:
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
        except Exception as e:
            logger.error(f"Failed writing token stats to {target_path}: {e}")

        return stats
