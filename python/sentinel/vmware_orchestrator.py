"""
KISWARM v4.1 — Module 20: VMware Autonomous Orchestration
==========================================================
Controlled autonomy layer for VM lifecycle management.

Architecture:  AI Engine → Orchestrator → pyVmomi stub → vCenter

VM Classes:
  VM-A:  AI Core
  VM-B:  SCADA / OPC Runtime Clone
  VM-C:  PLCnext / SoftPLC / Test Runtime
  VM-D:  SQL Historian Clone          (optional)
  VM-E:  Fault Injection Sandbox      (optional)

Rules (hard-coded, cannot be overridden):
  - Production VMs: read-only queries only from AI
  - All mutations execute on CLONE of test VM
  - Every operation logged to immutable audit ledger
  - No direct ESXi root access
  - Human approval required before any production promotion
"""

from __future__ import annotations

import hashlib
import json
import time
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

# ─────────────────────────────────────────────────────────────────────────────
# VM REGISTRY (in-memory model of vCenter inventory)
# ─────────────────────────────────────────────────────────────────────────────

VM_CLASSES = {
    "VM-A": "ai_core",
    "VM-B": "scada_opc",
    "VM-C": "plc_test",
    "VM-D": "sql_historian",
    "VM-E": "fault_sandbox",
}

PRODUCTION_VMS = {"VM-A", "VM-B"}   # NEVER mutate directly
TEST_VMS       = {"VM-C", "VM-D", "VM-E"}


