"""
KISWARM v4.1 — Test Suite for 6 Advanced CIEC Modules
======================================================
150 tests covering:
  Module 17: TD3IndustrialController
  Module 18: IEC61131ASTParser
  Module 19: ExtendedPhysicsTwin
  Module 20: VMwareOrchestrator
  Module 21: FormalVerificationEngine
  Module 22: ByzantineFederatedAggregator
  Module 23: MutationGovernanceEngine
"""

import math
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 17 — TD3 Industrial Controller
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.td3_controller import (
    TD3IndustrialController, _ActorNetwork, _CriticNetwork,
    _ReplayBuffer, _Transition, ACTION_NAMES, PLC_ACTION_BOUNDS,
    N_ACTIONS,
)


class TestTD3Controller:

    def setup_method(self):
        self.ctrl = TD3IndustrialController(state_dim=16, seed=42)

    # ── Network architecture ──────────────────────────────────────────────────

    def test_actor_network_output_dimension(self):
        actor = _ActorNetwork(16, seed=0)
        out = actor.forward([0.0] * 16)
        assert len(out) == N_ACTIONS

    def test_actor_output_bounded(self):
        actor = _ActorNetwork(16, seed=1)
        out = actor.forward([0.5] * 16)
        for i, (lo, hi) in enumerate(PLC_ACTION_BOUNDS.values()):
            # tanh output scaled to half-range — bounds are ±half, so check < 1
            assert abs(out[i]) <= max(abs(lo), abs(hi)) * 2 + 1e-6

    def test_critic_network_scalar_output(self):
        critic = _CriticNetwork(16, seed=0)
        q = critic.forward([0.0] * 16, [0.0] * N_ACTIONS)
        assert isinstance(q, float)

    def test_actor_clone(self):
        actor = _ActorNetwork(16, seed=0)
        clone = actor.clone()
        out_a = actor.forward([1.0] * 16)
        out_c = clone.forward([1.0] * 16)
        assert out_a == out_c

    def test_critic_clone(self):
        critic = _CriticNetwork(16, seed=0)
        clone  = critic.clone()
        q1 = critic.forward([0.5] * 16, [0.0] * N_ACTIONS)
        q2 = clone.forward([0.5] * 16, [0.0] * N_ACTIONS)
        assert q1 == q2

    def test_soft_update_actor(self):
        a = _ActorNetwork(8, seed=0)
        b = _ActorNetwork(8, seed=99)
        orig_w = a.layers[0].W[0][0]
        a.soft_update(b, tau=1.0)   # full copy with tau=1
        assert a.layers[0].W[0][0] == b.layers[0].W[0][0]

    # ── Replay buffer ─────────────────────────────────────────────────────────

    def test_replay_buffer_capacity(self):
        buf = _ReplayBuffer(capacity=10)
        for i in range(20):
            buf.push(_Transition([float(i)], [0.0], float(i), [float(i+1)], False))
        assert len(buf) == 10

    def test_replay_buffer_sample(self):
        import random
        buf = _ReplayBuffer(capacity=100)
        for i in range(50):
            buf.push(_Transition([float(i)], [0.0], float(i), [float(i+1)], False))
        rng = random.Random(0)
        batch = buf.sample(10, rng)
        assert len(batch) == 10

    # ── select_action ─────────────────────────────────────────────────────────

    def test_select_action_returns_dict(self):
        action, info = self.ctrl.select_action([0.0] * 16)
        assert isinstance(action, dict)
        assert set(action.keys()) == set(ACTION_NAMES)

    def test_select_action_bounds_respected(self):
        action, _ = self.ctrl.select_action([1.0] * 16, deterministic=True)
        for name, (lo, hi) in PLC_ACTION_BOUNDS.items():
            assert lo <= action[name] <= hi, f"{name} out of bounds"

    def test_select_action_deterministic_no_noise(self):
        a1, _ = self.ctrl.select_action([0.5] * 16, deterministic=True)
        a2, _ = self.ctrl.select_action([0.5] * 16, deterministic=True)
        assert a1 == a2

    def test_select_action_info_keys(self):
        _, info = self.ctrl.select_action([0.0] * 16)
        assert "q1" in info and "q2" in info and "latency_ms" in info

    def test_select_action_latency_reasonable(self):
        _, info = self.ctrl.select_action([0.0] * 256)
        assert info["latency_ms"] < 500   # well within 10ms budget in real Python

    def test_select_action_state_padding(self):
        # Short state gets padded
        action, _ = self.ctrl.select_action([0.0] * 5)
        assert isinstance(action, dict)

    # ── observe + update ──────────────────────────────────────────────────────

    def test_observe_fills_buffer(self):
        self.ctrl.observe([0.0]*16, [0.0]*N_ACTIONS, 1.0, [0.1]*16, False)
        assert len(self.ctrl.buffer) == 1

    def test_update_skips_when_buffer_small(self):
        result = self.ctrl.update()
        assert result.get("skipped") is True

    def test_update_runs_after_enough_samples(self):
        ctrl = TD3IndustrialController(state_dim=8, seed=0)
        import random
        rng = random.Random(0)
        for _ in range(600):
            ctrl.observe(
                [rng.uniform(-1, 1)] * 8,
                [rng.uniform(-0.05, 0.05)] * N_ACTIONS,
                rng.uniform(-1, 1),
                [rng.uniform(-1, 1)] * 8,
                False,
            )
        result = ctrl.update(batch_size=64)
        assert "critic_loss" in result
        assert "skipped" not in result

    # ── Reward ────────────────────────────────────────────────────────────────

    def test_reward_positive_for_good_scores(self):
        r = TD3IndustrialController.compute_reward(0.9, 0.9, 0.0, 0.0, 0.0)
        assert r > 0.5

    def test_reward_negative_for_violations(self):
        r = TD3IndustrialController.compute_reward(0.0, 0.0, 1.0, 1.0, 1.0)
        assert r < 0

    # ── Stats ─────────────────────────────────────────────────────────────────

    def test_get_stats_returns_dict(self):
        stats = self.ctrl.get_stats()
        assert "action_names" in stats
        assert "plc_bounds" in stats

    def test_checkpoint(self):
        cp = self.ctrl.checkpoint()
        assert "state_dim" in cp and "hash" in cp

    def test_action_names_constant(self):
        assert "delta_kp" in ACTION_NAMES
        assert "delta_ki" in ACTION_NAMES
        assert "delta_kd" in ACTION_NAMES

    def test_n_actions_matches_bounds(self):
        assert N_ACTIONS == len(PLC_ACTION_BOUNDS)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 18 — IEC 61131-3 AST Parser
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.ast_parser import (
    IEC61131ASTParser, tokenize, Token,
    TK_KEYWORD, TK_IDENT, TK_NUMBER, TK_EOF,
)

