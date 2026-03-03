"""
KISWARM v2.2 — MODULE 4: CRYPTOGRAPHIC KNOWLEDGE LEDGER
========================================================
Signs every SwarmKnowledge entry. Maintains an append-only
Merkle log. Detects tampering of any historical knowledge entry.

Architecture:
  • Each entry is signed with SHA-256(content + metadata + prev_root)
  • A Merkle tree is maintained over all entry hashes
  • The ledger root hash changes if ANY entry is tampered with
  • Tamper detection: recompute Merkle root and compare to stored root

Merkle Tree:
  Leaf nodes  = SHA-256(entry content + signature)
  Parent node = SHA-256(left_child + right_child)
  Root        = single hash representing the entire ledger state

Entry Structure:
  {
    "index":       4,
    "hash_id":     "a3f2b91c",
    "query":       "...",
    "content_hash": "sha256 of content",
    "sources":     [...],
    "confidence":  0.87,
    "timestamp":   "ISO-8601",
    "prev_root":   "merkle root before this entry",
    "signature":   "sha256(content_hash + metadata + prev_root)",
    "merkle_pos":  "leaf index in Merkle tree"
  }

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("sentinel.ledger")

KISWARM_HOME  = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR   = os.path.join(KISWARM_HOME, "KISWARM")
LEDGER_FILE   = os.path.join(KISWARM_DIR, "knowledge_ledger.json")


# ── Merkle Tree ───────────────────────────────────────────────────────────────

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def merkle_root(leaves: list[str]) -> str:
    """
    Compute Merkle root from a list of leaf hashes.
    Returns the root hash string. Empty list returns all-zeros hash.
    """
    if not leaves:
        return "0" * 64
    layer = list(leaves)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])  # duplicate last node if odd
        next_layer = []
        for i in range(0, len(layer), 2):
            next_layer.append(_sha256(layer[i] + layer[i + 1]))
        layer = next_layer
    return layer[0]

def merkle_proof(leaves: list[str], index: int) -> list[dict]:
    """
    Generate Merkle inclusion proof for leaf at `index`.
    Returns list of {"hash": ..., "position": "left"|"right"} nodes.
    """
    if not leaves or index >= len(leaves):
        return []
    proof = []
    layer = list(leaves)
    i = index
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        if i % 2 == 0:
            sibling_idx = i + 1
            pos = "right"
        else:
            sibling_idx = i - 1
            pos = "left"
        proof.append({"hash": layer[sibling_idx], "position": pos})
        next_layer = []
        for j in range(0, len(layer), 2):
            next_layer.append(_sha256(layer[j] + layer[j + 1]))
        layer = next_layer
        i //= 2
    return proof


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    """A single signed, append-only knowledge ledger entry."""
    index:          int
    hash_id:        str
    query:          str
    content_hash:   str       # SHA-256 of the content
    sources:        list
    confidence:     float
    classification: str
    timestamp:      str
    prev_root:      str       # Merkle root before this entry
    signature:      str       # SHA-256(content_hash + metadata + prev_root)
    leaf_hash:      str       # SHA-256(signature + content_hash)

    def to_dict(self) -> dict:
        return asdict(self)

    def verify_signature(self) -> bool:
        """Recompute and verify the entry's signature."""
        expected = self._compute_signature(
            self.content_hash, self.query, self.confidence,
            self.timestamp, self.classification, self.prev_root,
        )
        return expected == self.signature

    @staticmethod
    def _compute_signature(
        content_hash: str, query: str, confidence: float,
        timestamp: str, classification: str, prev_root: str,
    ) -> str:
        payload = json.dumps({
            "content_hash": content_hash,
            "query": query,
            "confidence": confidence,
            "timestamp": timestamp,
            "classification": classification,
            "prev_root": prev_root,
        }, sort_keys=True)
        return _sha256(payload)


@dataclass
class TamperReport:
    """Result of a ledger integrity verification."""
    valid:              bool
    total_entries:      int
    tampered_entries:   list[int]   # indices of tampered entries
    current_root:       str
    expected_root:      str
    root_match:         bool
    timestamp:          str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_clean(self) -> bool:
        return self.valid and not self.tampered_entries


# ── Cryptographic Knowledge Ledger ───────────────────────────────────────────

