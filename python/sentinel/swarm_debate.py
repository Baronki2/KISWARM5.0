"""
KISWARM v2.1 — SWARM DEBATE ENGINE
Central Knowledge Manager — Conflict Resolution Module

When multiple intelligence sources contradict each other, the CKM
initiates a "Swarm Debate": multiple local Ollama models argue each
position and vote on the most probable truth before committing to memory.

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.1-EMS
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger("sentinel.debate")


@dataclass
class DebatePosition:
    model:      str
    stance:     str        # "A", "B", or "SYNTHESIS"
    argument:   str
    confidence: float = 0.0


@dataclass
class DebateVerdict:
    """Final verdict after swarm models debate conflicting intelligence."""
    winning_content:    str
    confidence:         float
    vote_tally:         dict = field(default_factory=dict)
    dissenting_models:  list = field(default_factory=list)
    synthesis:          Optional[str] = None
    timestamp:          str = field(default_factory=lambda: datetime.now().isoformat())


class SwarmDebateEngine:
    """
    Orchestrates a structured debate between local Ollama models
    to resolve conflicting intelligence from multiple sources.

    Protocol:
      1. Present both positions to each available model
      2. Each model votes and gives a 1-sentence argument
      3. Tally votes → winning position
      4. Optional: synthesis model creates a merged truth
    """

    OLLAMA_URL = "http://localhost:11434"

    def __init__(self):
        self.available_models: list[str] = []

    async def _get_available_models(self, session: aiohttp.ClientSession) -> list[str]:
        """Discover all locally available Ollama models."""
        try:
            async with session.get(
                f"{self.OLLAMA_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                data = await r.json()
                models = [m["name"] for m in data.get("models", [])]
                # Prioritize reasoning models for debate
                priority = ["deepseek", "qwen", "llama", "phi", "mistral", "gemma"]
                sorted_models = sorted(
                    models,
                    key=lambda m: next(
                        (i for i, p in enumerate(priority) if p in m.lower()), 99
                    )
                )
                return sorted_models[:5]  # Cap at 5 debaters
        except Exception as exc:
            logger.warning("Could not fetch models: %s", exc)
            return ["llama3:8b"]  # fallback

    async def _ask_model_to_vote(
        self,
        session: aiohttp.ClientSession,
        model: str,
        query: str,
        content_a: str,
        content_b: str,
    ) -> Optional[DebatePosition]:
        """Ask a single model to vote on which intelligence source is more accurate."""
        prompt = f"""You are a fact-checker in a swarm intelligence system.

QUERY: {query}

SOURCE A says:
{content_a[:500]}

SOURCE B says:
{content_b[:500]}

Which source provides more accurate, complete, and useful information about the query?
Reply with ONLY one of these formats:
VOTE: A | REASON: <one sentence why>
VOTE: B | REASON: <one sentence why>
VOTE: SYNTHESIS | REASON: <one sentence explaining how both are partially correct>"""

        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 80},
            }
            async with session.post(
                f"{self.OLLAMA_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                response = data.get("response", "").strip()

                # Parse vote
                import re
                vote_match   = re.search(r"VOTE:\s*(A|B|SYNTHESIS)", response, re.IGNORECASE)
                reason_match = re.search(r"REASON:\s*(.+)", response, re.IGNORECASE)

                if not vote_match:
                    return None

                stance   = vote_match.group(1).upper()
                argument = reason_match.group(1).strip() if reason_match else response[:100]

                return DebatePosition(
                    model=model,
                    stance=stance,
                    argument=argument,
                    confidence=0.8,
                )
        except Exception as exc:
            logger.warning("Model %s debate error: %s", model, exc)
            return None

    async def _synthesize(
        self,
        session: aiohttp.ClientSession,
        model: str,
        query: str,
        content_a: str,
        content_b: str,
    ) -> str:
        """Generate a synthesized truth merging the best of both sources."""
        prompt = f"""You are synthesizing intelligence from two sources for the query: {query}

SOURCE A: {content_a[:400]}

SOURCE B: {content_b[:400]}

Write a 2-3 sentence synthesis that combines the most accurate facts from both sources.
Be precise and factual."""
        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 200},
            }
            async with session.post(
                f"{self.OLLAMA_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                data = await r.json()
                return data.get("response", "").strip()
        except Exception:
            return ""

    async def debate(
        self,
        query: str,
        content_a: str,
        content_b: str,
        source_a_name: str = "Source A",
        source_b_name: str = "Source B",
    ) -> DebateVerdict:
        """
        Run a full swarm debate between two conflicting intelligence payloads.

        Returns a DebateVerdict with the winning content and confidence score.
        """
        logger.info("⚔ Swarm Debate initiated for query: '%s'", query)

        async with aiohttp.ClientSession() as session:
            models = await self._get_available_models(session)
            logger.info("Debate participants: %s", models)

            # Parallel vote collection
            vote_tasks = [
                self._ask_model_to_vote(session, model, query, content_a, content_b)
                for model in models
            ]
            vote_results = await asyncio.gather(*vote_tasks, return_exceptions=True)
            positions = [r for r in vote_results if isinstance(r, DebatePosition)]

            if not positions:
                logger.warning("No votes collected — defaulting to Source A")
                return DebateVerdict(
                    winning_content=content_a,
                    confidence=0.5,
                    vote_tally={"A": 1, "B": 0, "SYNTHESIS": 0},
                )

            # Tally votes
            tally = {"A": 0, "B": 0, "SYNTHESIS": 0}
            for pos in positions:
                tally[pos.stance] = tally.get(pos.stance, 0) + 1

            logger.info(
                "Vote tally — %s: %d | %s: %d | SYNTHESIS: %d",
                source_a_name, tally["A"],
                source_b_name, tally["B"],
                tally["SYNTHESIS"],
            )

            # Determine winner
            winning_stance = max(tally, key=tally.get)
            dissenting     = [p.model for p in positions if p.stance != winning_stance]

            # Generate synthesis if needed
            synthesis = None
            if winning_stance == "SYNTHESIS" or tally["SYNTHESIS"] >= 2:
                logger.info("Generating synthesis from best available model...")
                synthesis = await self._synthesize(
                    session, models[0], query, content_a, content_b
                )

            winning_content = (
                synthesis if (synthesis and winning_stance == "SYNTHESIS")
                else (content_a if winning_stance == "A" else content_b)
            )

            confidence = tally[winning_stance] / len(positions)

            verdict = DebateVerdict(
                winning_content=winning_content,
                confidence=confidence,
                vote_tally={
                    source_a_name: tally["A"],
                    source_b_name: tally["B"],
                    "SYNTHESIS":   tally["SYNTHESIS"],
                },
                dissenting_models=dissenting,
                synthesis=synthesis,
            )

            for pos in positions:
                logger.info("  [%s] voted %s — %s", pos.model, pos.stance, pos.argument)

            logger.info(
                "⚔ Debate resolved: %s wins (%.0f%% confidence)",
                winning_stance, confidence * 100
            )
            return verdict
