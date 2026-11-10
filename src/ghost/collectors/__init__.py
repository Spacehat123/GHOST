"""GHOST Environment Perception Collectors.

Collectors passively sample system, hardware, git repository, desktop, and network
events and publish typed Events to the EventBus.
"""

from ghost.collectors.system_collector import SystemCollector
from ghost.collectors.git_collector import GitCollector

__all__ = ["SystemCollector", "GitCollector"]