class CryptographicKnowledgeLedger:
    """
    Append-only, Merkle-authenticated ledger for all SwarmKnowledge entries.

    Every write operation:
      1. Hashes the content (SHA-256)
      2. Computes a signature over content + metadata + current Merkle root
      3. Appends to the ledger
      4. Updates the Merkle root

    Any tampering with a past entry will:
      1. Invalidate the entry's own signature
      2. Break the Merkle root chain
      3. Be detected by verify_integrity()
    """

    def __init__(self, ledger_path: str = LEDGER_FILE):
        self._ledger_path = ledger_path
        self._entries:    list[LedgerEntry] = []
        self._leaf_hashes: list[str]        = []
        self._root:        str              = "0" * 64
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._ledger_path):
            try:
                with open(self._ledger_path) as f:
                    raw = json.load(f)
                self._entries     = [LedgerEntry(**e) for e in raw.get("entries", [])]
                self._root        = raw.get("root", "0" * 64)
                self._leaf_hashes = [e.leaf_hash for e in self._entries]
                logger.info("Ledger loaded: %d entries | root=%s…", len(self._entries), self._root[:12])
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("Ledger load failed: %s", exc)
                self._entries, self._leaf_hashes, self._root = [], [], "0" * 64

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._ledger_path), exist_ok=True)
            payload = {
                "root":    self._root,
                "entries": [e.to_dict() for e in self._entries],
            }
            with open(self._ledger_path, "w") as f:
                json.dump(payload, f, indent=2)
        except OSError as exc:
            logger.error("Ledger save failed: %s", exc)

    # ── Core API ──────────────────────────────────────────────────────────────

    def append(self, knowledge) -> LedgerEntry:
        """
        Append a SwarmKnowledge entry to the ledger.

        Args:
            knowledge: SwarmKnowledge instance (must have .hash_id, .query,
                       .content, .sources, .confidence, .classification, .timestamp)

        Returns:
            LedgerEntry — the signed, committed ledger record.
        """
        content_hash = _sha256(knowledge.content)
        prev_root    = self._root

        signature = LedgerEntry._compute_signature(
            content_hash=content_hash,
            query=knowledge.query,
            confidence=knowledge.confidence,
            timestamp=knowledge.timestamp,
            classification=knowledge.classification,
            prev_root=prev_root,
        )

        leaf_hash = _sha256(signature + content_hash)

        entry = LedgerEntry(
            index=len(self._entries),
            hash_id=knowledge.hash_id,
            query=knowledge.query,
            content_hash=content_hash,
            sources=knowledge.sources,
            confidence=knowledge.confidence,
            classification=knowledge.classification,
            timestamp=knowledge.timestamp,
            prev_root=prev_root,
            signature=signature,
            leaf_hash=leaf_hash,
        )

        self._entries.append(entry)
        self._leaf_hashes.append(leaf_hash)
        self._root = merkle_root(self._leaf_hashes)
        self._save()

        logger.info(
            "Ledger append: index=%d | hash=%s | root=%s…",
            entry.index, knowledge.hash_id, self._root[:12],
        )
        return entry

    def verify_integrity(self) -> TamperReport:
        """
        Full ledger integrity check.
        Recomputes all signatures and the Merkle root.
        Returns TamperReport with any detected tampering.
        """
        tampered = []

        for entry in self._entries:
            if not entry.verify_signature():
                tampered.append(entry.index)
                logger.error(
                    "TAMPER DETECTED at ledger index %d (hash_id=%s)",
                    entry.index, entry.hash_id,
                )

        # Recompute Merkle root from stored leaf hashes
        computed_root = merkle_root([e.leaf_hash for e in self._entries])
        root_match    = computed_root == self._root

        if not root_match:
            logger.critical(
                "Merkle root mismatch! Expected=%s… Got=%s…",
                self._root[:12], computed_root[:12],
            )

        valid = not tampered and root_match
        return TamperReport(
            valid=valid,
            total_entries=len(self._entries),
            tampered_entries=tampered,
            current_root=computed_root,
            expected_root=self._root,
            root_match=root_match,
        )

    def get_proof(self, index: int) -> dict:
        """
        Get Merkle inclusion proof for entry at `index`.
        Proves a specific entry exists in the ledger without revealing all entries.
        """
        if index < 0 or index >= len(self._entries):
            return {"error": f"Index {index} out of range"}
        proof = merkle_proof(self._leaf_hashes, index)
        entry = self._entries[index]
        return {
            "index":      index,
            "hash_id":    entry.hash_id,
            "leaf_hash":  entry.leaf_hash,
            "root":       self._root,
            "proof":      proof,
            "proof_len":  len(proof),
        }

    def get_entry(self, hash_id: str) -> Optional[LedgerEntry]:
        for e in self._entries:
            if e.hash_id == hash_id:
                return e
        return None

    @property
    def root(self) -> str:
        return self._root

    @property
    def size(self) -> int:
        return len(self._entries)

    def summary(self) -> dict:
        return {
            "entries":    self.size,
            "root":       self._root,
            "root_short": self._root[:16] + "…",
            "timestamp":  datetime.now().isoformat(),
        }
