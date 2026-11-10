"""Specialized Domain Intelligence Agents for GHOST.

Conforms to ARCHITECTURE_TARGET.md §Layer 6:
All agents subclass AgentContract and adhere to strict verification criteria.
"""

from ghost.agents.base import AgentContract, VerificationEvidence

__all__ = ["AgentContract", "VerificationEvidence"]
