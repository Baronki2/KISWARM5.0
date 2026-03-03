"""
KISWARM v2.1 — SENTINEL BRIDGE
Autonomous Knowledge Extraction (AKE) Engine

Detects knowledge gaps in the local swarm, deploys multi-source research
scouts, cross-verifies intelligence, and injects distilled knowledge into
the Qdrant vector database — fully autonomous, no human intervention required.

Architecture:
  Gap Detection → Scout Deployment → Multi-Source Fetch
  → Logic Synthesis → Cross-Verification → Swarm Memory Injection

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.1-EMS
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from pathlib import Path

import aiohttp

# ── Optional dependencies (graceful degradation) ─────────────────────────────
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────
KISWARM_HOME  = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR   = os.path.join(KISWARM_HOME, "KISWARM")
QDRANT_PATH   = os.path.join(KISWARM_DIR, "qdrant_data")
LOG_DIR       = os.path.join(KISWARM_HOME, "logs")
SENTINEL_LOG  = os.path.join(LOG_DIR, "sentinel_bridge.log")
CACHE_FILE    = os.path.join(KISWARM_DIR, "sentinel_cache.json")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(QDRANT_DIR := os.path.join(KISWARM_DIR, "qdrant_data"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SENTINEL] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(SENTINEL_LOG),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sentinel")


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class IntelligencePacket:
    """A single piece of distilled knowledge from one source."""
    source:       str
    url:          str
    content:      str
    confidence:   float = 0.0
    word_count:   int   = 0
    timestamp:    str   = field(default_factory=lambda: datetime.now().isoformat())
    tags:         list  = field(default_factory=list)

@dataclass
class SwarmKnowledge:
    """Verified, cross-triangulated knowledge ready for Qdrant injection."""
    query:        str
    content:      str
    sources:      list
    confidence:   float
    classification: str = "SENTINEL-VERIFIED"
    timestamp:    str   = field(default_factory=lambda: datetime.now().isoformat())
    hash_id:      str   = ""

    def __post_init__(self):
        self.hash_id = hashlib.sha256(
            f"{self.query}{self.content[:200]}".encode()
        ).hexdigest()[:16]


# ── Scout Sources ─────────────────────────────────────────────────────────────

class WikipediaScout:
    """Extracts encyclopedic knowledge from Wikipedia REST API."""

    BASE_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
    SEARCH_URL = "https://en.wikipedia.org/w/api.php"

    async def fetch(self, session: aiohttp.ClientSession, query: str) -> Optional[IntelligencePacket]:
        try:
            # Search first
            params = {
                "action": "query", "list": "search",
                "srsearch": query, "srlimit": 1,
                "format": "json", "utf8": 1,
            }
            async with session.get(self.SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                results = data.get("query", {}).get("search", [])
                if not results:
                    return None
                title = results[0]["title"]

            # Fetch summary
            safe_title = title.replace(" ", "_")
            async with session.get(f"{self.BASE_URL}{safe_title}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                page = await r.json()
                content = page.get("extract", "")
                if not content:
                    return None
                return IntelligencePacket(
                    source="Wikipedia",
                    url=page.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    content=content,
                    word_count=len(content.split()),
                    confidence=0.75,
                    tags=["encyclopedic", "verified"],
                )
        except Exception as exc:
            logger.warning("Wikipedia scout error: %s", exc)
            return None


class ArxivScout:
    """Extracts scientific/research intelligence from ArXiv API."""

    BASE_URL = "http://export.arxiv.org/api/query"

    async def fetch(self, session: aiohttp.ClientSession, query: str) -> Optional[IntelligencePacket]:
        try:
            params = {
                "search_query": f"all:{query}",
                "start": 0, "max_results": 3,
                "sortBy": "relevance", "sortOrder": "descending",
            }
            async with session.get(self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                text = await r.text()

            # Parse Atom XML (no lxml needed)
            entries = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)
            if not entries:
                return None

            summaries = []
            for entry in entries[:2]:
                title   = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
                summary = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
                link    = re.search(r'href="(https://arxiv\.org/abs/[^"]+)"', entry)
                if title and summary:
                    summaries.append(
                        f"[{title.group(1).strip()}] {summary.group(1).strip()}"
                    )

            if not summaries:
                return None

            content = "\n\n".join(summaries)
            return IntelligencePacket(
                source="ArXiv",
                url=link.group(1) if link else "https://arxiv.org",
                content=content,
                word_count=len(content.split()),
                confidence=0.85,
                tags=["research", "academic", "peer-reviewed"],
            )
        except Exception as exc:
            logger.warning("ArXiv scout error: %s", exc)
            return None


class DuckDuckGoScout:
    """Extracts web intelligence via DuckDuckGo Instant Answer API."""

    BASE_URL = "https://api.duckduckgo.com/"

    async def fetch(self, session: aiohttp.ClientSession, query: str) -> Optional[IntelligencePacket]:
        try:
            params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
            async with session.get(self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)

            content_parts = []
            abstract = data.get("AbstractText", "").strip()
            if abstract:
                content_parts.append(abstract)

            for topic in data.get("RelatedTopics", [])[:3]:
                if isinstance(topic, dict) and topic.get("Text"):
                    content_parts.append(topic["Text"])

            if not content_parts:
                return None

            content = "\n\n".join(content_parts)
            return IntelligencePacket(
                source="DuckDuckGo",
                url=data.get("AbstractURL", "https://duckduckgo.com"),
                content=content,
                word_count=len(content.split()),
                confidence=0.65,
                tags=["web", "general"],
            )
        except Exception as exc:
            logger.warning("DuckDuckGo scout error: %s", exc)
            return None


class OllamaScout:
    """Uses the local Ollama swarm itself as a knowledge scout for synthesis."""

    BASE_URL = "http://localhost:11434/api/generate"

    def __init__(self, model: str = "llama3:8b"):
        self.model = model

    async def fetch(self, session: aiohttp.ClientSession, query: str) -> Optional[IntelligencePacket]:
        try:
            payload = {
                "model": self.model,
                "prompt": (
                    f"Provide a concise, factual summary of everything you know about: {query}\n"
                    "Focus on key facts, definitions, and technical details. Be precise."
                ),
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 500},
            }
            async with session.post(
                self.BASE_URL, json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                content = data.get("response", "").strip()
                if not content:
                    return None
                return IntelligencePacket(
                    source=f"Ollama/{self.model}",
                    url="http://localhost:11434",
                    content=content,
                    word_count=len(content.split()),
                    confidence=0.70,
                    tags=["local-llm", "synthesis"],
                )
        except Exception as exc:
            logger.warning("Ollama scout error: %s", exc)
            return None


# ── Logic Synthesizer ─────────────────────────────────────────────────────────

class LogicSynthesizer:
    """
    Cross-verifies intelligence from multiple sources and distills
    the highest-confidence, non-contradictory knowledge.
    """

    def distill(self, packets: list[IntelligencePacket], query: str) -> str:
        """Remove noise, deduplicate, and synthesize a clean knowledge payload."""
        if not packets:
            return ""

        # Sort by confidence descending
        ranked = sorted(packets, key=lambda p: p.confidence, reverse=True)

        # Deduplicate by content similarity (simple hash-based)
        seen_hashes = set()
        unique = []
        for pkt in ranked:
            h = hashlib.md5(pkt.content[:200].encode()).hexdigest()
            if h not in seen_hashes:
                seen_hashes.add(h)
                unique.append(pkt)

        # Build distilled content
        sections = []
        for pkt in unique:
            # Clean whitespace and HTML artifacts
            clean = re.sub(r"\s+", " ", pkt.content).strip()
            clean = re.sub(r"<[^>]+>", "", clean)
            if len(clean) > 100:
                sections.append(
                    f"[SOURCE: {pkt.source} | Confidence: {pkt.confidence:.0%}]\n{clean}"
                )

        return "\n\n---\n\n".join(sections)

    def compute_confidence(self, packets: list[IntelligencePacket]) -> float:
        """Compute aggregate confidence from multiple sources."""
        if not packets:
            return 0.0
        # Weighted average — more sources = higher confidence
        weights = [p.confidence for p in packets]
        base = sum(weights) / len(weights)
        # Bonus for multi-source corroboration
        source_bonus = min(0.15, len(packets) * 0.05)
        return min(1.0, base + source_bonus)

    def detect_conflicts(self, packets: list[IntelligencePacket]) -> list[str]:
        """Flag potential contradictions between sources for Swarm Debate."""
        # Simple heuristic: flag if sources differ significantly in length
        conflicts = []
        if len(packets) >= 2:
            lengths = [p.word_count for p in packets]
            if max(lengths) > min(lengths) * 5 and min(lengths) > 0:
                conflicts.append(
                    f"Significant content disparity detected between sources "
                    f"({min(lengths)}–{max(lengths)} words). Swarm debate recommended."
                )
        return conflicts


# ── Central Knowledge Manager ─────────────────────────────────────────────────

class CentralKnowledgeManager:
    """
    Evaluates local swarm confidence for a query.
    Returns True if a knowledge gap is detected (confidence < threshold).
    """

    def __init__(self, threshold: float = 0.85):
        self.threshold = threshold
        self._cache: dict[str, float] = self._load_cache()

    def _load_cache(self) -> dict:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self._cache, f, indent=2)
        except OSError as exc:
            logger.warning("Cache save failed: %s", exc)

    async def estimate_local_confidence(
        self, session: aiohttp.ClientSession, query: str
    ) -> float:
        """Ask the local Ollama model how confident it is about a query."""
        cache_key = hashlib.md5(query.encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            payload = {
                "model": "llama3:8b",
                "prompt": (
                    f"On a scale from 0.0 to 1.0, how confident are you in giving "
                    f"a complete and accurate answer to: '{query}'\n"
                    "Reply with ONLY a decimal number between 0.0 and 1.0. Nothing else."
                ),
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 10},
            }
            async with session.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    text = data.get("response", "0.5").strip()
                    match = re.search(r"(\d+\.?\d*)", text)
                    if match:
                        score = min(1.0, max(0.0, float(match.group(1))))
                        self._cache[cache_key] = score
                        self._save_cache()
                        return score
        except Exception as exc:
            logger.warning("Confidence estimation failed: %s", exc)

        # Default: assume medium confidence
        return 0.5

    def gap_detected(self, confidence: float) -> bool:
        return confidence < self.threshold


# ── Swarm Memory Injector ─────────────────────────────────────────────────────

class SwarmMemoryInjector:
    """Vectorizes and injects distilled knowledge into Qdrant."""

    def __init__(self):
        self.client = None
        self.encoder = None
        self._init_qdrant()
        self._init_encoder()

    def _init_qdrant(self):
        if not QDRANT_AVAILABLE:
            logger.warning("qdrant-client not installed — memory injection disabled")
            return
        try:
            self.client = QdrantClient(path=QDRANT_PATH)
            # Ensure sentinel collection exists
            try:
                self.client.get_collection("sentinel_knowledge")
            except Exception:
                self.client.create_collection(
                    "sentinel_knowledge",
                    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
                )
                logger.info("Created 'sentinel_knowledge' collection in Qdrant")
        except Exception as exc:
            logger.error("Qdrant init failed: %s", exc)
            self.client = None

    def _init_encoder(self):
        if not EMBEDDINGS_AVAILABLE:
            logger.warning("sentence-transformers not installed — using hash embeddings")
            return
        try:
            self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Embedding model loaded: all-MiniLM-L6-v2")
        except Exception as exc:
            logger.warning("Embedding model load failed: %s", exc)

    def _embed(self, text: str) -> list[float]:
        """Generate a 384-dim embedding vector."""
        if self.encoder:
            return self.encoder.encode(text[:512]).tolist()
        # Fallback: deterministic hash-based pseudo-embedding
        h = hashlib.sha256(text.encode()).digest()
        base = [((b / 255.0) * 2 - 1) for b in h]
        # Pad/repeat to 384 dimensions
        return (base * 12)[:384]

    def inject(self, knowledge: SwarmKnowledge) -> bool:
        """Store verified intelligence in Qdrant."""
        if not self.client:
            logger.warning("Qdrant unavailable — logging knowledge to file instead")
            self._fallback_log(knowledge)
            return False
        try:
            vector = self._embed(f"{knowledge.query} {knowledge.content}")
            point = PointStruct(
                id=abs(hash(knowledge.hash_id)) % (2**63),
                vector=vector,
                payload={
                    "query":          knowledge.query,
                    "content":        knowledge.content[:4000],
                    "sources":        knowledge.sources,
                    "confidence":     knowledge.confidence,
                    "classification": knowledge.classification,
                    "timestamp":      knowledge.timestamp,
                    "hash_id":        knowledge.hash_id,
                },
            )
            self.client.upsert("sentinel_knowledge", points=[point])
            logger.info(
                "✓ Injected into swarm memory | hash=%s | confidence=%.0f%%",
                knowledge.hash_id, knowledge.confidence * 100,
            )
            return True
        except Exception as exc:
            logger.error("Memory injection failed: %s", exc)
            return False

    def _fallback_log(self, knowledge: SwarmKnowledge):
        """Write knowledge to JSON log if Qdrant unavailable."""
        log_path = os.path.join(KISWARM_DIR, "sentinel_knowledge_log.jsonl")
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps({
                    "query": knowledge.query,
                    "content": knowledge.content[:2000],
                    "confidence": knowledge.confidence,
                    "timestamp": knowledge.timestamp,
                }) + "\n")
        except OSError as exc:
            logger.error("Fallback log write failed: %s", exc)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Search existing swarm knowledge for a query."""
        if not self.client:
            return []
        try:
            vector = self._embed(query)
            results = self.client.search(
                "sentinel_knowledge", query_vector=vector, limit=top_k
            )
            return [r.payload for r in results]
        except Exception as exc:
            logger.warning("Memory search failed: %s", exc)
            return []