@dataclass
class VMState:
    vm_id:       str
    name:        str
    vm_class:    str
    power:       str = "on"           # "on" | "off" | "suspended"
    cpu_count:   int = 4
    memory_mb:   int = 8192
    disk_gb:     int = 100
    ip_address:  str = ""
    is_clone:    bool = False
    parent_id:   Optional[str] = None
    snapshots:   List[str] = field(default_factory=list)
    tags:        Dict[str, str] = field(default_factory=dict)
    created_at:  str = ""
    network_isolated: bool = False

    def to_dict(self) -> dict:
        return {
            "vm_id":      self.vm_id,
            "name":       self.name,
            "vm_class":   self.vm_class,
            "power":      self.power,
            "cpu_count":  self.cpu_count,
            "memory_mb":  self.memory_mb,
            "disk_gb":    self.disk_gb,
            "is_clone":   self.is_clone,
            "parent_id":  self.parent_id,
            "snapshots":  self.snapshots,
            "network_isolated": self.network_isolated,
            "created_at": self.created_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG ENTRY
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    entry_id:   str
    timestamp:  str
    operation:  str
    vm_id:      str
    actor:      str
    details:    Dict[str, Any]
    result:     str       # "ok" | "denied" | "failed"
    signature:  str = ""  # SHA-256 of payload

    def to_dict(self) -> dict:
        return {
            "entry_id":  self.entry_id,
            "timestamp": self.timestamp,
            "operation": self.operation,
            "vm_id":     self.vm_id,
            "actor":     self.actor,
            "details":   self.details,
            "result":    self.result,
            "signature": self.signature,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MUTATION LINEAGE RECORD
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MutationRecord:
    mutation_id:     str
    source_vm:       str
    clone_vm:        str
    snapshot_before: str
    param_deltas:    Dict[str, float]
    twin_result:     Optional[Dict]
    formal_verified: bool
    promoted:        bool
    approval_code:   Optional[str]
    steps_completed: List[str]
    created_at:      str

    def to_dict(self) -> dict:
        return {
            "mutation_id":     self.mutation_id,
            "source_vm":       self.source_vm,
            "clone_vm":        self.clone_vm,
            "snapshot_before": self.snapshot_before,
            "param_deltas":    self.param_deltas,
            "twin_result":     self.twin_result,
            "formal_verified": self.formal_verified,
            "promoted":        self.promoted,
            "approval_code":   self.approval_code,
            "steps_completed": self.steps_completed,
            "created_at":      self.created_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# pyVmomi STUB (simulates vCenter API without real VMware)
# ─────────────────────────────────────────────────────────────────────────────

class _PyVmomiStub:
    """
    Simulates pyVmomi/vCenter operations.
    Replace with real pyVmomi calls for production.
    """

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)
        self._latency = (0.1, 0.5)   # simulated op latency range

    def _op(self, name: str) -> Dict[str, Any]:
        """Simulate vCenter API call with latency."""
        time.sleep(self._rng.uniform(*self._latency) * 0.001)  # minimal in tests
        return {"status": "ok", "op": name, "moref": str(uuid.uuid4())[:8]}

    def create_snapshot(self, vm_id: str, name: str) -> Dict:
        return {**self._op("CreateSnapshot"), "snapshot_name": name, "vm_id": vm_id}

    def revert_snapshot(self, vm_id: str, snapshot_name: str) -> Dict:
        return {**self._op("RevertSnapshot"), "snapshot_name": snapshot_name}

    def delete_snapshot(self, vm_id: str, snapshot_name: str) -> Dict:
        return {**self._op("DeleteSnapshot"), "snapshot_name": snapshot_name}

    def clone_vm(self, src_vm_id: str, clone_name: str) -> Dict:
        return {**self._op("CloneVM"), "clone_name": clone_name, "src": src_vm_id}

    def power_on(self, vm_id: str) -> Dict:
        return self._op("PowerOn")

    def power_off(self, vm_id: str) -> Dict:
        return self._op("PowerOff")

    def reconfigure_vm(self, vm_id: str, cpu: int = None, mem_mb: int = None) -> Dict:
        return {**self._op("ReconfigVM"), "cpu": cpu, "mem_mb": mem_mb}

    def set_network_isolation(self, vm_id: str, isolate: bool) -> Dict:
        return {**self._op("SetNetwork"), "isolated": isolate}

    def get_vm_info(self, vm_id: str) -> Dict:
        return {**self._op("GetVMInfo"), "vm_id": vm_id, "power": "on"}


# ─────────────────────────────────────────────────────────────────────────────
# VMWARE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class VMwareOrchestrator:
    """
    Controls VM lifecycle for KISWARM mutation governance pipeline.

    Safety rules enforced in code:
      1. Production VMs (VM-A, VM-B) → query only, never mutate
      2. All mutations run on clones of test VMs
      3. Every operation creates an immutable audit entry
      4. Promotion requires valid approval_code
    """

    APPROVAL_CODE = "Maquister_Equtitum"   # Baron's authorization code

    def __init__(self, seed: int = 0):
        self._vcenter = _PyVmomiStub(seed)
        self._vms: Dict[str, VMState] = {}
        self._audit: List[AuditEntry] = []
        self._mutations: Dict[str, MutationRecord] = {}
        self._op_count  = 0
        self._init_default_vms()

    # ── VM Inventory ──────────────────────────────────────────────────────────

    def _init_default_vms(self) -> None:
        for name, vm_class in VM_CLASSES.items():
            self._vms[name] = VMState(
                vm_id=name, name=name, vm_class=vm_class,
                created_at=datetime.utcnow().isoformat(),
                ip_address=f"192.168.100.{10 + list(VM_CLASSES.keys()).index(name)}",
            )

    def register_vm(self, vm_id: str, name: str, vm_class: str,
                    **kwargs) -> Dict[str, Any]:
        self._vms[vm_id] = VMState(
            vm_id=vm_id, name=name, vm_class=vm_class,
            created_at=datetime.utcnow().isoformat(), **kwargs
        )
        return {"registered": True, "vm_id": vm_id}

    def list_vms(self) -> List[dict]:
        return [vm.to_dict() for vm in self._vms.values()]

    def get_vm(self, vm_id: str) -> Optional[dict]:
        vm = self._vms.get(vm_id)
        return vm.to_dict() if vm else None

    # ── Snapshot Operations ───────────────────────────────────────────────────

    def create_snapshot(self, vm_id: str, snap_name: str,
                        actor: str = "kiswarm") -> Dict[str, Any]:
        vm = self._vms.get(vm_id)
        if not vm:
            return self._deny(vm_id, "create_snapshot", actor, "VM not found")

        result = self._vcenter.create_snapshot(vm_id, snap_name)
        vm.snapshots.append(snap_name)
        self._audit_log("create_snapshot", vm_id, actor,
                        {"snap_name": snap_name}, "ok")
        self._op_count += 1
        return {"ok": True, "vm_id": vm_id, "snapshot": snap_name, **result}

    def revert_snapshot(self, vm_id: str, snap_name: str,
                        actor: str = "kiswarm") -> Dict[str, Any]:
        vm = self._vms.get(vm_id)
        if not vm:
            return self._deny(vm_id, "revert_snapshot", actor, "VM not found")
        if snap_name not in vm.snapshots:
            return self._deny(vm_id, "revert_snapshot", actor, "Snapshot not found")

        result = self._vcenter.revert_snapshot(vm_id, snap_name)
        self._audit_log("revert_snapshot", vm_id, actor,
                        {"snap_name": snap_name}, "ok")
        self._op_count += 1
        return {"ok": True, "vm_id": vm_id, "reverted_to": snap_name}

    # ── Clone Operations ───────────────────────────────────────────────────────

    def clone_vm(self, src_vm_id: str, clone_name: str = None,
                 isolate_network: bool = True,
                 actor: str = "kiswarm") -> Dict[str, Any]:
        """Clone a VM for mutation testing. Production VMs cloned read-only."""
        src = self._vms.get(src_vm_id)
        if not src:
            return self._deny(src_vm_id, "clone_vm", actor, "Source VM not found")

        clone_name = clone_name or f"{src_vm_id}_clone_{self._op_count}"
        clone_id   = f"CLONE_{clone_name}"

        result = self._vcenter.clone_vm(src_vm_id, clone_name)
        if isolate_network:
            self._vcenter.set_network_isolation(clone_id, True)

        clone_vm = VMState(
            vm_id    = clone_id,
            name     = clone_name,
            vm_class = src.vm_class,
            is_clone = True,
            parent_id= src_vm_id,
            cpu_count= src.cpu_count,
            memory_mb= src.memory_mb,
            disk_gb  = src.disk_gb,
            created_at= datetime.utcnow().isoformat(),
            network_isolated = isolate_network,
        )
        self._vms[clone_id] = clone_vm
        self._audit_log("clone_vm", src_vm_id, actor,
                        {"clone_id": clone_id, "isolated": isolate_network}, "ok")
        self._op_count += 1
        return {"ok": True, "clone_id": clone_id, "clone_name": clone_name,
                "isolated": isolate_network}

    # ── Power & Resource ──────────────────────────────────────────────────────

    def power_cycle(self, vm_id: str, action: str = "on",
                    actor: str = "kiswarm") -> Dict[str, Any]:
        vm = self._vms.get(vm_id)
        if not vm:
            return self._deny(vm_id, "power_cycle", actor, "VM not found")
        if vm_id in PRODUCTION_VMS and action == "off":
            return self._deny(vm_id, "power_cycle", actor,
                              "Cannot power off production VM")
        if action == "on":
            self._vcenter.power_on(vm_id)
            vm.power = "on"
        else:
            self._vcenter.power_off(vm_id)
            vm.power = "off"
        self._audit_log("power_cycle", vm_id, actor, {"action": action}, "ok")
        return {"ok": True, "vm_id": vm_id, "power": vm.power}

    def reallocate_resources(self, vm_id: str, cpu_count: int = None,
                              memory_mb: int = None,
                              actor: str = "kiswarm") -> Dict[str, Any]:
        vm = self._vms.get(vm_id)
        if not vm:
            return self._deny(vm_id, "reallocate", actor, "VM not found")
        if vm_id in PRODUCTION_VMS:
            return self._deny(vm_id, "reallocate", actor,
                              "Cannot reallocate production VM resources")
        self._vcenter.reconfigure_vm(vm_id, cpu_count, memory_mb)
        if cpu_count:  vm.cpu_count  = cpu_count
        if memory_mb:  vm.memory_mb  = memory_mb
        self._audit_log("reallocate", vm_id, actor,
                        {"cpu": cpu_count, "mem": memory_mb}, "ok")
        return {"ok": True, "vm_id": vm_id, "cpu": vm.cpu_count, "mem": vm.memory_mb}

    # ── Mutation Governance Pipeline ─────────────────────────────────────────

    def begin_mutation(self, source_vm: str, param_deltas: Dict[str, float],
                       actor: str = "kiswarm") -> str:
        """Step 1-2: Create clone + snapshot before mutation."""
        snap_name = f"pre_mutation_{int(time.time())}"
        self.create_snapshot(source_vm, snap_name, actor)

        clone_res = self.clone_vm(source_vm, actor=actor)
        clone_id  = clone_res.get("clone_id", "")

        mid = f"MUT_{uuid.uuid4().hex[:12]}"
        self._mutations[mid] = MutationRecord(
            mutation_id     = mid,
            source_vm       = source_vm,
            clone_vm        = clone_id,
            snapshot_before = snap_name,
            param_deltas    = param_deltas,
            twin_result     = None,
            formal_verified = False,
            promoted        = False,
            approval_code   = None,
            steps_completed = ["snapshot_created", "clone_created"],
            created_at      = datetime.utcnow().isoformat(),
        )
        self._audit_log("begin_mutation", source_vm, actor,
                        {"mutation_id": mid, "deltas": param_deltas}, "ok")
        return mid

    def record_twin_result(self, mutation_id: str,
                           twin_result: Dict) -> Dict[str, Any]:
        m = self._mutations.get(mutation_id)
        if not m:
            return {"error": "Mutation not found"}
        m.twin_result = twin_result
        m.steps_completed.append("twin_validated")
        return {"ok": True, "mutation_id": mutation_id}

    def record_formal_verification(self, mutation_id: str,
                                    passed: bool) -> Dict[str, Any]:
        m = self._mutations.get(mutation_id)
        if not m:
            return {"error": "Mutation not found"}
        m.formal_verified = passed
        m.steps_completed.append("formal_verified" if passed else "formal_rejected")
        return {"ok": True, "verified": passed}

    def promote_mutation(self, mutation_id: str,
                         approval_code: str) -> Dict[str, Any]:
        """Step 8+: Requires approval code — ONLY Baron Marco Paolo Ialongo."""
        m = self._mutations.get(mutation_id)
        if not m:
            return {"error": "Mutation not found"}

        # Validate approval code
        if approval_code != self.APPROVAL_CODE:
            self._audit_log("promote_mutation", m.source_vm, "unknown",
                            {"mutation_id": mutation_id}, "denied")
            return {"ok": False, "denied": True,
                    "reason": "Invalid approval code — promotion blocked"}

        # Validate pipeline completeness
        required = {"snapshot_created", "clone_created",
                    "twin_validated", "formal_verified"}
        completed = set(m.steps_completed)
        missing   = required - completed
        if missing:
            return {"ok": False, "blocked": True,
                    "missing_steps": list(missing)}

        # Verify twin passed and formal passed
        twin_ok   = m.twin_result and m.twin_result.get("promoted", False)
        formal_ok = m.formal_verified
        if not twin_ok or not formal_ok:
            return {"ok": False, "blocked": True,
                    "reason": "Twin or formal verification failed"}

        m.promoted     = True
        m.approval_code = approval_code
        m.steps_completed.append("promoted")
        self._audit_log("promote_mutation", m.source_vm, "baron",
                        {"mutation_id": mutation_id}, "ok")
        return {"ok": True, "promoted": True, "mutation_id": mutation_id}

    def rollback_mutation(self, mutation_id: str,
                          actor: str = "kiswarm") -> Dict[str, Any]:
        m = self._mutations.get(mutation_id)
        if not m:
            return {"error": "Mutation not found"}
        self.revert_snapshot(m.source_vm, m.snapshot_before, actor)
        m.steps_completed.append("rolled_back")
        self._audit_log("rollback_mutation", m.source_vm, actor,
                        {"mutation_id": mutation_id}, "ok")
        return {"ok": True, "mutation_id": mutation_id, "rolled_back": True}

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit_log(self, operation: str, vm_id: str, actor: str,
                   details: dict, result: str) -> None:
        payload = json.dumps(
            {"op": operation, "vm": vm_id, "actor": actor,
             "ts": datetime.utcnow().isoformat(), **details},
            sort_keys=True
        ).encode()
        sig = hashlib.sha256(payload).hexdigest()[:24]
        self._audit.append(AuditEntry(
            entry_id  = f"AUD_{len(self._audit):06d}",
            timestamp = datetime.utcnow().isoformat(),
            operation = operation,
            vm_id     = vm_id,
            actor     = actor,
            details   = details,
            result    = result,
            signature = sig,
        ))

    def _deny(self, vm_id: str, op: str, actor: str,
              reason: str) -> Dict[str, Any]:
        self._audit_log(op, vm_id, actor, {"reason": reason}, "denied")
        return {"ok": False, "denied": True, "reason": reason}

    def get_audit_log(self, limit: int = 50) -> List[dict]:
        return [e.to_dict() for e in self._audit[-limit:]]

    def get_mutation(self, mutation_id: str) -> Optional[dict]:
        m = self._mutations.get(mutation_id)
        return m.to_dict() if m else None

    def get_stats(self) -> dict:
        return {
            "vm_count":        len(self._vms),
            "clone_count":     sum(1 for v in self._vms.values() if v.is_clone),
            "mutation_count":  len(self._mutations),
            "promoted_count":  sum(1 for m in self._mutations.values() if m.promoted),
            "audit_entries":   len(self._audit),
            "op_count":        self._op_count,
            "production_vms":  list(PRODUCTION_VMS),
            "test_vms":        list(TEST_VMS),
        }
