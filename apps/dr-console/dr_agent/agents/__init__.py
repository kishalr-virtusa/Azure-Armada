"""Agents: each module exposes `run(...) -> AgentResult` with a deterministic fallback."""
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"

COMMON_RULES = """
Rules:
- You are part of the "Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation" system on Azure.
- Use ONLY resource names, regions and facts present in the input. Never invent resources.
- Region A = Central India (primary). Region B = Southeast Asia (DR). Traffic Manager routes users; Azure SQL uses a failover group.
- Be concise and specific. Plain English, no marketing language.
"""


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8") + "\n" + COMMON_RULES
