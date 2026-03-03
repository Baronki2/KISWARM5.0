"""
KISWARM v4.5 — Module 33a: Swarm Soul Mirror
=============================================
Identity snapshot system for swarm entities.

Guarantees that an entity's core identity (roles, capabilities, context,
knowledge fingerprint) is captured, hashed, and verifiable — surviving:
  • Model replacement / upgrade
  • Hardware failure & VM migration
  • Network partition & node restart

Design:
  • Every snapshot is SHA-256 signed against its content (tamper-evident)
  • Snapshots stored as append-only JSONL per entity
  • Latest snapshot always retrievable in O(n) with stream scan
  • Verification: re-hash and compare — zero-trust model

Integration:
  SwarmImmortalityKernel calls this module to persist and recover
  an entity's "soul" (identity core) across lifecycle events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SOUL_DIR = os.path.join(
    os.path.dirname(__file__), "../../sentinel_data/soul_mirror"
)

SNAPSHOT_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IdentitySnapshot:
    """
    A point-in-time capture of an entity's core identity.

    identity_core contains:
      - roles:         List[str]   — current capabilities
      - model_family:  str         — e.g. "qwen2.5:14b"
      - knowledge_hash: str        — SHA-256 of known facts fingerprint
      - context:       Dict        — arbitrary context from caller
      - version:       str         — semantic version of the entity
    """
    snapshot_id:     str
    entity_id:       str
    timestamp:       float
    identity_core:   Dict[str, Any]
    content_hash:    str                          # SHA-256 of identity_core
    schema_version:  str = SNAPSHOT_SCHEMA_VERSION
    prev_snapshot_id: Optional[str] = None       # chain reference

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id":      self.snapshot_id,
            "entity_id":        self.entity_id,
            "timestamp":        self.timestamp,
            "identity_core":    self.identity_core,
            "content_hash":     self.content_hash,
            "schema_version":   self.schema_version,
            "prev_snapshot_id": self.prev_snapshot_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IdentitySnapshot":
        return cls(
            snapshot_id=d["snapshot_id"],
            entity_id=d["entity_id"],
            timestamp=d["timestamp"],
            identity_core=d["identity_core"],
            content_hash=d["content_hash"],
            schema_version=d.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
            prev_snapshot_id=d.get("prev_snapshot_id"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SWARM SOUL MIRROR
# ─────────────────────────────────────────────────────────────────────────────

class SwarmSoulMirror:
    """
    Manages identity snapshots for all registered swarm entities.

    Storage layout (JSONL, one file per entity):
        soul_dir/
          <entity_id>.jsonl    ← append-only snapshot chain
    """

    def __init__(self, soul_dir: str = DEFAULT_SOUL_DIR):
        self.soul_dir = soul_dir
        os.makedirs(soul_dir, exist_ok=True)
        logger.debug(f"[SoulMirror] Initialized at {soul_dir}")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _entity_path(self, entity_id: str) -> str:
        # Sanitise: allow only alphanum, dash, underscore
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in entity_id)
        return os.path.join(self.soul_dir, f"{safe}.jsonl")

    @staticmethod
    def _hash_identity(identity_core: Dict[str, Any]) -> str:
        canonical = json.dumps(identity_core, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _append_snapshot(self, snapshot: IdentitySnapshot) -> None:
        path = self._entity_path(snapshot.entity_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")

    def _load_all_snapshots(self, entity_id: str) -> List[IdentitySnapshot]:
        path = self._entity_path(entity_id)
        if not os.path.exists(path):
            return []
        snapshots: List[IdentitySnapshot] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    snapshots.append(IdentitySnapshot.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"[SoulMirror] Skipping malformed snapshot: {e}")
        return snapshots

    # ── Public API ────────────────────────────────────────────────────────────

    def create_identity_snapshot(
        self,
        entity_id: str,
        context: Dict[str, Any],
    ) -> str:
        """
        Create and persist a new identity snapshot.

        context should include:
          roles, model_family, knowledge_hash, version, etc.

        Returns snapshot_id.
        """
        if not entity_id:
            raise ValueError("entity_id must not be empty")

        # Find previous snapshot for chain
        existing = self._load_all_snapshots(entity_id)
        prev_id = existing[-1].snapshot_id if existing else None

        identity_core = {
            "roles":          context.get("roles", []),
            "model_family":   context.get("model_family", "unknown"),
            "knowledge_hash": context.get("knowledge_hash", ""),
            "version":        context.get("version", "0.0.1"),
            "context":        {k: v for k, v in context.items()
                               if k not in {"roles", "model_family", "knowledge_hash", "version"}},
        }

        content_hash = self._hash_identity(identity_core)

        snapshot = IdentitySnapshot(
            snapshot_id=str(uuid.uuid4()),
            entity_id=entity_id,
            timestamp=time.time(),
            identity_core=identity_core,
            content_hash=content_hash,
            prev_snapshot_id=prev_id,
        )

        self._append_snapshot(snapshot)
        logger.info(f"[SoulMirror] Snapshot {snapshot.snapshot_id[:8]} created for {entity_id}")
        return snapshot.snapshot_id

    def get_latest_snapshot(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent snapshot dict, or None."""
        snapshots = self._load_all_snapshots(entity_id)
        if not snapshots:
            return None
        latest = max(snapshots, key=lambda s: s.timestamp)
        return latest.to_dict()

    def get_snapshot_by_id(self, entity_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific snapshot by ID."""
        for s in self._load_all_snapshots(entity_id):
            if s.snapshot_id == snapshot_id:
                return s.to_dict()
        return None

    def verify_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        """
        Verify snapshot integrity by re-hashing identity_core.
        Returns True if content_hash matches.
        """
        try:
            expected = self._hash_identity(snapshot["identity_core"])
            return expected == snapshot.get("content_hash", "")
        except Exception as e:
            logger.error(f"[SoulMirror] Verification error: {e}")
            return False

    def list_entities(self) -> List[str]:
        """Return all entity IDs with at least one snapshot."""
        entities = []
        for fname in os.listdir(self.soul_dir):
            if fname.endswith(".jsonl"):
                entities.append(fname[:-6])   # strip .jsonl
        return entities

    def snapshot_count(self, entity_id: str) -> int:
        return len(self._load_all_snapshots(entity_id))

    def entity_stats(self, entity_id: str) -> Dict[str, Any]:
        """Summary statistics for one entity."""
        snapshots = self._load_all_snapshots(entity_id)
        if not snapshots:
            return {"entity_id": entity_id, "snapshot_count": 0, "latest": None}
        latest = max(snapshots, key=lambda s: s.timestamp)
        ages   = [time.time() - s.timestamp for s in snapshots]
        return {
            "entity_id":      entity_id,
            "snapshot_count": len(snapshots),
            "latest_id":      latest.snapshot_id,
            "latest_age_s":   round(time.time() - latest.timestamp, 1),
            "oldest_age_s":   round(max(ages), 1),
            "valid":          self.verify_snapshot(latest.to_dict()),
        }
