"""
KISWARM v4.5 — Module 33b: Evolution Memory Vault
==================================================
Immutable, append-only event log for swarm entity lifecycle evolution.

Records every significant event in an entity's life:
  • immortality_checkpoint   — periodic state preservation
  • model_upgrade            — model family change
  • role_change              — capability added / removed
  • migration                — hardware / VM migration
  • recovery                 — entity reconstructed after failure
  • custom                   — arbitrary application events

Design:
  • Single JSONL file — append-only (never modified, only extended)
  • SHA-256 event ID for deduplication and reference
  • Indexed in-memory on load for fast queries
  • Supports filtering by entity_id, event_type, time range

Integration:
  SwarmImmortalityKernel calls record_event() on every checkpoint.
  Any system may call get_history() to replay an entity's evolution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_VAULT_DIR = os.path.join(
    os.path.dirname(__file__), "../../sentinel_data/evolution_vault"
)

VALID_EVENT_TYPES = {
    "immortality_checkpoint",
    "model_upgrade",
    "role_change",
    "migration",
    "recovery",
    "hardware_loss",
    "sil_recertification",
    "governance_decision",
    "custom",
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VaultEvent:
    event_id:   str
    event_type: str
    entity_id:  str
    timestamp:  float
    payload:    Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id":   self.event_id,
            "event_type": self.event_type,
            "entity_id":  self.entity_id,
            "timestamp":  self.timestamp,
            "payload":    self.payload,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VaultEvent":
        return cls(
            event_id=d["event_id"],
            event_type=d["event_type"],
            entity_id=d.get("entity_id", "unknown"),
            timestamp=d["timestamp"],
            payload=d.get("payload", {}),
        )


def _make_event_id(entity_id: str, event_type: str, timestamp: float) -> str:
    raw = f"{entity_id}:{event_type}:{timestamp:.6f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────────────
# EVOLUTION MEMORY VAULT
# ─────────────────────────────────────────────────────────────────────────────

class EvolutionMemoryVault:
    """
    Immutable, append-only evolution log for all registered entities.

    Storage:
        vault_dir/
          events.jsonl          ← all events, append-only
          [generated at runtime — never edited]
    """

    def __init__(self, vault_dir: str = DEFAULT_VAULT_DIR):
        self.vault_dir  = vault_dir
        self._vault_path = os.path.join(vault_dir, "events.jsonl")
        os.makedirs(vault_dir, exist_ok=True)
        logger.debug(f"[EvolutionVault] Initialized at {vault_dir}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append_event(self, event: VaultEvent) -> None:
        with open(self._vault_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def _stream_events(self) -> List[VaultEvent]:
        if not os.path.exists(self._vault_path):
            return []
        events: List[VaultEvent] = []
        with open(self._vault_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(VaultEvent.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"[EvolutionVault] Skipping malformed event: {e}")
        return events

    # ── Public API ────────────────────────────────────────────────────────────

    def record_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        entity_id: Optional[str] = None,
    ) -> str:
        """
        Append a new event to the vault.

        event_type must be one of VALID_EVENT_TYPES.
        payload is arbitrary — caller defines structure.
        entity_id extracted from payload if not provided.

        Returns event_id.
        """
        if event_type not in VALID_EVENT_TYPES:
            # Accept unknown types but log warning
            logger.warning(f"[EvolutionVault] Non-standard event type: {event_type}")

        ts = time.time()
        eid = entity_id or payload.get("entity_id", "global")

        event = VaultEvent(
            event_id=_make_event_id(eid, event_type, ts),
            event_type=event_type,
            entity_id=eid,
            timestamp=ts,
            payload=payload,
        )
        self._append_event(event)
        logger.info(f"[EvolutionVault] Recorded {event_type} for {eid} ({event.event_id[:8]})")
        return event.event_id

    def get_history(
        self,
        entity_id: str,
        event_type: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve event history for an entity.

        Optionally filter by event_type and/or since (unix timestamp).
        Returns at most `limit` most-recent events.
        """
        events = self._stream_events()
        filtered = [
            e for e in events
            if e.entity_id == entity_id
            and (event_type is None or e.event_type == event_type)
            and (since is None or e.timestamp >= since)
        ]
        # Return most recent first
        filtered.sort(key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in filtered[:limit]]

    def get_all_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Return all events (most recent first)."""
        events = self._stream_events()
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in events[:limit]]

    def total_events(self) -> int:
        return len(self._stream_events())

    def entity_event_count(self, entity_id: str) -> int:
        return sum(1 for e in self._stream_events() if e.entity_id == entity_id)

    def list_entities(self) -> List[str]:
        return list({e.entity_id for e in self._stream_events()})

    def entity_timeline(self, entity_id: str) -> Dict[str, Any]:
        """Summary timeline of an entity's evolution."""
        history = [e for e in self._stream_events() if e.entity_id == entity_id]
        if not history:
            return {"entity_id": entity_id, "events": 0, "timeline": []}
        history.sort(key=lambda e: e.timestamp)
        return {
            "entity_id":     entity_id,
            "events":        len(history),
            "first_event":   history[0].event_type,
            "latest_event":  history[-1].event_type,
            "age_s":         round(time.time() - history[0].timestamp, 1),
            "timeline":      [
                {"type": e.event_type, "ts": round(e.timestamp, 2)}
                for e in history
            ],
        }

    def stats(self) -> Dict[str, Any]:
        events = self._stream_events()
        by_type: Dict[str, int] = {}
        for e in events:
            by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        return {
            "total_events":   len(events),
            "entity_count":   len({e.entity_id for e in events}),
            "events_by_type": by_type,
        }