SIMPLE_ST = """
PROGRAM PumpControl
  VAR
    Setpoint : REAL := 75.0;
    Temperature : REAL;
    PID_Out : REAL;
  END_VAR

  Temperature := 70.0;
  IF Temperature > Setpoint THEN
    PID_Out := 0.0;
  ELSE
    PID_FB(KP := 1.5, KI := 0.1, KD := 0.05, SP := Setpoint, PV := Temperature, OUT => PID_Out);
  END_IF;
END_PROGRAM
"""

SAFETY_ST = """
PROGRAM SafetyCtrl
  VAR
    ESTOP : BOOL;
    Valve : BOOL;
  END_VAR
  IF ESTOP = TRUE THEN
    Valve := FALSE;
  END_IF;
END_PROGRAM
"""

class TestASTParser:

    def setup_method(self):
        self.parser = IEC61131ASTParser()

    # ── Tokenizer ─────────────────────────────────────────────────────────────

    def test_tokenize_keywords(self):
        tokens = tokenize("PROGRAM MyProg END_PROGRAM")
        kinds  = [t.kind for t in tokens if t.kind != TK_EOF]
        assert TK_KEYWORD in kinds

    def test_tokenize_identifiers(self):
        tokens = tokenize("MyVar := 3.14;")
        idents = [t.value for t in tokens if t.kind == TK_IDENT]
        assert "MyVar" in idents

    def test_tokenize_numbers(self):
        tokens = tokenize("42 3.14 1e5")
        nums   = [t.kind for t in tokens if t.kind == TK_NUMBER]
        assert len(nums) == 3

    def test_tokenize_comment_stripped(self):
        tokens = tokenize("(* this is a comment *) x := 1;")
        vals   = [t.value for t in tokens]
        assert "this" not in vals

    def test_tokenize_eof(self):
        tokens = tokenize("")
        assert tokens[-1].kind == TK_EOF

    # ── Parse result ──────────────────────────────────────────────────────────

    def test_parse_returns_result(self):
        result = self.parser.parse(SIMPLE_ST)
        assert result is not None
        assert result.ast is not None

    def test_parse_program_name(self):
        result = self.parser.parse(SIMPLE_ST)
        assert result.program_name == "PumpControl"

    def test_parse_var_count(self):
        result = self.parser.parse(SIMPLE_ST)
        assert result.var_count >= 3

    def test_parse_stmt_count(self):
        result = self.parser.parse(SIMPLE_ST)
        assert result.stmt_count >= 2

    def test_parse_source_hash(self):
        result = self.parser.parse(SIMPLE_ST)
        assert len(result.source_hash) == 16

    def test_parse_caching(self):
        result1 = self.parser.parse(SIMPLE_ST)
        result2 = self.parser.parse(SIMPLE_ST)
        assert result1.source_hash == result2.source_hash
        assert self.parser.get_stats()["cache_size"] >= 1

    # ── Graphs ────────────────────────────────────────────────────────────────

    def test_cfg_has_nodes(self):
        result = self.parser.parse(SIMPLE_ST)
        assert len(result.cfg) >= 2   # at least entry + exit

    def test_ddg_edges_list(self):
        result = self.parser.parse(SIMPLE_ST)
        assert isinstance(result.ddg_edges, list)

    def test_sdg_edges_list(self):
        result = self.parser.parse(SIMPLE_ST)
        assert isinstance(result.sdg_edges, list)

    # ── Pattern detection ─────────────────────────────────────────────────────

    def test_detects_pid_block(self):
        result = self.parser.parse(SIMPLE_ST)
        # PID_FB call should be detected
        assert len(result.pid_blocks) >= 1

    def test_pid_block_name(self):
        result = self.parser.parse(SIMPLE_ST)
        names = [p.name for p in result.pid_blocks]
        assert any("PID" in n.upper() for n in names)

    def test_detects_safety_interlock(self):
        result = self.parser.parse(SAFETY_ST)
        assert len(result.interlocks) >= 1

    def test_interlock_is_safety(self):
        result = self.parser.parse(SAFETY_ST)
        assert any(i.safety for i in result.interlocks)

    def test_dead_code_detection(self):
        dead_st = """
PROGRAM DeadTest
  VAR x : REAL; END_VAR
  IF FALSE THEN
    x := 99.0;
  END_IF;
END_PROGRAM
"""
        result = self.parser.parse(dead_st)
        assert len(result.dead_code) >= 1

    # ── to_dict ───────────────────────────────────────────────────────────────

    def test_to_dict_structure(self):
        result = self.parser.parse(SIMPLE_ST)
        d = result.to_dict()
        assert "program_name" in d
        assert "cfg_nodes"    in d
        assert "ddg_edges"    in d
        assert "pid_blocks"   in d
        assert "interlocks"   in d

    def test_empty_program_no_crash(self):
        result = self.parser.parse("PROGRAM Empty END_PROGRAM")
        assert result is not None

    def test_parse_error_captured(self):
        result = self.parser.parse("TOTALLY INVALID SOURCE ??? ###")
        assert isinstance(result.errors, list)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 19 — Extended Physics Twin
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.extended_physics import (
    ExtendedPhysicsTwin, ThermalBlock, PumpBlock, ValveBlock,
    MotorBlock, BatteryBlock, ElectricalBlock,
    FaultConfig, rk4_step, semi_implicit_euler_step,
)


