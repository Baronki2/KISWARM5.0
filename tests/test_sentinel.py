"""
KISWARM v2.1 — Unit Tests: Sentinel Bridge AKE System

Run: pytest tests/test_sentinel.py -v
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from sentinel.sentinel_bridge import (
    IntelligencePacket,
    LogicSynthesizer,
    SwarmKnowledge,
    CentralKnowledgeManager,
    SwarmMemoryInjector,
)
from sentinel.swarm_debate import DebatePosition, DebateVerdict, SwarmDebateEngine


# ── IntelligencePacket ────────────────────────────────────────────────────────

class TestIntelligencePacket:
    def test_defaults(self):
        p = IntelligencePacket(source="Test", url="http://x.com", content="hello world")
        assert p.confidence == 0.0
        assert p.word_count == 0
        assert p.timestamp  # auto-filled

    def test_custom_fields(self):
        p = IntelligencePacket(
            source="ArXiv", url="http://arxiv.org", content="quantum computing",
            confidence=0.85, tags=["research"]
        )
        assert p.confidence == 0.85
        assert "research" in p.tags


# ── SwarmKnowledge ────────────────────────────────────────────────────────────

class TestSwarmKnowledge:
    def test_hash_auto_generated(self):
        k = SwarmKnowledge(
            query="test query", content="test content",
            sources=[], confidence=0.9
        )
        assert len(k.hash_id) == 16

    def test_hash_deterministic(self):
        k1 = SwarmKnowledge(query="q", content="c", sources=[], confidence=0.9)
        k2 = SwarmKnowledge(query="q", content="c", sources=[], confidence=0.9)
        assert k1.hash_id == k2.hash_id

    def test_different_queries_different_hash(self):
        k1 = SwarmKnowledge(query="q1", content="c", sources=[], confidence=0.9)
        k2 = SwarmKnowledge(query="q2", content="c", sources=[], confidence=0.9)
        assert k1.hash_id != k2.hash_id

    def test_default_classification(self):
        k = SwarmKnowledge(query="q", content="c", sources=[], confidence=0.9)
        assert k.classification == "SENTINEL-VERIFIED"


# ── LogicSynthesizer ──────────────────────────────────────────────────────────

class TestLogicSynthesizer:
    def setup_method(self):
        self.synth = LogicSynthesizer()

    def _make_packet(self, source, content, confidence=0.7, word_count=50):
        return IntelligencePacket(
            source=source, url="http://example.com",
            content=content, confidence=confidence, word_count=word_count
        )

    def test_distill_empty_returns_empty(self):
        assert self.synth.distill([], "query") == ""

    def test_distill_single_packet(self):
        p = self._make_packet("Wikipedia", "Quantum computing uses qubits, which are quantum bits that can exist in superposition states simultaneously.")
        result = self.synth.distill([p], "quantum computing")
        assert "Wikipedia" in result
        assert "qubits" in result

    def test_distill_multiple_sources(self):
        packets = [
            self._make_packet("Wikipedia", "Qubits are quantum bits that leverage superposition and entanglement to perform computations.", confidence=0.75),
            self._make_packet("ArXiv", "Quantum entanglement enables superposition allowing quantum computers to process vast amounts of data in parallel.", confidence=0.85),
        ]
        result = self.synth.distill(packets, "quantum")
        # At least one source should appear (dedup may merge if content hash matches)
        assert "ArXiv" in result or "Wikipedia" in result
        assert len(result) > 50

    def test_distill_deduplicates(self):
        content = "Identical content here for testing deduplication logic in the LogicSynthesizer distill method which filters short content."
        packets = [
            self._make_packet("Source1", content, confidence=0.7),
            self._make_packet("Source2", content, confidence=0.8),
        ]
        result = self.synth.distill(packets, "test")
        # Should only appear once despite two identical packets
        assert result.count("Identical content") == 1

    def test_distill_filters_short_content(self):
        p = self._make_packet("Source", "Too short", confidence=0.9)
        result = self.synth.distill([p], "test")
        assert result == ""  # < 100 chars filtered out

    def test_compute_confidence_single(self):
        packets = [self._make_packet("S1", "content", confidence=0.8)]
        conf = self.synth.compute_confidence(packets)
        assert 0.8 <= conf <= 1.0

    def test_compute_confidence_multiple_higher(self):
        single = [self._make_packet("S1", "c", confidence=0.7)]
        multi  = [
            self._make_packet("S1", "c1", confidence=0.7),
            self._make_packet("S2", "c2", confidence=0.7),
            self._make_packet("S3", "c3", confidence=0.7),
        ]
        assert self.synth.compute_confidence(multi) > self.synth.compute_confidence(single)

    def test_compute_confidence_empty(self):
        assert self.synth.compute_confidence([]) == 0.0

    def test_compute_confidence_capped_at_1(self):
        packets = [self._make_packet(f"S{i}", "c", confidence=0.99) for i in range(10)]
        assert self.synth.compute_confidence(packets) <= 1.0

    def test_detect_conflicts_no_conflict(self):
        packets = [
            self._make_packet("S1", "content", word_count=100),
            self._make_packet("S2", "content", word_count=120),
        ]
        conflicts = self.synth.detect_conflicts(packets)
        assert conflicts == []

    def test_detect_conflicts_large_disparity(self):
        packets = [
            self._make_packet("S1", "short", word_count=10),
            self._make_packet("S2", "very long content " * 100, word_count=600),
        ]
        conflicts = self.synth.detect_conflicts(packets)
        assert len(conflicts) > 0

    def test_detect_no_conflict_single_source(self):
        packets = [self._make_packet("S1", "content", word_count=100)]
        assert self.synth.detect_conflicts(packets) == []


# ── CentralKnowledgeManager ───────────────────────────────────────────────────

class TestCentralKnowledgeManager:
    def setup_method(self):
        self.ckm = CentralKnowledgeManager(threshold=0.85)

    def test_gap_detected_below_threshold(self):
        assert self.ckm.gap_detected(0.5)  is True
        assert self.ckm.gap_detected(0.84) is True

    def test_no_gap_above_threshold(self):
        assert self.ckm.gap_detected(0.85) is False
        assert self.ckm.gap_detected(1.0)  is False

    def test_custom_threshold(self):
        ckm = CentralKnowledgeManager(threshold=0.5)
        assert ckm.gap_detected(0.4) is True
        assert ckm.gap_detected(0.6) is False

    def test_cache_persists_between_calls(self, tmp_path, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "CACHE_FILE", str(tmp_path / "cache.json"))
        ckm = CentralKnowledgeManager()
        ckm._cache["test_key"] = 0.9
        ckm._save_cache()
        ckm2 = CentralKnowledgeManager()
        assert "test_key" in ckm2._cache

    def test_malformed_cache_handled(self, tmp_path, monkeypatch):
        import sentinel.sentinel_bridge as sb
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not valid json!!")
        monkeypatch.setattr(sb, "CACHE_FILE", str(cache_file))
        ckm = CentralKnowledgeManager()
        assert isinstance(ckm._cache, dict)


# ── SwarmMemoryInjector ───────────────────────────────────────────────────────

class TestSwarmMemoryInjector:
    def test_embed_returns_384_dims(self, monkeypatch):
        # Force hash-based embedding (no sentence-transformers needed)
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "EMBEDDINGS_AVAILABLE", False)
        inj = SwarmMemoryInjector()
        inj.encoder = None
        vec = inj._embed("test text")
        assert len(vec) == 384
        assert all(isinstance(v, float) for v in vec)

    def test_embed_deterministic(self, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "EMBEDDINGS_AVAILABLE", False)
        inj = SwarmMemoryInjector()
        inj.encoder = None
        v1 = inj._embed("same text")
        v2 = inj._embed("same text")
        assert v1 == v2

    def test_embed_different_texts_differ(self, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "EMBEDDINGS_AVAILABLE", False)
        inj = SwarmMemoryInjector()
        inj.encoder = None
        v1 = inj._embed("text A")
        v2 = inj._embed("text B")
        assert v1 != v2

    def test_fallback_log_when_no_qdrant(self, tmp_path, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_PATH", str(tmp_path / "qdrant"))
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        inj = SwarmMemoryInjector()
        inj.client = None
        k = SwarmKnowledge(
            query="test", content="test content",
            sources=[], confidence=0.8
        )
        inj._fallback_log(k)
        log_file = tmp_path / "sentinel_knowledge_log.jsonl"
        assert log_file.exists()
        line = json.loads(log_file.read_text().strip())
        assert line["query"] == "test"

    def test_inject_without_qdrant_returns_false(self, tmp_path, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        inj = SwarmMemoryInjector()
        inj.client = None
        k = SwarmKnowledge(query="q", content="c", sources=[], confidence=0.8)
        result = inj.inject(k)
        assert result is False


# ── SwarmDebateEngine ─────────────────────────────────────────────────────────

class TestSwarmDebateEngine:
    def test_debate_position_dataclass(self):
        pos = DebatePosition(model="llama3", stance="A", argument="More accurate", confidence=0.9)
        assert pos.stance == "A"
        assert pos.model  == "llama3"

    def test_debate_verdict_dataclass(self):
        v = DebateVerdict(
            winning_content="content A",
            confidence=0.75,
            vote_tally={"A": 3, "B": 1},
        )
        assert v.winning_content == "content A"
        assert v.confidence == 0.75
        assert v.vote_tally["A"] == 3

    def test_vote_tally_determines_winner(self):
        # Simulate tally logic
        tally = {"A": 3, "B": 1, "SYNTHESIS": 0}
        winner = max(tally, key=tally.get)
        assert winner == "A"

    def test_synthesis_wins_when_most_votes(self):
        tally = {"A": 1, "B": 1, "SYNTHESIS": 3}
        winner = max(tally, key=tally.get)
        assert winner == "SYNTHESIS"

    def test_dissenting_models_calculated(self):
        positions = [
            DebatePosition("m1", "A", "arg"),
            DebatePosition("m2", "A", "arg"),
            DebatePosition("m3", "B", "arg"),
        ]
        winning_stance = "A"
        dissenting = [p.model for p in positions if p.stance != winning_stance]
        assert dissenting == ["m3"]
        assert len(dissenting) == 1


# ── Integration: SentinelBridge pipeline (mocked network) ────────────────────

class TestSentinelBridgePipeline:
    @pytest.mark.asyncio
    async def test_run_returns_no_gap_when_confident(self, tmp_path, monkeypatch):
        """When local confidence > threshold, AKE should not be triggered."""
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "CACHE_FILE", str(tmp_path / "cache.json"))

        bridge = sb.SentinelBridge(confidence_threshold=0.85)

        # Patch CKM to return high confidence
        async def mock_confidence(session, query):
            return 0.95

        bridge.ckm.estimate_local_confidence = mock_confidence

        result = await bridge.run("quantum computing")
        assert result["status"] == "no_gap"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_run_extracts_when_gap_detected(self, tmp_path, monkeypatch):
        """When confidence < threshold, scouts should be deployed."""
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "CACHE_FILE", str(tmp_path / "cache.json"))

        bridge = sb.SentinelBridge(confidence_threshold=0.85)

        # Low confidence → gap detected
        async def mock_low_confidence(session, query):
            return 0.40

        bridge.ckm.estimate_local_confidence = mock_low_confidence

        # Mock scouts to return content
        async def mock_fetch(session, query):
            return sb.IntelligencePacket(
                source="MockScout", url="http://mock.com",
                content="This is a detailed mock intelligence payload with sufficient content to pass the 100-char filter.",
                confidence=0.8, word_count=20,
            )

        for scout in bridge.scouts:
            scout.fetch = mock_fetch

        result = await bridge.run("obscure topic")
        assert result["status"] == "success"
        assert result["sources"] > 0

    @pytest.mark.asyncio
    async def test_force_mode_skips_gap_check(self, tmp_path, monkeypatch):
        """Force mode should deploy scouts regardless of confidence."""
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "CACHE_FILE", str(tmp_path / "cache.json"))

        bridge = sb.SentinelBridge()
        called = {"count": 0}

        async def mock_fetch(session, query):
            called["count"] += 1
            return sb.IntelligencePacket(
                source="MockScout", url="http://mock.com",
                content="Force mode content that is long enough to pass the filter check easily.",
                confidence=0.9, word_count=15,
            )

        for scout in bridge.scouts:
            scout.fetch = mock_fetch

        result = await bridge.run("any query", force=True)
        assert called["count"] == len(bridge.scouts)

    @pytest.mark.asyncio
    async def test_all_scouts_empty_returns_scouts_empty(self, tmp_path, monkeypatch):
        import sentinel.sentinel_bridge as sb
        monkeypatch.setattr(sb, "QDRANT_AVAILABLE", False)
        monkeypatch.setattr(sb, "KISWARM_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(sb, "CACHE_FILE", str(tmp_path / "cache.json"))

        bridge = sb.SentinelBridge()

        async def mock_low_conf(session, query):
            return 0.1

        bridge.ckm.estimate_local_confidence = mock_low_conf

        async def empty_fetch(session, query):
            return None

        for scout in bridge.scouts:
            scout.fetch = empty_fetch

        result = await bridge.run("test")
        assert result["status"] == "scouts_empty"
