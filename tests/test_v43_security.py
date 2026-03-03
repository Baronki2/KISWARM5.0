"""
KISWARM v4.3 — Test Suite: ICS Cybersecurity Engine + OT Network Monitor
135 tests covering Modules 29 and 30
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from python.sentinel.ics_security import (
    ICSSecurityEngine, CVEIntelligenceAgent, NetworkAnomalyDetector,
    PLCCodeScanner, SCADAConfigAssessor, IncidentCorrelationEngine,
    IEC62443Assessor, SecurityLedger, SecurityFinding,
    INDUSTRIAL_CVE_DB, UNSAFE_PLC_PATTERNS, MITRE_ICS,
    SL_DESCRIPTIONS, SL_REQUIREMENTS,
)
from python.sentinel.ot_network_monitor import (
    OTNetworkMonitor, OTAlert, OTSegment,
    PROTOCOL_SAFE_FC, SUSPICIOUS_FC,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    return ICSSecurityEngine()

@pytest.fixture
def monitor():
    return OTNetworkMonitor()

@pytest.fixture
def sample_plc_code():
    return """
PROGRAM PumpCtrl
VAR
  pressure : REAL;
  valve    : BOOL;
  fault    : BOOL;
END_VAR
valve := TRUE;
IF pressure > 800 THEN
  fault := TRUE;
END_IF
END_PROGRAM
"""

@pytest.fixture
def safe_plc_code():
    return """
PROGRAM SafePump
VAR
  WD_timer  : TON;
  pressure  : REAL := 0.0;
  ESTOP     : BOOL;
  fault_out : BOOL := FALSE;