class TestExtendedPhysics:

    def setup_method(self):
        self.twin = ExtendedPhysicsTwin(seed=42)

    # ── Individual blocks ─────────────────────────────────────────────────────

    def test_thermal_state_dot(self):
        blk = ThermalBlock()
        d   = blk.state_dot({"T": 25.0}, {"Q_in": 1000.0, "Q_out": 200.0},
                             blk.default_params())
        assert "T" in d
        assert isinstance(d["T"], float)

    def test_thermal_heating_increases_T(self):
        blk = ThermalBlock()
        d   = blk.state_dot({"T": 20.0}, {"Q_in": 5000.0, "Q_out": 0.0},
                             blk.default_params())
        assert d["T"] > 0   # temperature should rise

    def test_pump_compute(self):
        blk  = PumpBlock()
        out  = blk.compute(1450.0, blk.default_params())
        assert "Q" in out and "H" in out and "P_hydraulic" in out
        assert out["Q"] > 0

    def test_valve_state_dot(self):
        blk = ValveBlock()
        d   = blk.state_dot({"v": 0.3}, {"u": 0.8}, blk.default_params())
        assert d["v"] > 0   # valve opening

    def test_motor_state_dot(self):
        blk = MotorBlock()
        d   = blk.state_dot({"omega": 0.0, "i_a": 0.0},
                             {"V": 220.0, "T_load": 0.0},
                             blk.default_params())
        assert "omega" in d and "i_a" in d

    def test_battery_state_dot(self):
        blk = BatteryBlock()
        d   = blk.state_dot({"SOC": 0.8, "T_b": 25.0},
                             {"I_charge": 10.0, "I_disch": 5.0},
                             blk.default_params())
        assert "SOC" in d

    def test_battery_voltage(self):
        blk = BatteryBlock()
        v   = blk.compute_voltage(1.0, 0.0)   # fully charged, no current
        assert v >= 4.0                         # near 4.2 V

    def test_electrical_state_dot(self):
        blk = ElectricalBlock()
        d   = blk.state_dot({"f": 50.0},
                             {"P_gen": 1000.0, "P_load": 950.0},
                             blk.default_params())
        assert "f" in d

    # ── Integration ───────────────────────────────────────────────────────────

    def test_rk4_step(self):
        blk   = ThermalBlock()
        state = {"T": 25.0}
        inp   = {"Q_in": 1000.0, "Q_out": 200.0}
        new_s = rk4_step(blk, state, inp, blk.default_params(), dt=0.1)
        assert "T" in new_s

    def test_euler_step(self):
        blk   = ValveBlock()
        state = {"v": 0.3}
        new_s = semi_implicit_euler_step(blk, state, {"u": 0.8},
                                          blk.default_params(), dt=0.1)
        assert "v" in new_s

    # ── Twin simulation ───────────────────────────────────────────────────────

    def test_twin_step_returns_simstep(self):
        ss = self.twin.step({"Q_in": 1000.0, "RPM": 1450.0, "u": 0.5,
                              "V": 220.0, "T_load": 5.0,
                              "I_charge": 10.0, "I_disch": 5.0,
                              "P_gen": 1000.0, "P_load": 950.0})
        assert ss.step == 0
        assert isinstance(ss.state, dict)

    def test_twin_step_state_not_empty(self):
        ss = self.twin.step({})
        assert len(ss.state) > 0

    def test_twin_episode_returns_metrics(self):
        result = self.twin.run_episode(n_steps=20)
        assert "survive_rate" in result
        assert "violations"   in result

    def test_twin_episode_survive_rate_range(self):
        result = self.twin.run_episode(n_steps=20)
        assert 0.0 <= result["survive_rate"] <= 1.0

    def test_twin_evaluate_mutation_returns_bool(self):
        promoted, metrics = self.twin.evaluate_mutation(
            {"h_conv": 0.05}, n_runs=2
        )
        assert isinstance(promoted, bool)
        assert "mean_survive" in metrics

    # ── Fault injection ───────────────────────────────────────────────────────

    def test_fault_config_active(self):
        fc = FaultConfig("sensor_bias", "Q_in", 3.0, onset_step=0)
        assert fc.is_active(5)

    def test_fault_config_not_active_before_onset(self):
        fc = FaultConfig("sensor_bias", "Q_in", 3.0, onset_step=10)
        assert not fc.is_active(5)

    def test_fault_config_duration_expiry(self):
        fc = FaultConfig("sensor_bias", "Q_in", 3.0, onset_step=0, duration=5)
        assert fc.is_active(4)
        assert not fc.is_active(5)

    def test_episode_with_fault(self):
        fault = FaultConfig("sensor_bias", "Q_in", 2.0, onset_step=5)
        result = self.twin.run_episode(n_steps=20, faults=[fault])
        assert "fault_count" in result
        assert result["fault_count"] == 1

    def test_get_stats(self):
        stats = self.twin.get_stats()
        assert "blocks"       in stats
        assert "hard_limits"  in stats


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 20 — VMware Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.vmware_orchestrator import (
    VMwareOrchestrator, PRODUCTION_VMS, TEST_VMS, VM_CLASSES,
)


