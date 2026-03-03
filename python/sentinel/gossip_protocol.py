"""
KISWARM v4.8 — Module 47: Gossip Protocol
==========================================
How fixes and experience propagate through the mesh — without any central server.

Inspired by epidemic broadcast protocols used in industrial sensor networks
and Cassandra's anti-entropy repair — not by social media algorithms.

Rules:
  1. A gossip item travels max TTL hops (default 4 = reaches ~625 nodes at 5 peers)
  2. Each node stores seen gossip signatures (SHA-256[:16]) to prevent loops
  3. A node re-broadcasts to ALL its peers except the one it received from
  4. Older gossip (> 24h) is not re-broadcast but stored locally
  5. Fixes are MERGED into local known_fixes.json if fix_id not already present
  6. Experience events are APPENDED to local experience store

Gossip types:
  - "fix"         → a new known fix to add to known_fixes.json
  - "experience"  → an anonymized experience event pattern
  - "peer_info"   → a node announcing itself to the mesh
  - "upgrade"     → a signal that a newer KISWARM version is available

Dual-track: this runs PARALLEL to GitHub FeedbackChannel.
  GitHub  → broad reach, community scale, internet required
  Gossip  → zero latency, zero dependency, works on air-gapped networks
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_TTL    = 4      # hops
MAX_SEEN_SIZE  = 10_000 # signatures to keep in memory
GOSSIP_MAX_AGE = 86400  # 24 hours — don't re-broadcast older gossip


class GossipType(str, Enum):
    FIX        = "fix"
    EXPERIENCE = "experience"
    PEER_INFO  = "peer_info"
    UPGRADE    = "upgrade"


@dataclass
class GossipItem:
    """A single gossip packet traveling through the mesh."""
    gossip_id:   str            # UUID
    gossip_type: str            # GossipType value
    origin_id:   str            # node_id of originator (first 16 chars)
    created_at:  float
    ttl:         int            # Decremented each hop — drop at 0
    payload:     Dict[str, Any] # Type-specific payload
    signature:   str            # SHA-256[:16] of (gossip_id + payload)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > GOSSIP_MAX_AGE

    def should_forward(self) -> bool:
        return self.ttl > 0 and not self.is_expired()

    def decrement(self) -> "GossipItem":
        """Return a copy with TTL decremented."""
        return GossipItem(
            gossip_id=self.gossip_id,
            gossip_type=self.gossip_type,
            origin_id=self.origin_id,
            created_at=self.created_at,
            ttl=self.ttl - 1,
            payload=self.payload,
            signature=self.signature,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GossipItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def create(
        cls,
        gossip_type: GossipType,
        origin_id: str,
        payload: Dict[str, Any],
        ttl: int = DEFAULT_TTL,
    ) -> "GossipItem":
        import uuid as _uuid
        gid = str(_uuid.uuid4())
        sig = hashlib.sha256(
            (gid + json.dumps(payload, sort_keys=True)).encode()
        ).hexdigest()[:16]
        return cls(
            gossip_id=gid,
            gossip_type=gossip_type.value,
            origin_id=origin_id,
            created_at=time.time(),
            ttl=ttl,
            payload=payload,
            signature=sig,
        )


# ─────────────────────────────────────────────────────────────────────────────
# GOSSIP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class GossipProtocol:
    """
    Manages gossip creation, forwarding, deduplication, and application.
    Works with SwarmPeer to broadcast and receive gossip.
    """

    def __init__(
        self,
        node_id:        str,
        storage_dir:    Optional[str] = None,
        on_new_fix:     Optional[Callable[[Dict[str, Any]], None]] = None,
        on_new_exp:     Optional[Callable[[Dict[str, Any]], None]] = None,
        on_upgrade:     Optional[Callable[[str], None]] = None,
    ):
        self.node_id      = node_id
        self.on_new_fix   = on_new_fix
        self.on_new_exp   = on_new_exp
        self.on_upgrade   = on_upgrade

        if storage_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            storage_dir = os.path.join(base, "sentinel_data", "gossip")
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

        self._seen:         Set[str]             = set()   # Signatures we've processed
        self._items:        Dict[str, GossipItem] = {}     # gossip_id → item
        self._broadcast_fn: Optional[Callable]   = None   # SwarmPeer.broadcast_gossip

        # Load persisted seen-set (survives restarts, prevents re-processing)
        self._seen_file = os.path.join(storage_dir, "seen.json")
        self._load_seen()

        self._fixes_file = os.path.join(
            os.path.dirname(storage_dir), "..", "experience", "known_fixes.json"
        )

    def set_broadcaster(self, fn: Callable[[Dict[str, Any]], int]) -> None:
        """Connect to SwarmPeer.broadcast_gossip."""
        self._broadcast_fn = fn

    # ── Create and send gossip ────────────────────────────────────────────────

    def gossip_fix(self, fix_data: Dict[str, Any]) -> GossipItem:
        """Broadcast a new fix to the entire mesh."""
        item = GossipItem.create(GossipType.FIX, self.node_id, fix_data)
        self._mark_seen(item.signature)
        self._broadcast(item)
        logger.info(f"[Gossip] Broadcasting fix: {fix_data.get('fix_id', '?')} — TTL={item.ttl}")
        return item

    def gossip_experience(self, exp_event: Dict[str, Any]) -> GossipItem:
        """Broadcast an anonymized experience pattern."""
        # Strip everything except the pattern
        safe = {
            "error_class":    exp_event.get("error_class"),
            "error_message":  exp_event.get("error_message", "")[:100],
            "module":         exp_event.get("module"),
            "os_family":      exp_event.get("os_family"),
            "kiswarm_version": exp_event.get("kiswarm_version"),
        }
        item = GossipItem.create(GossipType.EXPERIENCE, self.node_id, safe, ttl=3)
        self._mark_seen(item.signature)
        self._broadcast(item)
        return item

    def gossip_upgrade(self, new_version: str, changelog: str = "") -> GossipItem:
        """Signal to the mesh that a new KISWARM version is available."""
        item = GossipItem.create(GossipType.UPGRADE, self.node_id, {
            "version":   new_version,
            "changelog": changelog[:500],
            "upgrade_cmd": "git -C ~/KISWARM pull origin main",
        })
        self._mark_seen(item.signature)
        self._broadcast(item)
        logger.info(f"[Gossip] Broadcasting upgrade signal: v{new_version}")
        return item

    def gossip_peer_info(self, address: str, port: int) -> GossipItem:
        """Announce a peer node to the mesh (peer discovery via gossip)."""
        item = GossipItem.create(GossipType.PEER_INFO, self.node_id, {
            "address": address,
            "port":    port,
            "node_id": self.node_id,
        }, ttl=2)
        self._mark_seen(item.signature)
        self._broadcast(item)
        return item

    # ── Receive gossip ────────────────────────────────────────────────────────

    def receive(self, raw: Dict[str, Any]) -> bool:
        """
        Process incoming gossip from a peer.
        Returns True if new (not seen before), False if duplicate.
        """
        try:
            item = GossipItem.from_dict(raw)
        except Exception as e:
            logger.debug(f"[Gossip] Invalid gossip: {e}")
            return False

        # Deduplication
        if item.signature in self._seen:
            return False

        self._mark_seen(item.signature)
        self._items[item.gossip_id] = item

        # Apply the gossip locally
        self._apply(item)

        # Forward if TTL allows
        if item.should_forward():
            self._broadcast(item.decrement())

        return True

    def _apply(self, item: GossipItem) -> None:
        """Apply a gossip item to the local system."""
        if item.gossip_type == GossipType.FIX.value:
            self._apply_fix(item.payload)

        elif item.gossip_type == GossipType.EXPERIENCE.value:
            if self.on_new_exp:
                self.on_new_exp(item.payload)
            self._store_experience(item.payload)

        elif item.gossip_type == GossipType.UPGRADE.value:
            version = item.payload.get("version", "?")
            logger.info(f"[Gossip] Upgrade signal received: v{version} available")
            if self.on_upgrade:
                self.on_upgrade(version)

        elif item.gossip_type == GossipType.PEER_INFO.value:
            # Peer discovery — caller (SwarmPeer) can use this
            addr = item.payload.get("address")
            port = item.payload.get("port", 11440)
            logger.info(f"[Gossip] Peer discovery: {addr}:{port}")

    def _apply_fix(self, fix_data: Dict[str, Any]) -> None:
        """Merge a received fix into local known_fixes.json."""
        if not fix_data.get("fix_id"):
            return

        try:
            fixes_file = os.path.realpath(self._fixes_file)
            if os.path.exists(fixes_file):
                with open(fixes_file) as f:
                    data = json.load(f)
            else:
                data = {"version": "1.0", "fixes": []}

            existing_ids = {f["fix_id"] for f in data.get("fixes", [])}
            if fix_data["fix_id"] in existing_ids:
                return  # Already have this fix

            data.setdefault("fixes", []).append(fix_data)
            data["updated_at"]  = time.strftime("%Y-%m-%dT%H:%M:%S")
            data["total_fixes"] = len(data["fixes"])

            with open(fixes_file, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(f"[Gossip] New fix applied: {fix_data['fix_id']} (via mesh)")
            if self.on_new_fix:
                self.on_new_fix(fix_data)

        except Exception as e:
            logger.warning(f"[Gossip] Fix apply failed: {e}")

    def _store_experience(self, exp: Dict[str, Any]) -> None:
        """Store a received experience pattern for local analysis."""
        try:
            store = os.path.join(self.storage_dir, "received_experiences.jsonl")
            with open(store, "a") as f:
                f.write(json.dumps({**exp, "received_at": time.time()}) + "\n")
        except Exception:
            pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _broadcast(self, item: GossipItem) -> int:
        if self._broadcast_fn:
            return self._broadcast_fn(item.to_dict())
        return 0

    def _mark_seen(self, signature: str) -> None:
        self._seen.add(signature)
        if len(self._seen) > MAX_SEEN_SIZE:
            # Trim oldest (approximate — sets have no order, this just limits size)
            overflow = list(self._seen)[:1000]
            for sig in overflow:
                self._seen.discard(sig)
        self._save_seen()

    def _save_seen(self) -> None:
        try:
            # Save only recent 1000 to avoid bloat
            seen_list = list(self._seen)[-1000:]
            with open(self._seen_file, "w") as f:
                json.dump({"seen": seen_list, "saved_at": time.time()}, f)
        except Exception:
            pass

    def _load_seen(self) -> None:
        try:
            if os.path.exists(self._seen_file):
                with open(self._seen_file) as f:
                    data = json.load(f)
                self._seen = set(data.get("seen", []))
        except Exception:
            self._seen = set()

    # ── Status ────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        type_counts: Dict[str, int] = {}
        for item in self._items.values():
            t = item.gossip_type
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "node_id":       self.node_id,
            "items_seen":    len(self._seen),
            "items_stored":  len(self._items),
            "by_type":       type_counts,
            "has_broadcaster": self._broadcast_fn is not None,
        }
