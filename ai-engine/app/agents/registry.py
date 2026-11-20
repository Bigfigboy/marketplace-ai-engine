"""Agent registry.

Agents are loaded from trusted, in-repository modules listed in ``AGENT_MODULES``. Each
module exposes ``AGENT`` (a ``BaseAgent`` instance). Loading third-party / uploaded code
is intentionally not supported: marketplace developers' agents must first go through
code review and be added to this repository (or run as separate services - see
docs/ARCHITECTURE.md, "Scaling to hundreds of agents").
"""

from __future__ import annotations

import importlib
import re

from app.agents.base import BaseAgent
from app.core.errors import NotFoundError

_TRUSTED_PREFIX = "app.agents."
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> BaseAgent:
        if not isinstance(agent, BaseAgent):
            raise TypeError("Only BaseAgent instances can be registered")
        if not _SLUG_RE.match(agent.slug):
            raise ValueError(f"Invalid agent slug: {agent.slug!r}")
        if agent.slug in self._agents:
            raise ValueError(f"Agent slug already registered: {agent.slug}")
        agent.info()  # fail fast if schemas are broken
        self._agents[agent.slug] = agent
        return agent

    def load_modules(self, modules: list[str]) -> None:
        for module_name in modules:
            if not module_name.startswith(_TRUSTED_PREFIX):
                raise ValueError(f"Refusing to load agent from untrusted module: {module_name}")
            module = importlib.import_module(module_name)
            agent = getattr(module, "AGENT", None)
            if agent is None:
                raise ValueError(f"Module {module_name} does not define AGENT")
            self.register(agent)

    def get(self, slug: str) -> BaseAgent:
        agent = self._agents.get(slug)
        if agent is None:
            raise NotFoundError("Agent not found", code="agent_not_found")
        return agent

    def all(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def slugs(self) -> list[str]:
        return list(self._agents)
