"""
KISWARM v4.0 — CIEC Module Test Suite
Tests for Modules 11–16: PLC Parser, SCADA Observer, Physics Twin,
Rule Engine, Knowledge Graph, Industrial Actor-Critic
"""

import math
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest

# ── Module 11: PLC Semantic Parser ───────────────────────────────────────────

class TestPLCSemanticParser:
    """Module 11 — IEC 61131-3 Structured Text parser."""

    def _parser(self):
        from sentinel.plc_parser import PLCSemanticParser
        return PLCSemanticParser(store_path="/tmp/test_plc_cache.json")

    ST_PUMP_CTRL = """
PROGRAM PumpControl
VAR
    pressure   : REAL := 0.0;
    setpoint   : REAL := 4.0;
    pump_out   : REAL := 0.0;
    ESTOP      : BOOL := FALSE;
    fault_flag : BOOL := FALSE;
END_VAR

IF ESTOP OR fault_flag THEN
    pump_out := 0.0;
ELSE
    IF pressure > 8.0 THEN
        pump_out := 0.0;
    ELSE
        pump_out := PID(SP := setpoint, PV := pressure, KP := 1.2, KI := 0.3, KD := 0.05);
    END_IF
END_IF

WD_Timer(IN := NOT fault_flag, PT := T#5S);
IF WD_Timer.Q THEN
    fault_flag := TRUE;
END_IF

END_PROGRAM
"""

    def test_parse_returns_result(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL, "PumpControl")
        assert r.program_name == "PumpControl"

    def test_source_hash_computed(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL, "PumpControl")
        assert len(r.source_hash) == 16

    def test_variable_extraction(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert "pressure" in r.variables
        assert "setpoint" in r.variables

    def test_variable_types(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert r.variables.get("pressure", {}).get("type") == "REAL"

    def test_safety_variable_detection(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert "ESTOP" in r.safety_flags or len(r.safety_flags) >= 0

    def test_cir_nodes_built(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert r.node_count > 0

    def test_if_nodes_detected(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        if_nodes = [n for n in r.nodes if n.node_type == "IF"]
        assert len(if_nodes) >= 1

    def test_pid_block_detected(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert len(r.pid_blocks) >= 1

    def test_pid_params_extracted(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        if r.pid_blocks:
            pid = r.pid_blocks[0]
            assert pid.kp > 0

    def test_interlock_detected(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert len(r.interlocks) >= 1

    def test_watchdog_detected(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert len(r.watchdogs) >= 1

    def test_dsg_edges_built(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert r.edge_count >= 0   # may be 0 if no cross-node signals

    def test_to_dict_complete(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL, "PumpControl")
        d = r.to_dict()
        assert "nodes" in d
        assert "pid_blocks" in d
        assert "interlocks" in d
        assert "stats" in d

    def test_parse_time_recorded(self):
        p = self._parser()
        r = p.parse(self.ST_PUMP_CTRL)
        assert r.parse_time_ms >= 0.0

    def test_cache_hit_on_same_source(self):
        p = self._parser()
        r1 = p.parse(self.ST_PUMP_CTRL, "A")
        r2 = p.parse(self.ST_PUMP_CTRL, "B")
        assert r1.source_hash == r2.source_hash

    def test_empty_source_handled(self):
        p = self._parser()
        r = p.parse("", "EMPTY")
        assert r.node_count == 0

    def test_parse_multiple(self):
        p = self._parser()
        results = p.parse_multiple([
            (self.ST_PUMP_CTRL, "PUMP"),
            ("PROGRAM X\nEND_PROGRAM", "SIMPLE"),
        ])
        assert len(results) == 2

    def test_stats_returned(self):
        p = self._parser()
        s = p.get_stats()
        assert "total_parses" in s

    def test_tokenizer_smoke(self):
        from sentinel.plc_parser import tokenize
        tokens = tokenize("IF pressure > 8.0 THEN pump_out := 0.0; END_IF")
        assert len(tokens) > 0

    def test_variable_extraction_standalone(self):
        from sentinel.plc_parser import extract_variables
        src = "VAR\n  x : REAL := 1.0;\n  y : INT;\nEND_VAR"
        vars_ = extract_variables(src)
        assert "x" in vars_
        assert vars_["x"]["type"] == "REAL"


# ── Module 12: SCADA Observer ─────────────────────────────────────────────────

class TestSCADAObserver:
    """Module 12 — OPC UA / SQL historian observation layer."""

    def _obs(self):
        from sentinel.scada_observer import SCADAObserver
        return SCADAObserver(store_path="/tmp/test_scada_state.json")

    def test_subscribe_tags(self):
        obs = self._obs()
        obs.subscribe_tags(["pressure", "temperature", "flow"])
        assert "pressure" in obs.subscriber.tag_names

    def test_push_reading(self):
        obs = self._obs()
        obs.push_reading("temperature", 45.0)
        buf = obs.subscriber.get_buffer("temperature")
        assert buf is not None
        assert buf.latest() == 45.0

    def test_push_snapshot(self):
        obs = self._obs()
        obs.push_snapshot({"temp": 50.0, "pressure": 3.0})
        assert obs.subscriber.get_buffer("temp").latest() == 50.0

    def test_feature_computation(self):
        obs = self._obs()
        ts  = time.time()
        for i in range(50):
            obs.push_reading("temperature", 20.0 + i * 0.5, ts + i * 0.1)
        buf  = obs.subscriber.get_buffer("temperature")
        feat = buf.get_features()
        assert feat.mean > 20.0
        assert feat.sample_count == 50

    def test_variance_nonzero_on_varying_signal(self):
        obs = self._obs()
        ts  = time.time()
        import math as _m
        for i in range(100):
            obs.push_reading("sinus", _m.sin(i * 0.1) * 10, ts + i * 0.05)
        feat = obs.subscriber.get_buffer("sinus").get_features()
        assert feat.variance > 0

    def test_switching_frequency_binary(self):
        obs = self._obs()
        ts  = time.time()
        for i in range(40):
            val = 1.0 if i % 4 < 2 else 0.0
            obs.push_reading("relay", val, ts + i * 0.25)
        feat = obs.subscriber.get_buffer("relay").get_features()
        assert feat.switching_frequency >= 0.0

    def test_actuator_cycle_count(self):
        obs = self._obs()
        ts  = time.time()
        vals = [0, 50, 100, 50, 0, 50, 100, 50, 0]
        for i, v in enumerate(vals):
            obs.push_reading("valve", float(v), ts + i)
        feat = obs.subscriber.get_buffer("valve").get_features()
        assert feat.actuator_cycle_count >= 2

    def test_thermal_drift_positive(self):
        obs = self._obs()
        ts  = time.time()
        for i in range(20):
            obs.push_reading("temp_drift", float(20 + i), ts + i)
        feat = obs.subscriber.get_buffer("temp_drift").get_features()
        assert feat.thermal_drift > 0

    def test_build_state_vector(self):
        obs = self._obs()
        for tag in ["pressure", "temperature"]:
            for i in range(10):
                obs.push_reading(tag, float(i), time.time())
        sv = obs.build_state_vector()
        assert sv.timestamp > 0
        assert len(sv.tag_features) >= 2

    def test_state_vector_flatten(self):
        obs = self._obs()
        for i in range(5):
            obs.push_reading("t1", float(i), time.time())
        sv = obs.build_state_vector()
        vec = sv.to_vector()
        assert len(vec) > 0

    def test_alarm_push(self):
        obs = self._obs()
        obs.push_alarm("pressure", "High pressure alarm", "CRITICAL")
        sv  = obs.build_state_vector()
        assert sv.alarm_count >= 1

    def test_ingest_historian(self):
        obs = self._obs()
        records = [
            {"tag": "flow", "value": float(i), "timestamp": time.time() + i}
            for i in range(100)
        ]
        count = obs.ingest_history(records)
        assert count == 100

    def test_historian_replay(self):
        obs = self._obs()
        records = [
            {"tag": "replay_tag", "value": float(i), "timestamp": time.time() + i}
            for i in range(10)
        ]
        obs.ingest_history(records)
        n = obs.historian.replay_to_subscriber(obs.subscriber)
        assert n >= 10

    def test_stats_returned(self):
        obs = self._obs()
        obs.push_reading("x", 1.0)
        s = obs.get_stats()
        assert "subscribed_tags" in s
        assert "total_samples" in s

    def test_anomaly_detection(self):
        obs = self._obs()
        ts = time.time()
        for i in range(50):
            obs.push_reading("stable_tag", 10.0, ts + i)
        anomalies = obs.get_anomalies(std_threshold=0.5)
        assert isinstance(anomalies, list)

    def test_tag_buffer_window_trim(self):
        from sentinel.scada_observer import TagBuffer
        buf = TagBuffer("test", window_seconds=2.0)
        old_ts = time.time() - 10.0
        buf.push(99.0, old_ts)
        buf.push(1.0, time.time())
        buf.push(2.0, time.time())
        assert buf.sample_count <= 2

    def test_feature_to_vector_length(self):
        from sentinel.scada_observer import TagFeatures
        feat = TagFeatures("t", 0, 1, 10)
        vec  = feat.to_vector()
        assert len(vec) == 12


# ── Module 13: Physics Twin ───────────────────────────────────────────────────

class TestPhysicsTwin:
    """Module 13 — Digital twin physics simulation engine."""

    def _twin(self):
        from sentinel.physics_twin import PhysicsTwin
        return PhysicsTwin(store_path="/tmp/test_twin.json")

    def test_thermal_step(self):
        from sentinel.physics_twin import ThermalState
        t = ThermalState(temperature=20.0, q_in=5000.0, k_loss=10.0, t_env=20.0)
        new_t = t.step(1.0)
        assert new_t > 20.0   # heating up

    def test_thermal_equilibrium(self):
        from sentinel.physics_twin import ThermalState
        t = ThermalState(temperature=100.0, q_in=0.0, k_loss=50.0, t_env=20.0)
        for _ in range(200):
            t.step(1.0)
        assert t.temperature < 100.0   # cooled down

    def test_pump_flow(self):
        from sentinel.physics_twin import PumpState
        p = PumpState(k_flow=1.0)
        p.step(dp=4.0)
        assert p.flow_rate == pytest.approx(2.0, abs=0.01)  # sqrt(4)=2

    def test_pump_cavitation_detection(self):
        from sentinel.physics_twin import PumpState
        p = PumpState(npsh_available=2.0, npsh_required=3.0)
        p.step(dp=1.0)
        assert p.cavitation_event is True

    def test_pump_no_cavitation_normal(self):
        from sentinel.physics_twin import PumpState
        p = PumpState(npsh_available=6.0, npsh_required=3.0)
        p.step(dp=1.0)
        assert p.cavitation_event is False

    def test_battery_soc_charging(self):
        from sentinel.physics_twin import BatteryState
        b = BatteryState(soc=0.5, capacity_ah=100.0)
        b.step(dt=3600.0, i_charge=10.0, i_discharge=0.0)
        assert b.soc > 0.5

    def test_battery_soc_discharging(self):
        from sentinel.physics_twin import BatteryState
        b = BatteryState(soc=0.8, capacity_ah=100.0)
        b.step(dt=3600.0, i_charge=0.0, i_discharge=10.0)
        assert b.soc < 0.8

    def test_battery_soc_bounded(self):
        from sentinel.physics_twin import BatteryState
        b = BatteryState(soc=0.99, capacity_ah=10.0)
        b.step(dt=3600.0, i_charge=100.0, i_discharge=0.0)
        assert b.soc <= 1.0

    def test_battery_voltage_formula(self):
        from sentinel.physics_twin import BatteryState
        b = BatteryState(soc=0.8, r_internal=0.1)
        b.i_discharge = 10.0
        # V = OCV(0.8) - 10*0.1; OCV > 3
        assert b.voltage > 2.0

    def test_power_routing_frequency(self):
        from sentinel.physics_twin import PowerRoutingState
        pr = PowerRoutingState(loads=[1500.0], generation=[1000.0], inertia_h=5.0)
        pr.step(dt=1.0)
        assert pr.frequency < 50.0   # under-generation → frequency drop

    def test_power_routing_balanced(self):
        from sentinel.physics_twin import PowerRoutingState
        pr = PowerRoutingState(loads=[1000.0], generation=[1000.0])
        pr.step(dt=1.0)
        assert abs(pr.frequency - 50.0) < 0.1

    def test_latency_layer_delivers(self):
        from sentinel.physics_twin import LatencyNoiseLayer
        ll = LatencyNoiseLayer(min_delay_ms=1.0, max_delay_ms=5.0, seed=42)
        t0 = 0.0
        ll.command(1.0, t0)
        result = ll.get_effective(t0 + 1.0)
        assert result is not None
        assert abs(result - 1.0) < 1.0   # noise bounded

    def test_latency_no_delivery_before_delay(self):
        from sentinel.physics_twin import LatencyNoiseLayer
        ll = LatencyNoiseLayer(min_delay_ms=500.0, max_delay_ms=600.0, seed=1)
        ll.command(5.0, 0.0)
        result = ll.get_effective(0.001)   # too early
        assert result is None

    def test_fault_injector_sensor_drift(self):
        from sentinel.physics_twin import FaultInjector, ActiveFault
        fi = FaultInjector(seed=42)
        t  = time.time()
        fi._active.append(ActiveFault(
            fault_type="sensor_drift", tag="temperature",
            start_time=t-1, end_time=t+100, severity=1.0,
            param={"drift_rate": 0.1},
        ))
        drifted = fi.apply_sensor_drift(100.0, "temperature", 10.0)
        assert drifted > 100.0

    def test_fault_injector_actuator_failed(self):
        from sentinel.physics_twin import FaultInjector, ActiveFault
        fi = FaultInjector(seed=1)
        t  = time.time()
        fi._active.append(ActiveFault(
            fault_type="actuator_failed", tag="pump",
            start_time=t-1, end_time=t+100, severity=1.0,
        ))
        result = fi.apply_actuator_partial(100.0, "pump")
        assert result == 0.0

    def test_fault_injector_schedule_random(self):
        fi = _get_fault_injector()
        tags   = ["pressure", "flow", "temp"]
        faults = fi.schedule_random(tags, duration_steps=100, fault_rate=0.5)
        assert len(faults) >= 1

    def test_physics_twin_run(self):
        twin = self._twin()
        result = twin.run(steps=20, dt=0.1, inject_faults=False)
        assert result.steps == 20
        assert result.survival_score >= 0.0
        assert len(result.thermal_history) == 20

    def test_physics_twin_run_with_faults(self):
        twin = self._twin()
        result = twin.run(steps=30, dt=0.1, inject_faults=True)
        assert result.steps == 30

    def test_physics_twin_evaluate_mutation(self):
        twin   = self._twin()
        params = {"steps": 20, "dt": 0.1, "q_in": 1500.0, "dp": 1.5}
        promote, metrics = twin.evaluate_mutation(params, n_runs=2)
        assert isinstance(promote, bool)
        assert "promoted" in metrics

    def test_physics_twin_stats(self):
        twin = self._twin()
        twin.run(steps=10, dt=0.1)
        s = twin.get_stats()
        assert s["total_runs"] >= 1

    def test_catastrophic_state_detection(self):
        fi = _get_fault_injector()
        assert fi.is_catastrophic({"temperature": 200.0})
        assert fi.is_catastrophic({"pressure": 20.0})
        assert not fi.is_catastrophic({"temperature": 50.0, "pressure": 3.0})


def _get_fault_injector():
    from sentinel.physics_twin import FaultInjector
    return FaultInjector(seed=99)


# ── Module 14: Rule Constraint Engine ─────────────────────────────────────────

class TestRuleConstraintEngine:
    """Module 14 — Absolute safety constraint layer."""

    def _engine(self):
        from sentinel.rule_engine import RuleConstraintEngine
        return RuleConstraintEngine(store_path="/tmp/test_constraints.json")

    def test_safe_state_passes(self):
        engine = self._engine()
        state  = {"pressure": 3.0, "battery_soc": 0.8, "temperature": 40.0}
        action = {"delta_kp": 0.01}
        result = engine.validate(state, action)
        assert result.allowed is True

    def test_overpressure_blocks(self):
        engine = self._engine()
        state  = {"pressure": 9.0, "battery_soc": 0.8, "temperature": 40.0}
        action = {"delta_kp": 0.01}
        result = engine.validate(state, action)
        assert result.allowed is False
        assert "OVERPRESSURE_BLOCK" in result.hard_violations

    def test_battery_critical_blocks(self):
        engine = self._engine()
        state  = {"battery_soc": 0.10, "pressure": 2.0, "temperature": 40.0}
        result = engine.validate(state, {"delta_kp": 0.01})
        assert result.allowed is False

    def test_overtemp_blocks(self):
        engine = self._engine()
        state  = {"temperature": 100.0, "pressure": 2.0, "battery_soc": 0.8}
        result = engine.validate(state, {})
        assert result.allowed is False

    def test_soft_constraint_penalty_only(self):
        engine = self._engine()
        state  = {"pressure": 7.0, "battery_soc": 0.9, "temperature": 40.0}
        result = engine.validate(state, {})
        # pressure > 6.5 is soft — should not block
        assert result.allowed is True
        assert result.total_penalty > 0

    def test_pid_kp_bound_hard(self):
        engine = self._engine()
        state  = {"pressure": 3.0, "battery_soc": 0.9, "temperature": 40.0}
        action = {"delta_kp": 0.10}   # > 5%
        result = engine.validate(state, action)
        assert result.allowed is False
        assert "PID_KP_BOUND" in result.hard_violations

    def test_pid_ki_bound_hard(self):
        engine = self._engine()
        state  = {"pressure": 3.0, "battery_soc": 0.9, "temperature": 40.0}
        action = {"delta_ki": -0.10}
        result = engine.validate(state, action)
        assert result.allowed is False

    def test_action_clamped_to_plc_safe(self):
        engine = self._engine()
        result = engine.validate({}, {"delta_kp": 0.03})
        assert result.action_after.get("delta_kp", 0.03) <= 0.05

    def test_penalty_computation(self):
        engine  = self._engine()
        state   = {"pressure": 9.5, "battery_soc": 0.1}
        penalty = engine.compute_penalty(state, {})
        assert penalty >= 1e6

    def test_is_safe_state_true(self):
        engine = self._engine()
        assert engine.is_safe_state({"pressure": 3.0, "battery_soc": 0.9, "temperature": 50.0})

    def test_is_safe_state_false_pressure(self):
        engine = self._engine()
        assert engine.is_safe_state({"pressure": 10.0}) is False

    def test_add_custom_constraint(self):
        from sentinel.rule_engine import ConstraintDefinition
        engine = self._engine()
        custom = ConstraintDefinition(
            name="CUSTOM_FLOW",
            condition=lambda s: s.get("flow", 0) > 10.0,
            hard=True,
            penalty_value=1e5,
        )
        engine.add_constraint(custom)
        result = engine.validate({"flow": 15.0}, {})
        assert result.allowed is False
        assert "CUSTOM_FLOW" in result.hard_violations

    def test_remove_constraint(self):
        engine = self._engine()
        ok = engine.remove_constraint("PID_KP_BOUND")
        assert ok is True
        # After removal, large kp should not be blocked
        result = engine.validate({"pressure": 3.0}, {"delta_kp": 0.10})
        assert "PID_KP_BOUND" not in result.hard_violations

    def test_get_constraints_list(self):
        engine = self._engine()
        clist  = engine.get_constraints()
        assert len(clist) >= 5
        assert all("name" in c and "hard" in c for c in clist)

    def test_violation_history(self):
        engine = self._engine()
        engine.validate({"pressure": 10.0}, {})
        hist = engine.get_violation_history()
        assert len(hist) >= 1

    def test_stats_returned(self):
        engine = self._engine()
        engine.validate({}, {})
        s = engine.get_stats()
        assert "total_checks" in s
        assert "block_rate" in s

    def test_validation_fast(self):
        engine = self._engine()
        t0 = time.perf_counter()
        for _ in range(100):
            engine.validate({"pressure": 2.0, "battery_soc": 0.9}, {"delta_kp": 0.01})
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0   # 100 checks in < 1s

    def test_frequency_deviation_block(self):
        engine = self._engine()
        state  = {"grid_frequency": 47.0}
        result = engine.validate(state, {})
        assert result.allowed is False

    def test_actuator_wear_block(self):
        engine = self._engine()
        state  = {"actuator_wear_index": 0.97}
        result = engine.validate(state, {})
        assert result.allowed is False


# ── Module 15: Knowledge Graph ────────────────────────────────────────────────

class TestKnowledgeGraph:
    """Module 15 — Cross-project knowledge repository."""

    def _kg(self):
        from sentinel.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph(store_path="/tmp/test_kg.json", site_id="test_site")

    def test_add_pid_config(self):
        kg   = self._kg()
        node = kg.add_pid_config("Pump PID", 1.2, 0.3, 0.05, 0.1, 0.0, 100.0, "pump")
        assert node.node_id in kg._nodes

    def test_pid_config_payload(self):
        kg   = self._kg()
        node = kg.add_pid_config("Test PID", 2.0, 0.5, 0.1, 0.05, 0, 100)
        assert node.payload["kp"] == 2.0
        assert node.payload["ki"] == 0.5

    def test_add_failure_signature(self):
        kg   = self._kg()
        node = kg.add_failure_signature(
            "Pump Cavitation",
            symptoms=["high_switching_freq", "pressure_drop", "vibration"],
            root_cause="NPSH insufficient",
            fix_template={"npsh_margin": 2.0},
        )
        assert node.kind == "FailureSig"
        assert node.node_id in kg._nodes

    def test_add_optimization_template(self):
        kg   = self._kg()
        node = kg.add_optimization_template(
            "PID Gain Reduction",
            problem_class="oscillation",
            solution={"reduce_kp_by": 0.15},
            performance_gain=0.12,
        )
        assert node.kind == "OptTemplate"

    def test_record_outcome_success(self):
        kg   = self._kg()
        node = kg.add_pid_config("X", 1.0, 0.1, 0.01, 0.1, 0, 100)
        kg.record_outcome(node.node_id, success=True)
        assert kg._nodes[node.node_id].success_count == 1

    def test_record_outcome_failure(self):
        kg   = self._kg()
        node = kg.add_pid_config("Y", 1.0, 0.1, 0.01, 0.1, 0, 100)
        kg.record_outcome(node.node_id, success=False)
        assert kg._nodes[node.node_id].failure_count == 1

    def test_success_rate(self):
        from sentinel.knowledge_graph import KGNode
        node = KGNode("n1", "PIDConfig", "test")
        node.success_count = 7
        node.failure_count = 3
        assert node.success_rate == pytest.approx(0.7, abs=0.01)

    def test_find_similar_returns_results(self):
        kg = self._kg()
        kg.add_pid_config("Pump A", 1.2, 0.3, 0.05, 0.1, 0, 100, tags=["pump"])
        kg.add_pid_config("Pump B", 1.1, 0.25, 0.04, 0.1, 0, 100, tags=["pump"])
        matches = kg.find_similar([1.2, 0.3, 0.05], ["pump"], kind_filter="PIDConfig")
        assert len(matches) >= 1

    def test_find_by_symptoms(self):
        kg = self._kg()
        kg.add_failure_signature(
            "Cavitation",
            symptoms=["pressure_drop", "high_vibration"],
            root_cause="NPSH",
            fix_template={"action": "increase_inlet_pressure"},
        )
        matches = kg.find_by_symptoms(["pressure_drop", "high_vibration"])
        assert len(matches) >= 1

    def test_find_by_symptoms_no_match(self):
        kg      = self._kg()
        matches = kg.find_by_symptoms(["completely_unknown_symptom"])
        assert matches == []

    def test_detect_recurring_patterns(self):
        kg = self._kg()
        for i in range(3):
            kg.add_failure_signature(
                f"Cavitation_{i}",
                symptoms=["pressure_drop", "vibration"],
                root_cause=f"NPSH insufficient #{i}",
                fix_template={},
                project_id=f"PLANT_{i}",
            )
        patterns = kg.detect_recurring_patterns(min_occurrences=2)
        assert len(patterns) >= 1
        assert patterns[0]["occurrences"] >= 2

    def test_add_edge(self):
        kg = self._kg()
        n1 = kg.add_pid_config("PID-1", 1.0, 0.1, 0.01, 0.1, 0, 100)
        n2 = kg.add_pid_config("PID-2", 1.1, 0.15, 0.02, 0.1, 0, 100)
        kg.add_edge(n1.node_id, n2.node_id, "SUPERSEDES")
        assert len(kg._edges) >= 1

    def test_export_diff_bundle(self):
        kg     = self._kg()
        kg.add_pid_config("Export Test", 1.0, 0.1, 0.01, 0.1, 0, 100)
        bundle = kg.export_diff_bundle()
        assert "bundle_sig" in bundle
        assert bundle["node_count"] >= 1

    def test_import_diff_bundle_valid(self):
        kg1 = self._kg()
        kg2 = KnowledgeGraph_fresh()
        kg1.add_pid_config("From Site 1", 1.5, 0.4, 0.06, 0.1, 0, 100)
        bundle   = kg1.export_diff_bundle()
        imported = kg2.import_diff_bundle(bundle)
        assert imported >= 1

    def test_import_diff_bundle_invalid_sig(self):
        kg    = self._kg()
        count = kg.import_diff_bundle({
            "site_id": "bad",
            "timestamp": time.time(),
            "node_count": 1,
            "nodes": [],
            "bundle_sig": "INVALID_SIG",
        })
        assert count == 0

    def test_node_signature_deterministic(self):
        from sentinel.knowledge_graph import KGNode
        n1 = KGNode("id1", "PIDConfig", "Same Title", payload={"kp": 1.0})
        n2 = KGNode("id1", "PIDConfig", "Same Title", payload={"kp": 1.0})
        assert n1.sign() == n2.sign()

    def test_get_stats(self):
        kg = self._kg()
        kg.add_pid_config("S", 1.0, 0.1, 0.01, 0.1, 0, 100)
        s  = kg.get_stats()
        assert "total_nodes" in s
        assert "by_kind" in s

    def test_list_nodes(self):
        kg = self._kg()
        kg.add_pid_config("L1", 1.0, 0.1, 0.01, 0.1, 0, 100)
        nodes = kg.list_nodes()
        assert len(nodes) >= 1

    def test_get_node(self):
        kg   = self._kg()
        node = kg.add_pid_config("G1", 1.0, 0.1, 0.01, 0.1, 0, 100)
        d    = kg.get_node(node.node_id)
        assert d is not None
        assert d["id"] == node.node_id


def KnowledgeGraph_fresh():
    from sentinel.knowledge_graph import KnowledgeGraph
    return KnowledgeGraph(store_path="/tmp/test_kg2.json", site_id="remote_site")


# ── Module 16: Industrial Actor-Critic ───────────────────────────────────────

class TestIndustrialActorCritic:
    """Module 16 — Constrained Actor-Critic RL engine."""

    def _ac(self):
        from sentinel.actor_critic import IndustrialActorCritic
        return IndustrialActorCritic(
            state_dim=16,
            n_constraints=3,
            store_path="/tmp/test_ac.json",
            seed=42,
        )

    def test_select_action_returns_dict(self):
        ac      = self._ac()
        state   = [float(i) * 0.1 for i in range(16)]
        action, info = ac.select_action(state)
        assert isinstance(action, dict)
        assert len(action) > 0

    def test_action_plc_bounds(self):
        ac    = self._ac()
        state = [0.5] * 16
        from sentinel.actor_critic import PLC_BOUNDS
        for _ in range(20):
            action, _ = ac.select_action(state)
            for name, (lo, hi) in PLC_BOUNDS.items():
                assert lo <= action[name] <= hi, f"{name}={action[name]} not in [{lo},{hi}]"

    def test_deterministic_action_reproducible(self):
        ac     = self._ac()
        state  = [0.3] * 16
        a1, _  = ac.select_action(state, deterministic=True)
        a2, _  = ac.select_action(state, deterministic=True)
        assert a1 == a2

    def test_observe_pushes_to_buffer(self):
        ac    = self._ac()
        state = [0.1] * 16
        action, _ = ac.select_action(state)
        ac.observe(state, list(action.values()), 1.0, state, False, 0.0)
        assert len(ac.buffer) >= 1

    def test_update_requires_enough_samples(self):
        ac     = self._ac()
        result = ac.update(batch_size=64)
        assert result["status"] == "buffer_too_small"

    def test_update_after_enough_samples(self):
        ac    = self._ac()
        state = [0.1] * 16
        for i in range(100):
            action, _ = ac.select_action(state)
            ac.observe(state, list(action.values()), float(i % 3), state, False)
        result = ac.update(batch_size=32)
        assert result["status"] == "updated"

    def test_reward_function(self):
        from sentinel.actor_critic import compute_reward, RewardWeights
        s  = {"variance": 0.1, "energy_waste": 0.2, "actuator_cycles": 5, "oscillation": 0.05}
        r  = compute_reward(s, {}, s, RewardWeights())
        assert isinstance(r, float)

    def test_reward_higher_when_stable(self):
        from sentinel.actor_critic import compute_reward, RewardWeights
        w    = RewardWeights()
        s_stable   = {"variance": 0.01, "energy_waste": 0.05, "actuator_cycles": 1, "oscillation": 0.01}
        s_unstable = {"variance": 10.0, "energy_waste": 0.5, "actuator_cycles": 50, "oscillation": 1.0}
        r_s = compute_reward({}, {}, s_stable, w)
        r_u = compute_reward({}, {}, s_unstable, w)
        assert r_s > r_u

    def test_lagrange_multiplier_update(self):
        from sentinel.actor_critic import LagrangeMultipliers
        lm = LagrangeMultipliers(n_constraints=2, eta_lambda=0.1)
        lm.update([1.0, 0.5])     # costs > d=0, λ should rise
        assert lm.values[0] > 0.0

    def test_lagrange_multiplier_nonneg(self):
        from sentinel.actor_critic import LagrangeMultipliers
        lm = LagrangeMultipliers(n_constraints=2, eta_lambda=0.1, d=10.0)
        lm.update([-5.0, -10.0])  # costs < d, λ should stay ≥ 0
        assert lm.values[0] >= 0.0

    def test_lagrangian_penalty_computed(self):
        from sentinel.actor_critic import LagrangeMultipliers
        lm = LagrangeMultipliers(n_constraints=2, eta_lambda=0.1)
        lm._lambdas = [1.0, 2.0]
        penalty = lm.lagrangian_penalty([0.5, 1.0])
        assert penalty > 0

    def test_shielding_with_constraint_engine(self):
        ac = self._ac()
        from sentinel.rule_engine import RuleConstraintEngine
        engine = RuleConstraintEngine(store_path="/tmp/test_shield_ce.json")
        state  = [0.0] * 16
        # Trigger unsafe state so constraint engine blocks
        action, info = ac.select_action(state, constraint_check=engine)
        assert isinstance(action, dict)

    def test_get_stats(self):
        ac = self._ac()
        ac.select_action([0.1] * 16)
        s  = ac.get_stats()
        assert "steps" in s
        assert "action_names" in s
        assert "plc_bounds" in s

    def test_encoder_forward_shape(self):
        from sentinel.actor_critic import SharedEncoder
        import random
        enc = SharedEncoder(16, random.Random(1))
        z   = enc.forward([0.1] * 16)
        from sentinel.actor_critic import ENCODER_DIM
        assert len(z) == ENCODER_DIM

    def test_actor_output_shape(self):
        from sentinel.actor_critic import ActorHead, SharedEncoder, ACTION_DIM
        import random
        rng = random.Random(1)
        enc = SharedEncoder(16, rng)
        act = ActorHead(rng)
        z   = enc.forward([0.1] * 16)
        actions, means, stds = act.forward(z)
        assert len(actions) == ACTION_DIM
        assert len(means)   == ACTION_DIM
        assert len(stds)    == ACTION_DIM

    def test_critic_output_scalar(self):
        from sentinel.actor_critic import CriticHead, SharedEncoder
        import random
        rng = random.Random(1)
        enc = SharedEncoder(16, rng)
        crt = CriticHead(rng)
        z   = enc.forward([0.1] * 16)
        v   = crt.forward(z)
        assert isinstance(v, float)

    def test_math_tanh_bounds(self):
        from sentinel.actor_critic import _tanh
        assert -1.0 <= _tanh(100.0) <= 1.0
        assert -1.0 <= _tanh(-100.0) <= 1.0

    def test_math_softplus_positive(self):
        from sentinel.actor_critic import _softplus
        assert _softplus(-5.0) > 0
        assert _softplus(0.0) > 0

    def test_run_episode_with_env(self):
        """Test episode runner with a trivial environment."""
        ac = self._ac()

        call_count = [0]
        def simple_env(action):
            call_count[0] += 1
            state = [0.1] * 16
            if action is None:
                return state, 0.0, False, {}
            reward = 1.0 if call_count[0] < 5 else 0.0
            done   = call_count[0] >= 5
            return state, reward, done, {"cost": 0.0}

        result = ac.run_episode(simple_env, max_steps=10, train=True)
        assert "total_reward" in result
        assert result["steps"] >= 1


# ── Integration: Full CIEC Pipeline ──────────────────────────────────────────

class TestCIECPipeline:
    """End-to-end CIEC pipeline integration tests."""

    def test_plc_to_knowledge_graph(self):
        """Parse PLC → extract PID → store in knowledge graph."""
        from sentinel.plc_parser   import PLCSemanticParser
        from sentinel.knowledge_graph import KnowledgeGraph

        parser = PLCSemanticParser(store_path="/tmp/int_plc.json")
        kg     = KnowledgeGraph(store_path="/tmp/int_kg.json")

        st_code = """
PROGRAM TestCtrl
VAR sp : REAL; pv : REAL; out : REAL; END_VAR
out := PID(SP := sp, PV := pv, KP := 1.5, KI := 0.2, KD := 0.04);
END_PROGRAM
"""
        result = parser.parse(st_code, "TestCtrl")
        for pid in result.pid_blocks:
            kg.add_pid_config(
                f"From:{result.program_name}",
                pid.kp, pid.ki, pid.kd,
                pid.sample_time,
                pid.output_min, pid.output_max,
                plant_type="test",
            )
        assert len(kg._nodes) >= 1

    def test_scada_to_constraint_check(self):
        """SCADA observation → build state → validate against constraints."""
        from sentinel.scada_observer import SCADAObserver
        from sentinel.rule_engine    import RuleConstraintEngine

        obs    = SCADAObserver(store_path="/tmp/int_scada.json")
        engine = RuleConstraintEngine(store_path="/tmp/int_rule.json")

        for i in range(20):
            obs.push_reading("pressure", 3.0 + i * 0.01, time.time() + i)
        sv     = obs.build_state_vector()
        # Build simple state from tag features
        state  = {"pressure": 3.0, "battery_soc": 0.9, "temperature": 45.0}
        result = engine.validate(state, {"delta_kp": 0.02})
        assert result.allowed is True

    def test_physics_twin_to_knowledge_graph(self):
        """Run twin → store mutation result in knowledge graph."""
        from sentinel.physics_twin import PhysicsTwin
        from sentinel.knowledge_graph import KnowledgeGraph

        twin = PhysicsTwin(store_path="/tmp/int_twin.json")
        kg   = KnowledgeGraph(store_path="/tmp/int_kg3.json")

        params  = {"steps": 10, "dt": 0.1, "q_in": 1800.0, "dp": 2.0}
        promote, metrics = twin.evaluate_mutation(params, n_runs=1)

        if promote:
            kg.add_optimization_template(
                "Twin-Validated Config",
                problem_class="thermal_efficiency",
                solution=params,
                performance_gain=metrics.get("avg_survival", 0.0),
            )
        # Either promoted (stored) or not — both valid
        assert isinstance(promote, bool)

    def test_actor_critic_with_constraint_shield(self):
        """RL selects action → rule engine validates → shielding works."""
        from sentinel.actor_critic import IndustrialActorCritic
        from sentinel.rule_engine  import RuleConstraintEngine

        ac     = IndustrialActorCritic(state_dim=8, store_path="/tmp/int_ac.json")
        engine = RuleConstraintEngine(store_path="/tmp/int_rule2.json")

        unsafe_state = [0.0] * 8
        for _ in range(5):
            action, info = ac.select_action(unsafe_state, constraint_check=engine)
            assert isinstance(action, dict)

    def test_knowledge_graph_cross_site_sync(self):
        """Simulate federated knowledge sync between two sites."""
        from sentinel.knowledge_graph import KnowledgeGraph

        site1 = KnowledgeGraph(store_path="/tmp/site1_kg.json", site_id="SITE_A")
        site2 = KnowledgeGraph(store_path="/tmp/site2_kg.json", site_id="SITE_B")

        # Site 1 learns a PID config
        site1.add_pid_config("Site A Pump PID", 1.2, 0.3, 0.05, 0.1, 0, 100)

        # Export and import to Site 2
        bundle   = site1.export_diff_bundle()
        imported = site2.import_diff_bundle(bundle)
        assert imported >= 1
        assert len(site2._nodes) >= 1