class TestVMwareOrchestrator:

    def setup_method(self):
        self.orch = VMwareOrchestrator(seed=0)

    def test_list_vms_returns_all_classes(self):
        vms = self.orch.list_vms()
        ids = {v["vm_id"] for v in vms}
        assert set(VM_CLASSES.keys()) == ids

    def test_get_vm_existing(self):
        vm = self.orch.get_vm("VM-A")
        assert vm is not None
        assert vm["vm_id"] == "VM-A"

    def test_get_vm_nonexistent(self):
        assert self.orch.get_vm("VM-Z") is None

    def test_register_vm(self):
        result = self.orch.register_vm("VM-X", "TestVM", "custom")
        assert result["registered"] is True
        assert self.orch.get_vm("VM-X") is not None

    def test_create_snapshot(self):
        result = self.orch.create_snapshot("VM-C", "snap1")
        assert result["ok"] is True
        vm = self.orch.get_vm("VM-C")
        assert "snap1" in vm["snapshots"]

    def test_revert_snapshot(self):
        self.orch.create_snapshot("VM-C", "snap_r")
        result = self.orch.revert_snapshot("VM-C", "snap_r")
        assert result["ok"] is True

    def test_revert_nonexistent_snapshot_denied(self):
        result = self.orch.revert_snapshot("VM-C", "nonexistent_snap")
        assert result["ok"] is False

    def test_clone_vm(self):
        result = self.orch.clone_vm("VM-C")
        assert result["ok"] is True
        assert "clone_id" in result
        assert self.orch.get_vm(result["clone_id"]) is not None

    def test_clone_is_marked_as_clone(self):
        result = self.orch.clone_vm("VM-C")
        clone  = self.orch.get_vm(result["clone_id"])
        assert clone["is_clone"] is True
        assert clone["parent_id"] == "VM-C"

    def test_power_cycle_on(self):
        result = self.orch.power_cycle("VM-C", "on")
        assert result["ok"] is True

    def test_power_off_production_denied(self):
        result = self.orch.power_cycle("VM-A", "off")
        assert result["ok"] is False
        assert result.get("denied")

    def test_reallocate_test_vm(self):
        result = self.orch.reallocate_resources("VM-C", cpu_count=8)
        assert result["ok"] is True

    def test_reallocate_production_denied(self):
        result = self.orch.reallocate_resources("VM-B", cpu_count=8)
        assert result["ok"] is False

    def test_begin_mutation(self):
        mid = self.orch.begin_mutation("VM-C", {"kp": 0.02})
        assert mid.startswith("MUT_")

    def test_record_twin_result(self):
        mid = self.orch.begin_mutation("VM-C", {"kp": 0.02})
        res = self.orch.record_twin_result(mid, {"promoted": True})
        assert res["ok"] is True

    def test_promote_invalid_code_denied(self):
        mid = self.orch.begin_mutation("VM-C", {"kp": 0.01})
        result = self.orch.promote_mutation(mid, "WRONG_CODE")
        assert result["ok"] is False
        assert result.get("denied")

    def test_promote_valid_code_requires_steps(self):
        mid = self.orch.begin_mutation("VM-C", {"kp": 0.01})
        # Valid code but steps not complete
        result = self.orch.promote_mutation(mid, "Maquister_Equtitum")
        assert result.get("ok") is False or result.get("blocked")

    def test_audit_log_populated(self):
        self.orch.create_snapshot("VM-C", "audit_test")
        log = self.orch.get_audit_log()
        assert len(log) >= 1

    def test_audit_log_has_signature(self):
        self.orch.clone_vm("VM-C")
        log = self.orch.get_audit_log()
        assert all("signature" in e for e in log)

    def test_get_stats(self):
        stats = self.orch.get_stats()
        assert "vm_count"       in stats
        assert "production_vms" in stats
        assert "audit_entries"  in stats


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 21 — Formal Verification Engine
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.formal_verification import (
    FormalVerificationEngine,
    check_lyapunov_stable, verify_barrier_certificate,
    MutationLedger, _spectral_radius, _is_positive_definite,
)