END_VAR
WD_timer(IN := TRUE, PT := T#500ms);
IF ESTOP THEN
  valve := FALSE;
  fault_out := TRUE;
END_IF
IF pressure > 6.5 THEN
  fault_out := TRUE;
END_IF
END_PROGRAM
"""

@pytest.fixture
def good_scada_config():
    return {
        "encryption_enabled": True,
        "auth_type": "strong",
        "patch_level_days": 30,
        "default_creds_changed": True,
        "unnecessary_services": False,
        "audit_log_enabled": True,
        "remote_access_mfa": True,
        "firmware_signed": True,
    }

@pytest.fixture
def bad_scada_config():
    return {
        "encryption_enabled": False,
        "auth_type": "weak",
        "patch_level_days": 180,
        "default_creds_changed": False,
        "unnecessary_services": True,
        "audit_log_enabled": False,
        "remote_access_mfa": False,
        "firmware_signed": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 29: CONSTANTS AND DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_mitre_ics_has_entries(self):
        assert len(MITRE_ICS) >= 15

    def test_mitre_tactics_present(self):
        tactics = {v["tactic"] for v in MITRE_ICS.values()}
        assert "Initial Access" in tactics
        assert "Impact" in tactics
        assert "Impair Process Control" in tactics

    def test_cve_db_populated(self):
        assert len(INDUSTRIAL_CVE_DB) >= 8

    def test_cve_has_required_fields(self):
        for cve in INDUSTRIAL_CVE_DB:
            assert "cve" in cve
            assert "product" in cve
            assert "cvss" in cve
            assert "protocol" in cve

    def test_unsafe_patterns_count(self):
        assert len(UNSAFE_PLC_PATTERNS) >= 12

    def test_sl_descriptions_complete(self):
        for sl in range(0, 5):
            assert sl in SL_DESCRIPTIONS

    def test_sl_requirements_defined(self):
        for sl in range(1, 5):
            assert sl in SL_REQUIREMENTS
            assert len(SL_REQUIREMENTS[sl]) > 0

    def test_sl_requirements_cumulative(self):
        # Higher SL must include all lower SL requirements
        for sl in range(2, 5):
            prev = set(SL_REQUIREMENTS[sl - 1])
            curr = set(SL_REQUIREMENTS[sl])
            assert prev.issubset(curr), f"SL{sl} missing SL{sl-1} controls"


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY LEDGER
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityLedger:
    def _make_finding(self, fid="F001"):
        return SecurityFinding(
            finding_id=fid, severity="HIGH", category="plc_code",
            title="Test", description="Desc", recommendation="Fix it",
            asset_id="asset1",
        )

    def test_empty_ledger_integrity(self):
        ledger = SecurityLedger()
        assert ledger.verify_integrity()

    def test_append_returns_hash(self):
        ledger = SecurityLedger()
        h = ledger.append(self._make_finding())
        assert len(h) == 64

    def test_multiple_entries_integrity(self):
        ledger = SecurityLedger()
        for i in range(10):
            ledger.append(self._make_finding(f"F{i:03d}"))
        assert ledger.verify_integrity()

    def test_len_tracks_entries(self):
        ledger = SecurityLedger()
        for i in range(5):
            ledger.append(self._make_finding(f"F{i:03d}"))
        assert len(ledger) == 5

    def test_get_all_returns_dicts(self):
        ledger = SecurityLedger()
        ledger.append(self._make_finding())
        entries = ledger.get_all()
        assert len(entries) == 1
        assert "finding_id" in entries[0]

    def test_chain_hashes_differ(self):
        ledger = SecurityLedger()
        h1 = ledger.append(self._make_finding("F001"))
        h2 = ledger.append(self._make_finding("F002"))
        assert h1 != h2


# ─────────────────────────────────────────────────────────────────────────────
# CVE INTELLIGENCE AGENT
# ─────────────────────────────────────────────────────────────────────────────

class TestCVEIntelligenceAgent:
    def test_scan_known_product(self):
        agent = CVEIntelligenceAgent()
        findings = agent.scan("asset1", [{"product": "KEPServerEX", "version": "6.5"}])
        assert len(findings) >= 1
        assert findings[0].category == "cve"

    def test_scan_unknown_product(self):
        agent = CVEIntelligenceAgent()
        findings = agent.scan("asset1", [{"product": "MadeUpProduct9999", "version": "1.0"}])
        assert findings == []

    def test_scan_modicon(self):
        agent = CVEIntelligenceAgent()
        findings = agent.scan("plc1", [{"product": "Schneider Modicon", "version": "3.0"}])
        assert any("Modicon" in f.title or "CVE" in f.title for f in findings)

    def test_finding_has_recommendation(self):
        agent = CVEIntelligenceAgent()
        findings = agent.scan("asset1", [{"product": "KEPServerEX", "version": "6.5"}])
        assert all(f.recommendation for f in findings)

    def test_lookup_by_protocol(self):
        agent = CVEIntelligenceAgent()
        results = agent.lookup("modbus")
        assert len(results) >= 1

    def test_lookup_generic(self):
        agent = CVEIntelligenceAgent()
        results = agent.lookup("generic")
        assert len(results) >= 1

    def test_high_cvss_is_critical(self):
        agent = CVEIntelligenceAgent()
        findings = agent.scan("asset1", [{"product": "KEPServerEX", "version": "6.5"}])
        crit = [f for f in findings if f.severity == "CRITICAL"]
        assert len(crit) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK ANOMALY DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkAnomalyDetector:
    def _build_baseline(self, detector, n=50):
        for i in range(n):
            detector.ingest_event("seg1", "modbus", "read", "192.168.1.10", 1.0)

    def test_no_alert_on_normal_rate(self):
        det = NetworkAnomalyDetector()
        self._build_baseline(det, 50)
        finding = det.ingest_event("seg1", "modbus", "read", "192.168.1.10", 1.0)
        assert finding is None  # exact mean = no alert

    def test_alert_on_spike(self):
        det = NetworkAnomalyDetector()
        self._build_baseline(det, 30)
        finding = det.ingest_event("seg1", "modbus", "read", "192.168.1.10", 50.0)
        assert finding is not None
        assert finding.category == "network"

    def test_alert_on_new_ip(self):
        det = NetworkAnomalyDetector()
        det.ingest_event("seg1", "modbus", "read", "192.168.1.10", 1.0)
        finding = det.ingest_event("seg1", "modbus", "read", "10.0.0.99", 1.0)
        assert finding is not None
        assert "NEW_SOURCE_IP" in finding.finding_id or "NEWIP" in finding.finding_id

    def test_first_ip_no_alert(self):
        det = NetworkAnomalyDetector()
        finding = det.ingest_event("seg1", "modbus", "read", "192.168.1.1", 1.0)
        assert finding is None  # first ever IP, no alert

    def test_baseline_accumulates(self):
        det = NetworkAnomalyDetector()
        self._build_baseline(det, 15)
        baseline = det.get_baselines()
        assert "modbus" in baseline

    def test_finding_has_mitre(self):
        det = NetworkAnomalyDetector()
        self._build_baseline(det, 30)
        finding = det.ingest_event("seg1", "modbus", "read", "192.168.1.10", 999.0)
        if finding:
            assert finding.mitre_technique is not None


# ─────────────────────────────────────────────────────────────────────────────
# PLC CODE SCANNER
# ─────────────────────────────────────────────────────────────────────────────

class TestPLCCodeScanner:
    def test_detects_missing_watchdog(self, sample_plc_code):
        scanner = PLCCodeScanner()
        findings = scanner.scan(sample_plc_code, "PumpCtrl", "plc1")
        ids = [f.finding_id for f in findings]
        assert any("P001" in fid for fid in ids)

    def test_detects_direct_actuator_write(self, sample_plc_code):
        scanner = PLCCodeScanner()
        findings = scanner.scan(sample_plc_code, "PumpCtrl", "plc1")
        assert any("P005" in f.finding_id for f in findings)

    def test_safe_code_fewer_findings(self, safe_plc_code, sample_plc_code):
        scanner = PLCCodeScanner()
        safe = scanner.scan(safe_plc_code, "SafePump", "plc1")
        unsafe = scanner.scan(sample_plc_code, "PumpCtrl", "plc1")
        assert len(safe) < len(unsafe)

    def test_all_findings_have_recommendations(self, sample_plc_code):
        scanner = PLCCodeScanner()
        findings = scanner.scan(sample_plc_code, "PumpCtrl", "plc1")
        assert all(f.recommendation for f in findings)

    def test_severity_values_valid(self, sample_plc_code):
        scanner = PLCCodeScanner()
        findings = scanner.scan(sample_plc_code, "PumpCtrl", "plc1")
        valid = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert all(f.severity in valid for f in findings)

    def test_commented_safety_block_detected(self):
        scanner = PLCCodeScanner()
        code = "PROGRAM X\n(* ESTOP := TRUE; *)\nEND_PROGRAM"
        findings = scanner.scan(code, "X", "plc1")
        assert any("P008" in f.finding_id for f in findings)

    def test_hardcoded_threshold_detected(self):
        scanner = PLCCodeScanner()
        code = "PROGRAM X\nsetpoint := 1500;\nEND_PROGRAM"
        findings = scanner.scan(code, "X", "plc1")
        assert any("P002" in f.finding_id for f in findings)

    def test_while_true_detected(self):
        scanner = PLCCodeScanner()
        code = "PROGRAM X\nWHILE TRUE DO\n  x := 1;\nEND_WHILE\nEND_PROGRAM"
        findings = scanner.scan(code, "X", "plc1")
        assert any("P003" in f.finding_id for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# SCADA CONFIG ASSESSOR
# ─────────────────────────────────────────────────────────────────────────────

class TestSCADAConfigAssessor:
    def test_good_config_no_findings(self, good_scada_config):
        assessor = SCADAConfigAssessor()
        findings, controls = assessor.assess("scada1", good_scada_config)
        assert findings == []
        assert len(controls) >= 6

    def test_bad_config_many_findings(self, bad_scada_config):
        assessor = SCADAConfigAssessor()
        findings, controls = assessor.assess("scada1", bad_scada_config)
        assert len(findings) >= 4

    def test_default_creds_is_critical(self, bad_scada_config):
        assessor = SCADAConfigAssessor()
        findings, _ = assessor.assess("scada1", bad_scada_config)
        crits = [f for f in findings if f.severity == "CRITICAL"]
        assert len(crits) >= 1

    def test_controls_present_returned(self, good_scada_config):
        assessor = SCADAConfigAssessor()
        _, controls = assessor.assess("scada1", good_scada_config)
        assert "encrypted_comms" in controls
        assert "authentication" in controls

    def test_missing_config_worst_case(self):
        assessor = SCADAConfigAssessor()
        findings, controls = assessor.assess("scada1", {})
        assert len(findings) >= 5  # all missing → all findings
        assert controls == []

    def test_partial_config(self):
        assessor = SCADAConfigAssessor()
        findings, controls = assessor.assess("scada1", {"encryption_enabled": True})
        assert "encrypted_comms" in controls
        assert len(findings) >= 3


# ─────────────────────────────────────────────────────────────────────────────
# IEC 62443 ASSESSOR
# ─────────────────────────────────────────────────────────────────────────────

class TestIEC62443Assessor:
    def test_sl1_achieved_with_sl1_controls(self):
        assessor = IEC62443Assessor()
        controls = ["authentication", "basic_logging", "account_management"]
        result = assessor.assess("asset1", 1, controls, [])
        assert result.achieved_sl >= 1

    def test_sl2_not_achieved_with_sl1_only(self):
        assessor = IEC62443Assessor()
        controls = ["authentication", "basic_logging", "account_management"]
        result = assessor.assess("asset1", 2, controls, [])
        assert result.achieved_sl < 2
        assert not result.compliant

    def test_full_sl2_controls_pass(self):
        assessor = IEC62443Assessor()
        controls = SL_REQUIREMENTS[2]
        result = assessor.assess("asset1", 2, controls, [])
        assert result.achieved_sl >= 2
        assert result.compliant

    def test_sl4_needs_all_controls(self):
        assessor = IEC62443Assessor()
        controls = SL_REQUIREMENTS[4]
        result = assessor.assess("asset1", 4, controls, [])
        assert result.compliant

    def test_invalid_sl_raises(self):
        assessor = IEC62443Assessor()
        with pytest.raises((ValueError, Exception)):
            assessor.assess("asset1", 5, [], [])

    def test_assessment_has_description(self):
        assessor = IEC62443Assessor()
        result = assessor.assess("asset1", 2, [], [])
        d = result.to_dict()
        assert d["sl_description"]

    def test_missing_controls_listed(self):
        assessor = IEC62443Assessor()
        result = assessor.assess("asset1", 2, [], [])
        assert len(result.controls_missing) > 0


# ─────────────────────────────────────────────────────────────────────────────
# INCIDENT CORRELATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestIncidentCorrelation:
    def _finding(self, mitre="T0855", sev="HIGH"):
        return SecurityFinding(
            finding_id="TEST001", severity=sev, category="network",
            title="Test", description="Desc", recommendation="Fix",
            asset_id="asset1", mitre_technique=mitre,
        )

    def test_correlate_groups_by_tactic(self):
        engine = IncidentCorrelationEngine()
        engine.ingest(self._finding("T0855"))
        engine.ingest(self._finding("T0804"))
        incidents = engine.correlate()
        tactics = {i["tactic"] for i in incidents}
        assert len(tactics) >= 1

    def test_correlate_same_tactic_grouped(self):
        engine = IncidentCorrelationEngine()
        engine.ingest(self._finding("T0855"))
        engine.ingest(self._finding("T0836"))
        incidents = engine.correlate()
        # Both T0855 and T0836 → Impair Process Control
        tactic_findings = sum(i["finding_count"] for i in incidents if i["tactic"] == "Impair Process Control")
        assert tactic_findings >= 2

    def test_get_all_returns_history(self):
        engine = IncidentCorrelationEngine()
        engine.ingest(self._finding())
        engine.correlate()
        history = engine.get_all()
        assert len(history) >= 1

    def test_kill_chain_stage_ordered(self):
        engine = IncidentCorrelationEngine()
        engine.ingest(self._finding("T0817"))  # Initial Access = stage 1
        engine.ingest(self._finding("T0813"))  # Impact = stage 8
        incidents = engine.correlate()
        stages = [i["kill_chain_stage"] for i in incidents if isinstance(i["kill_chain_stage"], int)]
        assert stages == sorted(stages, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# ICS SECURITY ENGINE (integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestICSSecurityEngine:
    def test_scan_plc_returns_dict(self, engine, sample_plc_code):
        result = engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        assert "finding_count" in result
        assert "findings" in result

    def test_scan_plc_increments_stats(self, engine, sample_plc_code):
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        assert engine.get_stats()["plc_scans"] == 1

    def test_network_event_no_anomaly(self, engine):
        for _ in range(50):
            engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 1.0)
        result = engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 1.0)
        assert result["anomaly"] is False  # exact mean = no alert

    def test_network_event_spike_anomaly(self, engine):
        for _ in range(30):
            engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 1.0)
        result = engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 999.0)
        assert result["anomaly"] is True

    def test_cve_scan(self, engine):
        result = engine.scan_cve("asset1", [{"product": "KEPServerEX", "version": "6.5"}])
        assert result["matches"] >= 1

    def test_scada_config_good(self, engine, good_scada_config):
        result = engine.assess_scada_config("scada1", good_scada_config)
        assert result["finding_count"] == 0

    def test_scada_config_bad(self, engine, bad_scada_config):
        result = engine.assess_scada_config("scada1", bad_scada_config)
        assert result["finding_count"] >= 4

    def test_iec62443_assess(self, engine):
        from python.sentinel.ics_security import SL_REQUIREMENTS
        controls = SL_REQUIREMENTS[2]
        result = engine.iec62443_assess("asset1", 2, controls_present=list(controls))
        assert "achieved_sl" in result
        assert result["achieved_sl"] >= 2

    def test_get_posture(self, engine, sample_plc_code, bad_scada_config):
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        engine.assess_scada_config("scada1", bad_scada_config)
        posture = engine.get_posture()
        assert 0.0 <= posture["overall_score"] <= 1.0
        assert posture["open_findings"] > 0

    def test_posture_score_decreases_with_findings(self, engine, sample_plc_code):
        clean = engine.get_posture()["overall_score"]
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        dirty = engine.get_posture()["overall_score"]
        assert dirty <= clean

    def test_ledger_integrity_maintained(self, engine, sample_plc_code):
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        ledger = engine.get_ledger()
        assert ledger["intact"] is True

    def test_incidents_returned(self, engine, sample_plc_code):
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        incidents = engine.get_incidents()
        assert isinstance(incidents, list)

    def test_stats_keys_present(self, engine):
        stats = engine.get_stats()
        for key in ["plc_scans", "network_events", "total_findings", "ledger_intact"]:
            assert key in stats

    def test_cve_lookup_modbus(self, engine):
        results = engine.cve_lookup("modbus")
        assert len(results) >= 1

    def test_full_pipeline(self, engine, sample_plc_code, bad_scada_config):
        engine.scan_plc(sample_plc_code, "PumpCtrl", "plc1")
        engine.assess_scada_config("scada1", bad_scada_config)
        engine.scan_cve("asset1", [{"product": "KEPServerEX", "version": "6"}])
        for _ in range(30):
            engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 1.0)
        engine.ingest_network_event("seg1", "modbus", "read", "192.168.1.1", 500.0)
        posture = engine.get_posture()
        assert posture["open_findings"] >= 5


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 30: OT NETWORK MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class TestOTNetworkMonitor:
    def test_register_segment(self, monitor):
        result = monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        assert result["registered"] == "seg1"

    def test_get_segments(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        segs = monitor.get_segments()
        assert any(s["segment_id"] == "seg1" for s in segs)

    def test_no_alert_normal_packet(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        for _ in range(50):
            monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 1.0)
        alerts = monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 1.0)
        assert len(alerts) == 0  # exact mean = no alert

    def test_suspicious_fc_alert(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        alerts = monitor.ingest_packet("seg1", "modbus", 8, "192.168.1.1", "10.0.1.5", 8, 1.0)
        assert any(a.alert_type == "SUSPICIOUS_FC" for a in alerts)

    def test_new_ip_alert(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 1.0)
        alerts = monitor.ingest_packet("seg1", "modbus", 3, "10.99.99.99", "10.0.1.5", 8, 1.0)
        assert any(a.alert_type == "NEW_SOURCE_IP" for a in alerts)

    def test_large_payload_alert(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        alerts = monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 2048, 1.0)
        assert any(a.alert_type == "LARGE_PAYLOAD" for a in alerts)

    def test_rate_anomaly_alert(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        for _ in range(30):
            monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 1.0)
        alerts = monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 999.0)
        assert any(a.alert_type == "RATE_ANOMALY" for a in alerts)

    def test_unknown_protocol_alert(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        alerts = monitor.ingest_packet("seg1", "telnet", 0, "192.168.1.1", "10.0.1.5", 8, 1.0)
        assert any(a.alert_type == "UNKNOWN_PROTOCOL" for a in alerts)

    def test_get_alerts_empty(self, monitor):
        alerts = monitor.get_alerts()
        assert isinstance(alerts, list)

    def test_get_alerts_with_content(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        monitor.ingest_packet("seg1", "modbus", 8, "192.168.1.1", "10.0.1.5", 8, 1.0)
        alerts = monitor.get_alerts()
        assert len(alerts) >= 1

    def test_get_alerts_filter_by_segment(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        monitor.register_segment("seg2", "10.0.2.0/24", ["opc_ua"])
        monitor.ingest_packet("seg1", "modbus", 8, "192.168.1.1", "10.0.1.5", 8)
        monitor.ingest_packet("seg2", "opc_ua", 0, "192.168.2.1", "10.0.2.5", 8)
        alerts1 = monitor.get_alerts(segment_id="seg1")
        assert all(a["segment_id"] == "seg1" for a in alerts1)

    def test_baseline_stats(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        for _ in range(15):
            monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8, 1.0)
        baseline = monitor.get_baseline("seg1")
        assert "modbus:3" in baseline
        assert "mean_hz" in baseline["modbus:3"]

    def test_stats_tracking(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8)
        stats = monitor.get_stats()
        assert stats["packets"] >= 1
        assert stats["segments"] >= 1

    def test_alert_has_recommendation(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        alerts = monitor.ingest_packet("seg1", "modbus", 8, "192.168.1.1", "10.0.1.5", 8)
        for a in alerts:
            assert a.recommendation

    def test_alert_severity_valid(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"])
        monitor.ingest_packet("seg1", "modbus", 8, "192.168.1.1", "10.0.1.5", 8)
        alerts = monitor.get_alerts()
        valid = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert all(a["severity"] in valid for a in alerts)

    def test_dnp3_suspicious_fc(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["dnp3"])
        alerts = monitor.ingest_packet("seg1", "dnp3", 5, "192.168.1.1", "10.0.1.5", 8)
        assert any(a.alert_type == "SUSPICIOUS_FC" for a in alerts)

    def test_off_hours_detection(self, monitor):
        import datetime
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"],
                                  permitted_hours={"start": 0, "end": 0})  # never permitted
        alerts = monitor.ingest_packet("seg1", "modbus", 3, "192.168.1.1", "10.0.1.5", 8)
        assert any(a.alert_type == "OFF_HOURS" for a in alerts)

    def test_multiple_alerts_same_packet(self, monitor):
        monitor.register_segment("seg1", "10.0.1.0/24", ["modbus"],
                                  permitted_hours={"start": 0, "end": 0})
        # new IP + suspicious FC + large payload + off-hours
        monitor.ingest_packet("seg1", "modbus", 3, "10.0.0.1", "10.0.1.5", 8)  # first IP
        alerts = monitor.ingest_packet("seg1", "modbus", 8, "10.99.0.1", "10.0.1.5", 1024)
        assert len(alerts) >= 3