# ── SENTINEL BRIDGE — Main Orchestrator ──────────────────────────────────────

class SentinelBridge:
    """
    KISWARM v2.1 Sentinel-Class Intelligence Reconnaissance Unit.

    Full pipeline:
      1. Detect knowledge gap via CKM confidence scoring
      2. Deploy parallel scouts (Wikipedia, ArXiv, DuckDuckGo, Ollama)
      3. Distill and cross-verify via LogicSynthesizer
      4. Inject verified SwarmKnowledge into Qdrant
    """

    def __init__(self, confidence_threshold: float = 0.85):
        self.ckm      = CentralKnowledgeManager(threshold=confidence_threshold)
        self.synth    = LogicSynthesizer()
        self.memory   = SwarmMemoryInjector()
        self.scouts   = [
            WikipediaScout(),
            ArxivScout(),
            DuckDuckGoScout(),
            OllamaScout(),
        ]
        logger.info("SentinelBridge initialized | threshold=%.0f%%", confidence_threshold * 100)

    async def run(self, query: str, force: bool = False) -> dict[str, Any]:
        """
        Full AKE pipeline for a query.

        Args:
            query: The knowledge query to research.
            force: Skip confidence check and always extract.

        Returns:
            Status dict with confidence, sources found, and injection result.
        """
        logger.info("═" * 60)
        logger.info("SENTINEL ACTIVATED | query='%s'", query)

        async with aiohttp.ClientSession(
            headers={"User-Agent": "KISWARM/2.1 Research Bot (+https://github.com/Baronki2/KISWARM)"}
        ) as session:

            # ── Step 1: Gap Detection ────────────────────────────────────────
            if not force:
                local_conf = await self.ckm.estimate_local_confidence(session, query)
                logger.info("Local swarm confidence: %.0f%% (threshold: %.0f%%)",
                            local_conf * 100, self.ckm.threshold * 100)

                if not self.ckm.gap_detected(local_conf):
                    logger.info("✓ No knowledge gap detected — swarm is confident")
                    return {"status": "no_gap", "confidence": local_conf, "query": query}

                logger.info("⚡ Knowledge gap detected — deploying scouts")
            else:
                logger.info("⚡ Force mode — deploying scouts unconditionally")
                local_conf = 0.0

            # ── Step 2: Parallel Scout Deployment ───────────────────────────
            logger.info("Deploying %d scouts in parallel...", len(self.scouts))
            tasks = [scout.fetch(session, query) for scout in self.scouts]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            packets: list[IntelligencePacket] = [
                r for r in results
                if isinstance(r, IntelligencePacket) and r is not None
            ]
            logger.info("Scout returns: %d/%d successful", len(packets), len(self.scouts))

            if not packets:
                logger.warning("All scouts returned empty — knowledge gap unresolved")
                return {"status": "scouts_empty", "confidence": 0.0, "query": query}

            # ── Step 3: Logic Synthesis & Cross-Verification ─────────────────
            conflicts = self.synth.detect_conflicts(packets)
            if conflicts:
                for c in conflicts:
                    logger.warning("CONFLICT: %s", c)

            distilled_content = self.synth.distill(packets, query)
            aggregate_conf    = self.synth.compute_confidence(packets)
            sources           = [{"source": p.source, "url": p.url, "confidence": p.confidence}
                                  for p in packets]

            logger.info("Distilled intelligence: %d chars | aggregate confidence: %.0f%%",
                        len(distilled_content), aggregate_conf * 100)

            # ── Step 4: Swarm Memory Injection ───────────────────────────────
            knowledge = SwarmKnowledge(
                query=query,
                content=distilled_content,
                sources=sources,
                confidence=aggregate_conf,
                classification="SENTINEL-VERIFIED-EMS",
            )
            injected = self.memory.inject(knowledge)

            logger.info("SENTINEL COMPLETE | hash=%s | injected=%s", knowledge.hash_id, injected)
            logger.info("═" * 60)

            return {
                "status":     "success",
                "query":      query,
                "hash_id":    knowledge.hash_id,
                "confidence": aggregate_conf,
                "sources":    len(packets),
                "source_list": sources,
                "conflicts":  conflicts,
                "injected":   injected,
                "chars":      len(distilled_content),
                "timestamp":  knowledge.timestamp,
            }

    async def query_memory(self, query: str, top_k: int = 5) -> list[dict]:
        """Search existing swarm knowledge without triggering extraction."""
        return self.memory.search(query, top_k=top_k)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="KISWARM v2.1 Sentinel Bridge — Autonomous Knowledge Extraction"
    )
    parser.add_argument("query", nargs="?", default="quantum computing overview",
                        help="Knowledge query to research")
    parser.add_argument("--force",    action="store_true", help="Skip gap detection, always extract")
    parser.add_argument("--search",   action="store_true", help="Search existing swarm memory instead")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="Confidence threshold for gap detection (default: 0.85)")
    args = parser.parse_args()

    bridge = SentinelBridge(confidence_threshold=args.threshold)

    if args.search:
        print(f"\n🔍 Searching swarm memory for: '{args.query}'")
        results = await bridge.query_memory(args.query)
        if results:
            for i, r in enumerate(results, 1):
                print(f"\n[{i}] Source: {r.get('sources', 'unknown')}")
                print(f"    Confidence: {r.get('confidence', 0):.0%}")
                print(f"    Content: {r.get('content', '')[:300]}...")
        else:
            print("No matching knowledge in swarm memory.")
    else:
        result = await bridge.run(args.query, force=args.force)
        print(f"\n{'═'*60}")
        print(f"  SENTINEL BRIDGE — EXTRACTION REPORT")
        print(f"{'═'*60}")
        print(f"  Query:      {result['query']}")
        print(f"  Status:     {result['status']}")
        print(f"  Confidence: {result.get('confidence', 0):.0%}")
        print(f"  Sources:    {result.get('sources', 0)}")
        print(f"  Injected:   {result.get('injected', False)}")
        if result.get("conflicts"):
            print(f"  ⚠ Conflicts: {len(result['conflicts'])}")
        print(f"{'═'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