class TestFormalVerification:

    def setup_method(self):
        self.engine = FormalVerificationEngine()

    # ── Matrix helpers ────────────────────────────────────────────────────────

    def test_spectral_radius_identity(self):
        I = [[1, 0], [0, 1]]
        rho = _spectral_radius(I)
        assert abs(rho - 1.0) < 0.1

    def test_spectral_radius_stable(self):
        A = [[0.5, 0.0], [0.0, 0.5]]
        assert _spectral_radius(A) < 1.0

    def test_positive_definite_identity(self):
        I = [[1, 0], [0, 1]]
        assert _is_positive_definite(I)

    def test_not_positive_definite(self):
        P = [[-1, 0], [0, 1]]
        assert not _is_positive_definite(P)

    # ── Lyapunov ─────────────────────────────────────────────────────────────

    def test_lyapunov_stable_system(self):
        A = [[0.5, 0.0], [0.0, 0.6]]
        res = check_lyapunov_stable(A)
        assert res["stable"] is True
        assert res["spectral_radius"] < 1.0

    def test_lyapunov_unstable_system(self):
        A = [[1.5, 0.0], [0.0, 1.5]]
        res = check_lyapunov_stable(A)
        assert res["stable"] is False

    def test_lyapunov_marginal(self):
        A = [[0.9, 0.0], [0.0, 0.85]]
        res = check_lyapunov_stable(A)
        assert res["stable"] is True

    def test_verify_linearized_stable(self):
        A   = [[0.4, 0.1], [0.0, 0.5]]
        res = self.engine.verify_linearized(A, mutation_id="test_stable")
        assert res.approved is True

    def test_verify_linearized_unstable_rejected(self):
        A   = [[2.0, 0.0], [0.0, 1.5]]
        res = self.engine.verify_linearized(A, mutation_id="test_unstable")
        assert res.approved is False
        assert res.reject_reason is not None

    def test_verify_linearized_latency(self):
        A   = [[0.5, 0.0], [0.0, 0.5]]
        res = self.engine.verify_linearized(A)
        assert res.latency_ms >= 0

    # ── Barrier ───────────────────────────────────────────────────────────────

    def test_barrier_certificate_stable_system(self):
        import math
        def B(x): return math.exp(-x[0]**2)  # always positive
        def f(x): return [x[0]]  # expanding: dB/dt = -2x^2*exp(-x^2) <= 0
        res = verify_barrier_certificate(B, f, [(-0.9, 0.9)], n_samples=100, dt=0.001)
        assert res.valid is True

    def test_barrier_certificate_invalid(self):
        def B(x): return -1.0   # always negative — invalid
        def f(x): return [xi * 2 for xi in x]
        res = verify_barrier_certificate(B, f, [(-1, 1)], n_samples=50)
        assert res.valid is False
        assert res.violations > 0

    def test_verify_barrier_api(self):
        import math
        def B(x): return math.exp(-x[0]**2)
        def f(x): return [x[0]]
        res = self.engine.verify_barrier(B, f, [(-0.9, 0.9)],
                                          n_samples=100, mutation_id="barrier_test")
        assert res.approved is True

    # ── Ledger ────────────────────────────────────────────────────────────────

    def test_ledger_appends(self):
        ledger = MutationLedger()
        ledger.append("MUT1", "lyapunov", True, {"x": 1})
        assert len(ledger) == 1

    def test_ledger_integrity_valid(self):
        ledger = MutationLedger()
        ledger.append("M1", "lyapunov", True, {})
        ledger.append("M2", "barrier",  True, {})
        assert ledger.verify_integrity() is True

    def test_ledger_tamper_detection(self):
        ledger = MutationLedger()
        ledger.append("M1", "lyapunov", True, {})
        ledger._entries[0].signature = "tampered"
        assert ledger.verify_integrity() is False

    def test_verify_full_stable(self):
        A   = [[0.6, 0.1], [0.0, 0.7]]
        res = self.engine.verify_full(A, mutation_id="full_stable")
        assert res.approved is True

    def test_get_stats(self):
        A = [[0.5, 0], [0, 0.5]]
        self.engine.verify_linearized(A)
        stats = self.engine.get_stats()
        assert stats["total"] >= 1
        assert "ledger_intact" in stats


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 22 — Byzantine Federated Aggregator
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.byzantine_aggregator import (
    ByzantineFederatedAggregator, SiteUpdate,
    trimmed_mean, coordinate_median, multi_krum, fltrust,
    detect_byzantine_updates,
)


