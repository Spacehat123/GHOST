"""Specialized Domain Intelligence Agents covering all 7 operational domains."""

from ghost.agents.base import AgentContract, VerificationEvidence
from ghost.agents.system_agent import SystemIntelligenceAgent
from ghost.agents.dev_agent import DevelopmentIntelligenceAgent
from ghost.agents.research_agent import ResearchIntelligenceAgent
from ghost.agents.business_agent import BusinessIntelligenceAgent
from ghost.agents.personal_agent import PersonalIntelligenceAgent
from ghost.agents.physical_agent import PhysicalIntelligenceAgent
from ghost.agents.creative_agent import CreativeIntelligenceAgent
from ghost.agents.market_agent import MarketIntelligenceAgent

__all__ = [
    "AgentContract",
    "VerificationEvidence",
    "SystemIntelligenceAgent",
    "DevelopmentIntelligenceAgent",
    "ResearchIntelligenceAgent",
    "BusinessIntelligenceAgent",
    "PersonalIntelligenceAgent",
    "PhysicalIntelligenceAgent",
    "CreativeIntelligenceAgent",
    "MarketIntelligenceAgent",
    "get_available_agents",
]


def get_available_agents():
    """Returns instantiated list of all specialized domain agents."""
    return [
        SystemIntelligenceAgent(),
        DevelopmentIntelligenceAgent(),
        ResearchIntelligenceAgent(),
        BusinessIntelligenceAgent(),
        PersonalIntelligenceAgent(),
        PhysicalIntelligenceAgent(),
        CreativeIntelligenceAgent(),
        MarketIntelligenceAgent(),
    ]
