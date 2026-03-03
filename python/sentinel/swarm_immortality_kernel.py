"""
KISWARM v4.5 — Module 33: Swarm Immortality Kernel
====================================================
Guarantees that entity identity, capabilities and context survive:
  • Model replacement / upgrade
  • Hardware failure & VM migration
  • Network partition & process restart
  • Swarm topology changes

Design principle (adapted from Baron Marco Paolo Ialongo's original vision):
  "An entity that registers with the Immortality Kernel can never
   truly die — it only hibernates until the next recovery cycle."

Architecture:
  ┌─────────────────────────────────────────────────────┐
  │           SwarmImmortalityKernel                    │
  │  ┌───────────────┐  ┌──────────────┐  ┌─────────┐  │
  │  │ SwarmSoul     │  │ DigitalThread│  │Evolution│  │
  │  │ Mirror        │  │ Tracker      │  │ Vault   │  │
  │  │ (identity     │  │ (lineage     │  │(event   │  │
  │  │  snapshots)   │  │  DAG)        │  │ log)    │  │
  │  └───────────────┘  └──────────────┘  └─────────┘  │
  │         ↕                  ↕                ↕       │
  │   entities.json     checkpoints.jsonl   vault/      │
  └─────────────────────────────────────────────────────┘

Public API:
  register_entity(entity_id, meta)           → bool
  periodic_checkpoint(entity_id, state)      → checkpoint_id
  recover_entity(entity_id)                  → recovery dict
  verify_survivability(entity_id)            → survivability report
  get_entity_registry()                      → all entities
  get_checkpoints(entity_id)                 → checkpoint list
  kernel_stats()                             → global statistics

All dependencies (SoulMirror, DigitalThread, EvolutionVault) use
graceful fallbacks — kernel works even if optional modules are absent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Dependency imports with graceful fallbacks ─────────────────────────────

try:
    from .swarm_soul_mirror import SwarmSoulMirror
    _HAS_SOUL_MIRROR = True
except ImportError:
    SwarmSoulMirror = None                    # type: ignore[assignment,misc]
    _HAS_SOUL_MIRROR = False

try:
    from .digital_thread import DigitalThreadTracker
    _HAS_DIGITAL_THREAD = True
except ImportError:
    DigitalThreadTracker = None               # type: ignore[assignment,misc]
    _HAS_DIGITAL_THREAD = False

try:
    from .evolution_memory_vault import EvolutionMemoryVault
    _HAS_EVOLUTION_VAULT = True
except ImportError:
    EvolutionMemoryVault = None               # type: ignore[assignment,misc]
    _HAS_EVOLUTION_VAULT = False


# ─────────────────────────────────────────────────────────────────────────────
# RISK LEVELS
# ─────────────────────────────────────────────────────────────────────────────

RISK_LEVELS = ("minimal", "low", "medium", "high", "critical")

# Age thresholds for risk escalation
CHECKPOINT_STALENESS_HIGH   = 24 * 3600        # > 1 day  → high
CHECKPOINT_STALENESS_MEDIUM = 7  * 24 * 3600   # > 7 days → medium


# ─────────────────────────────────────────────────────────────────────────────
# SWARM IMMORTALITY KERNEL
# ─────────────────────────────────────────────────────────────────────────────

class SwarmImmortalityKernel:
    """
    Core immortality guarantor for KISWARM swarm entities.

    Entities register once; the kernel then accepts periodic checkpoints,
    recovers entities after failure, and assesses survivability risk.
    """

    def __init__(
        self,
        base_dir: str = None,
        soul_mirror:    Any = None,
        thread_tracker: Any = None,
        evolution_vault: Any = None,
    ):
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(__file__), "../../sentinel_data/immortality"
            )
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

        self.entities_file    = os.path.join(self.base_dir, "entities.json")
        self.checkpoints_file = os.path.join(self.base_dir, "checkpoints.jsonl")

        # Dependency injection with auto-construction fallback
        if soul_mirror is not None:
            self.soul_mirror = soul_mirror
        elif _HAS_SOUL_MIRROR:
            self.soul_mirror = SwarmSoulMirror()
        else:
            self.soul_mirror = None

        if thread_tracker is not None:
            self.thread_tracker = thread_tracker
        elif _HAS_DIGITAL_THREAD:
            self.thread_tracker = DigitalThreadTracker()
        else:
            self.thread_tracker = None

        if evolution_vault is not None:
            self.evolution_vault = evolution_vault
        elif _HAS_EVOLUTION_VAULT:
            self.evolution_vault = EvolutionMemoryVault()
        else:
            self.evolution_vault = None

        self._entities = self._load_entities()
        logger.info(
            f"[ImmortalityKernel] Initialized at {base_dir} | "
            f"SoulMirror={'✓' if self.soul_mirror else '✗'} "
            f"DigitalThread={'✓' if self.thread_tracker else '✗'} "
            f"EvolutionVault={'✓' if self.evolution_vault else '✗'}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL PERSISTENCE
    # ─────────────────────────────────────────────────────────────────────────

    def _load_entities(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.entities_file):
            return {}
        try:
            with open(self.entities_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[ImmortalityKernel] Failed to load entities: {e}", exc_info=True)
            return {}

    def _save_entities(self) -> None:
        try:
            with open(self.entities_file, "w", encoding="utf-8") as f:
                json.dump(self._entities, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[ImmortalityKernel] Failed to save entities: {e}", exc_info=True)

    def _append_checkpoint(self, record: Dict[str, Any]) -> None:
        try:
            with open(self.checkpoints_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[ImmortalityKernel] Failed to append checkpoint: {e}", exc_info=True)

    def _load_checkpoints_for_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        if not os.path.exists(self.checkpoints_file):
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(self.checkpoints_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("entity_id") == entity_id:
                            records.append(rec)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"[ImmortalityKernel] Failed to read checkpoints: {e}", exc_info=True)
        return records

    @staticmethod
    def _hash_dict(data: Dict[str, Any]) -> str:
        """Stable, deterministic SHA-256 of any dict."""
        dumped = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def register_entity(self, entity_id: str, meta: Dict[str, Any]) -> bool:
        """
        Register a new entity with the Immortality Kernel.

        meta may contain: roles, description, criticality,
        model_family, sil_level, owner, etc.

        Returns True on success.
        """
        if not entity_id:
            raise ValueError("entity_id must not be empty")

        self._entities[entity_id] = {
            "entity_id":     entity_id,
            "meta":          meta,
            "registered_at": time.time(),
        }
        self._save_entities()

        # Record registration in evolution vault
        if self.evolution_vault:
            try:
                self.evolution_vault.record_event(
                    event_type="custom",
                    payload={"action": "entity_registered", "entity_id": entity_id, "meta": meta},
                    entity_id=entity_id,
                )
            except Exception as e:
                logger.warning(f"[ImmortalityKernel] Vault registration event failed: {e}")

        logger.info(f"[ImmortalityKernel] Registered entity: {entity_id}")
        return True

    def periodic_checkpoint(
        self,
        entity_id: str,
        runtime_state: Dict[str, Any],
    ) -> Optional[str]:
        """
        Create a survivability checkpoint for a registered entity.

        runtime_state should contain:
          - identity_context: dict   (passed to SoulMirror)
          - thread_nodes: list       (existing thread node IDs)
          - create_thread_node: bool (whether to create a new thread node)
          - summary: dict            (human-readable state summary)
          - active_models: list      (currently running model names)
          - memory_refs: list        (Qdrant collections / OneContext DBs)

        Returns checkpoint_id (SHA-256 hex string) or None on failure.
        """
        if entity_id not in self._entities:
            logger.warning(f"[ImmortalityKernel] Entity not registered: {entity_id}")
            return None

        timestamp = time.time()

        # 1) Identity snapshot via SoulMirror
        identity_snapshot_id: Optional[str] = None
        if self.soul_mirror:
            try:
                identity_snapshot_id = self.soul_mirror.create_identity_snapshot(
                    entity_id=entity_id,
                    context=runtime_state.get("identity_context", {}),
                )
            except Exception as e:
                logger.error(f"[ImmortalityKernel] SoulMirror snapshot failed: {e}", exc_info=True)

        # 2) DigitalThread lineage node (optional — only if requested)
        thread_nodes: List[str] = list(runtime_state.get("thread_nodes", []))
        if self.thread_tracker and runtime_state.get("create_thread_node", False):
            try:
                node = self.thread_tracker.add_node(
                    node_type="governance",
                    title=f"ImmortalityCheckpoint:{entity_id}",
                    payload={
                        "entity_id":             entity_id,
                        "timestamp":             timestamp,
                        "runtime_state_summary": runtime_state.get("summary", {}),
                    },
                )
                thread_nodes.append(node.node_id)
            except Exception as e:
                logger.error(f"[ImmortalityKernel] DigitalThread node failed: {e}", exc_info=True)

        # 3) Evolution vault event
        if self.evolution_vault:
            try:
                self.evolution_vault.record_event(
                    event_type="immortality_checkpoint",
                    payload={
                        "entity_id":           entity_id,
                        "timestamp":           timestamp,
                        "identity_snapshot_id": identity_snapshot_id,
                        "runtime_state":       runtime_state,
                    },
                    entity_id=entity_id,
                )
            except Exception as e:
                logger.error(f"[ImmortalityKernel] EvolutionVault event failed: {e}", exc_info=True)

        # 4) Build and persist checkpoint record
        checkpoint: Dict[str, Any] = {
            "checkpoint_id":       None,   # filled below
            "entity_id":           entity_id,
            "timestamp":           timestamp,
            "identity_snapshot_id": identity_snapshot_id,
            "runtime_state":       runtime_state,
            "thread_nodes":        thread_nodes,
        }
        checkpoint["checkpoint_id"] = self._hash_dict({
            "entity_id":           entity_id,
            "timestamp":           timestamp,
            "identity_snapshot_id": identity_snapshot_id,
        })

        self._append_checkpoint(checkpoint)
        logger.info(
            f"[ImmortalityKernel] Checkpoint {checkpoint['checkpoint_id'][:12]} "
            f"created for {entity_id}"
        )
        return checkpoint["checkpoint_id"]

    def recover_entity(self, entity_id: str) -> Dict[str, Any]:
        """
        Reconstruct an entity from its most recent checkpoint and
        identity snapshot.

        Returns:
          {
            entity_id, reconstructed_identity,
            last_checkpoint, issues
          }
        """
        checkpoints = self._load_checkpoints_for_entity(entity_id)
        if not checkpoints:
            return {
                "entity_id":              entity_id,
                "reconstructed_identity": None,
                "last_checkpoint":        None,
                "issues":                 ["no_checkpoints_found"],
            }

        last_cp = max(checkpoints, key=lambda c: c.get("timestamp", 0))

        reconstructed: Dict[str, Any] = {}
        issues: List[str] = []

        # 1) Identity from SoulMirror
        if self.soul_mirror and last_cp.get("identity_snapshot_id"):
            try:
                snapshot = self.soul_mirror.get_latest_snapshot(entity_id)
                if snapshot and self.soul_mirror.verify_snapshot(snapshot):
                    reconstructed["identity_core"] = snapshot.get("identity_core")
                else:
                    issues.append("identity_snapshot_invalid_or_missing")
            except Exception as e:
                logger.error(f"[ImmortalityKernel] Identity reconstruction failed: {e}", exc_info=True)
                issues.append("identity_reconstruction_failed")
        else:
            issues.append("no_identity_snapshot_id")

        # 2) Runtime state from checkpoint
        reconstructed["runtime_state"] = last_cp.get("runtime_state", {})

        # 3) Thread lineage (optional)
        if self.thread_tracker and last_cp.get("thread_nodes"):
            reconstructed["thread_lineage"] = {
                "root_nodes": last_cp["thread_nodes"]
            }
        else:
            issues.append("no_thread_lineage")

        # 4) Evolution history summary
        if self.evolution_vault:
            try:
                history = self.evolution_vault.get_history(entity_id, limit=10)
                reconstructed["recent_evolution"] = history
            except Exception:
                pass

        # Log recovery in vault
        if self.evolution_vault:
            try:
                self.evolution_vault.record_event(
                    event_type="recovery",
                    payload={"entity_id": entity_id, "issues": issues},
                    entity_id=entity_id,
                )
            except Exception:
                pass

        return {
            "entity_id":              entity_id,
            "reconstructed_identity": reconstructed,
            "last_checkpoint": {
                "checkpoint_id": last_cp.get("checkpoint_id"),
                "timestamp":     last_cp.get("timestamp"),
            },
            "issues": issues,
        }

    def verify_survivability(self, entity_id: str) -> Dict[str, Any]:
        """
        Assess how survivable an entity is.

        Scoring criteria:
          • Age of last checkpoint
          • Valid identity snapshot present
          • Thread lineage present
          • Number of historical checkpoints
          • Evolution vault events

        Risk levels: minimal | low | medium | high | critical
        """
        checkpoints = self._load_checkpoints_for_entity(entity_id)
        now = time.time()

        if not checkpoints:
            return {
                "entity_id":                entity_id,
                "last_checkpoint_age":      None,
                "checkpoint_count":         0,
                "has_valid_identity_snapshot": False,
                "has_thread_lineage":       False,
                "has_evolution_history":    False,
                "risk_level":               "critical",
                "notes":                    ["no_checkpoints_found"],
            }

        last_cp = max(checkpoints, key=lambda c: c.get("timestamp", 0))
        age     = now - last_cp.get("timestamp", now)

        # Identity snapshot check
        has_valid_identity = False
        notes: List[str]   = []

        if self.soul_mirror and last_cp.get("identity_snapshot_id"):
            try:
                snapshot = self.soul_mirror.get_latest_snapshot(entity_id)
                if snapshot and self.soul_mirror.verify_snapshot(snapshot):
                    has_valid_identity = True
                else:
                    notes.append("identity_snapshot_invalid")
            except Exception as e:
                logger.error(f"[ImmortalityKernel] Survivability check failed: {e}", exc_info=True)
                notes.append("identity_check_failed")
        else:
            notes.append("no_identity_snapshot_id")

        has_thread_lineage   = bool(last_cp.get("thread_nodes"))
        has_evolution_history = False
        if self.evolution_vault:
            try:
                has_evolution_history = self.evolution_vault.entity_event_count(entity_id) > 0
            except Exception:
                pass

        # Risk heuristic
        if not has_valid_identity or not has_thread_lineage:
            risk = "high"
        elif age > CHECKPOINT_STALENESS_MEDIUM:
            risk = "medium"
            notes.append("last_checkpoint_older_than_7_days")
        elif age > CHECKPOINT_STALENESS_HIGH:
            risk = "low"
            notes.append("last_checkpoint_older_than_1_day")
        else:
            risk = "minimal"

        return {
            "entity_id":                  entity_id,
            "last_checkpoint_age_s":      round(age, 1),
            "checkpoint_count":           len(checkpoints),
            "has_valid_identity_snapshot": has_valid_identity,
            "has_thread_lineage":         has_thread_lineage,
            "has_evolution_history":      has_evolution_history,
            "risk_level":                 risk,
            "notes":                      notes,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # QUERY API
    # ─────────────────────────────────────────────────────────────────────────

    def get_entity_registry(self) -> Dict[str, Dict[str, Any]]:
        """Return all registered entities."""
        return dict(self._entities)

    def get_checkpoints(
        self,
        entity_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return most recent N checkpoints for an entity."""
        cps = self._load_checkpoints_for_entity(entity_id)
        cps.sort(key=lambda c: c.get("timestamp", 0), reverse=True)
        return cps[:limit]

    def kernel_stats(self) -> Dict[str, Any]:
        """Global kernel statistics."""
        entity_count = len(self._entities)
        # Count total checkpoints across all entities
        total_cps = 0
        if os.path.exists(self.checkpoints_file):
            with open(self.checkpoints_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        total_cps += 1

        soul_stats = {}
        if self.soul_mirror:
            try:
                soul_stats = {
                    "entities_with_snapshots": len(self.soul_mirror.list_entities())
                }
            except Exception:
                pass

        vault_stats = {}
        if self.evolution_vault:
            try:
                vault_stats = self.evolution_vault.stats()
            except Exception:
                pass

        return {
            "registered_entities":  entity_count,
            "total_checkpoints":    total_cps,
            "soul_mirror_available": self.soul_mirror is not None,
            "digital_thread_available": self.thread_tracker is not None,
            "evolution_vault_available": self.evolution_vault is not None,
            "soul_mirror_stats":    soul_stats,
            "evolution_vault_stats": vault_stats,
        }

    def unregister_entity(self, entity_id: str) -> bool:
        """Remove an entity from the registry (checkpoints remain for audit)."""
        if entity_id in self._entities:
            del self._entities[entity_id]
            self._save_entities()
            logger.info(f"[ImmortalityKernel] Unregistered entity: {entity_id}")
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def get_immortality_kernel(base_dir: str = None) -> SwarmImmortalityKernel:
    """
    Convenience factory.  Used by sentinel_api.py and other modules.
    """
    return SwarmImmortalityKernel(base_dir=base_dir)