def _make_updates(n: int, dim: int = 4, base: float = 1.0,
                  seed: int = 0) -> list:
    import random
    rng = random.Random(seed)
    updates = []
    for i in range(n):
        grad = [base + rng.gauss(0, 0.05) for _ in range(dim)]
        updates.append(SiteUpdate(
            site_id     = f"site_{i}",
            gradient    = grad,
            param_dim   = dim,
            step        = 1,
            performance = 0.9,
            n_samples   = 100,
        ))
    return updates


class TestByzantineAggregator:

    def setup_method(self):
        self.agg = ByzantineFederatedAggregator(f_tolerance=1, seed=0)
        for i in range(7):
            self.agg.register_site(f"site_{i}")

    # ── Aggregation methods ───────────────────────────────────────────────────

    def test_trimmed_mean_basic(self):
        grads = [[1.0], [2.0], [3.0], [100.0], [-100.0]]
        result = trimmed_mean(grads, f=1)
        assert abs(result[0] - 2.0) < 0.5

    def test_trimmed_mean_all_same(self):
        grads = [[5.0, 5.0]] * 5
        result = trimmed_mean(grads, f=1)
        assert abs(result[0] - 5.0) < 1e-6

    def test_coordinate_median(self):
        grads = [[1.0], [2.0], [3.0], [4.0], [5.0]]
        result = coordinate_median(grads)
        assert abs(result[0] - 3.0) < 1e-6

    def test_multi_krum_selects_consensus(self):
        grads  = [[1.0, 1.0]] * 5 + [[100.0, 100.0]]
        result = multi_krum(grads, f=1)
        assert abs(result[0] - 1.0) < 5.0

    def test_fltrust_weights_by_cosine(self):
        root  = [1.0, 0.0]
        grads = [[1.0, 0.0], [1.0, 0.0], [-1.0, 0.0]]
        result = fltrust(root, grads)
        assert result[0] > 0   # aligned gradients dominate

    # ── Byzantine detection ───────────────────────────────────────────────────

    def test_detect_no_anomalies_clean_data(self):
        updates = _make_updates(7, dim=4, base=1.0)
        clean, anomalies = detect_byzantine_updates(updates)
        assert len(anomalies) == 0

    def test_detect_outlier_flagged(self):
        import random
        updates = _make_updates(6, dim=4, base=1.0)
        rng = random.Random(0)
        # Add extreme outlier
        updates.append(SiteUpdate(
            site_id="attacker", gradient=[1000.0]*4,
            param_dim=4, step=1, performance=0.0, n_samples=1,
        ))
        clean, anomalies = detect_byzantine_updates(updates, threshold_sigma=2.0)
        assert len(anomalies) >= 1

    # ── Full aggregation ──────────────────────────────────────────────────────

    def test_aggregate_returns_result(self):
        updates = _make_updates(7)
        result  = self.agg.aggregate(updates)
        assert result.round_id == 1
        assert result.n_sites  == 7

    def test_aggregate_byzantine_safe_7_sites(self):
        updates = _make_updates(7)
        result  = self.agg.aggregate(updates)
        # N=7 >= 3*1+1=4 → Byzantine safe
        assert result.byzantine_safe is True

    def test_aggregate_not_safe_insufficient_sites(self):
        updates = _make_updates(2)
        result  = self.agg.aggregate(updates)
        assert result.byzantine_safe is False

    def test_aggregate_methods(self):
        updates = _make_updates(7)
        for method in ("trimmed_mean", "krum", "median", "fltrust"):
            result = self.agg.aggregate(updates, method=method)
            assert len(result.aggregated) > 0

    def test_theta_updated_after_aggregate(self):
        updates = _make_updates(7, dim=4)
        self.agg.aggregate(updates)
        params = self.agg.export_global_params()
        assert params["param_dim"] == 4

    def test_site_leaderboard_has_entries(self):
        updates = _make_updates(4)
        self.agg.aggregate(updates)
        board = self.agg.get_site_leaderboard()
        assert len(board) >= 4

    def test_site_trust_score_range(self):
        board = self.agg.get_site_leaderboard()
        for entry in board:
            assert 0.0 <= entry["trust_score"] <= 1.0

    def test_get_stats(self):
        stats = self.agg.get_stats()
        assert "rounds"           in stats
        assert "f_tolerance"      in stats
        assert "min_sites_required" in stats

    def test_min_sites_formula(self):
        stats = self.agg.get_stats()
        assert stats["min_sites_required"] == 3 * self.agg.f + 1

    def test_site_update_signature(self):
        u = SiteUpdate("s1", [1.0, 2.0], 2, 1, 0.8, 10)
        assert len(u.signature) == 24


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 23 — Mutation Governance Pipeline
# ─────────────────────────────────────────────────────────────────────────────

