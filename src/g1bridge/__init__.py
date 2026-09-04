"""g1bridge — Even Realities G1 <-> Claude agent bridge."""

from .agent import GlassesAgent
from .agents import AgentSpec, load_agents
from .ble import G1Glasses
from .hud import HudText
from .paginate import paginate, wrap_text
from .session import HubSession
from .sim import SimGlasses

__all__ = [
    "AgentSpec",
    "G1Glasses",
    "GlassesAgent",
    "HubSession",
    "HudText",
    "SimGlasses",
    "load_agents",
    "paginate",
    "wrap_text",
]