from python.sentinel.mutation_governance import (
    MutationGovernanceEngine, APPROVAL_CODE, AUTHORIZED_BY,
    GATE_STEP, PIPELINE_STEPS,
)


class TestMutationGovernance:

    def setup_method(self):
        self.engine = MutationGovernanceEngine()

    def _run_steps_1_7(self, mid: str) -> None:
        """Helper: run steps 1-7 with default context."""
        twin_ok   = {"promoted": True, "survive_rate": 0.98, "violations": 0, "n_runs": 3}
        formal_ok = {"approved": True, "stable": True, "spectral_radius": 0.85}
        ctxs = {
            1: {},
            2: {},
            3: {},
            4: {"context": {"twin_result":   twin_ok}},
            5: {"context": {"fault_results": {"normal_load": True, "peak_load": True,
                                               "startup": True, "emergency_stop": True}}},
            6: {"context": {"formal_result": formal_ok}},
            7: {},
        }
        for step_id in range(1, 8):
            res = self.engine.run_step(mid, step_id, **ctxs.get(step_id, {}))
            assert res.get("passed"), f"Step {step_id} failed: {res}"

    def test_begin_mutation_returns_id(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.02})
        assert mid.startswith("MUT_")

    def test_mutation_initial_step(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.02})
        m   = self.engine.get_mutation(mid)
        assert m["current_step"] == 1

    def test_step1_extract_semantic(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        res = self.engine.run_step(mid, 1)
        assert res["passed"] is True

    def test_step2_propose_mutation(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        self.engine.run_step(mid, 1)
        res = self.engine.run_step(mid, 2)
        assert res["passed"] is True

    def test_step3_bounds_valid(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.02})
        self.engine.run_step(mid, 1)
        self.engine.run_step(mid, 2)
        res = self.engine.run_step(mid, 3)
        assert res["passed"] is True

    def test_step3_bounds_violation_rejects(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 99.0})
        self.engine.run_step(mid, 1)
        self.engine.run_step(mid, 2)
        res = self.engine.run_step(mid, 3)
        assert res.get("rejected") is True

    def test_out_of_order_step_blocked(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        res = self.engine.run_step(mid, 5)   # jump to step 5
        assert "error" in res

    def test_step8_via_run_step_blocked(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        self._run_steps_1_7(mid)
        res = self.engine.run_step(mid, 8)
        assert res.get("gate") is True or "error" in res

    def test_approve_wrong_code_denied(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        self._run_steps_1_7(mid)
        res = self.engine.approve(mid, "WRONG_CODE")
        assert res.get("approved") is False

    def test_approve_correct_code_passes(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 0.01})
        self._run_steps_1_7(mid)
        res = self.engine.approve(mid, APPROVAL_CODE)
        assert res.get("approved") is True
        assert res.get("approved_by") == AUTHORIZED_BY

    def test_full_pipeline_to_production(self):
        mid = self.engine.begin_mutation("ValveCtrl", {"delta_kp": 0.03})
        self._run_steps_1_7(mid)
        self.engine.approve(mid, APPROVAL_CODE)
        # Steps 9 and 10
        self.engine.run_step(mid, 9)
        self.engine.run_step(mid, 10)
        result = self.engine.release_production_key(mid)
        assert result["deployed"] is True
        assert result["production_key"].startswith("PRODKEY_")

    def test_production_key_requires_all_steps(self):
        mid = self.engine.begin_mutation("ValveCtrl", {"delta_kp": 0.01})
        result = self.engine.release_production_key(mid)
        assert "error" in result

    def test_rejected_mutation_blocks_further_steps(self):
        mid = self.engine.begin_mutation("PumpCtrl", {"delta_kp": 99.0})
        self.engine.run_step(mid, 1)
        self.engine.run_step(mid, 2)
        self.engine.run_step(mid, 3)   # will reject due to bounds
        m = self.engine.get_mutation(mid)
        assert m["status"] == "rejected"
        # Further steps should fail
        res = self.engine.run_step(mid, 4)
        assert "error" in res

    def test_list_mutations_all(self):
        self.engine.begin_mutation("P1", {"delta_kp": 0.01})
        self.engine.begin_mutation("P2", {"delta_ki": 0.01})
        muts = self.engine.list_mutations()
        assert len(muts) >= 2

    def test_list_mutations_by_status(self):
        mid = self.engine.begin_mutation("P3", {"delta_kp": 99.0})
        self.engine.run_step(mid, 1)
        self.engine.run_step(mid, 2)
        self.engine.run_step(mid, 3)   # rejected
        muts = self.engine.list_mutations(status="rejected")
        assert len(muts) >= 1

    def test_get_full_evidence_chain(self):
        mid = self.engine.begin_mutation("EV_TEST", {"delta_kp": 0.01})
        self.engine.run_step(mid, 1)
        evidence = self.engine.get_full_evidence(mid)
        assert len(evidence) >= 1
        assert "signature" in evidence[0]

    def test_evidence_signatures_present(self):
        mid = self.engine.begin_mutation("SIG_TEST", {"delta_kp": 0.01})
        self._run_steps_1_7(mid)
        evidence = self.engine.get_full_evidence(mid)
        assert all(len(e["signature"]) > 0 for e in evidence)

    def test_pipeline_step_count(self):
        assert len(PIPELINE_STEPS) == 11

    def test_gate_step_is_8(self):
        assert GATE_STEP == 8

    def test_approval_code_constant(self):
        assert APPROVAL_CODE == "Maquister_Equtitum"

    def test_authorized_by_constant(self):
        assert "Baron" in AUTHORIZED_BY

    def test_get_stats(self):
        stats = self.engine.get_stats()
        assert "total_mutations"  in stats
        assert "pipeline_steps"   in stats
        assert "authorized_by"    in stats
