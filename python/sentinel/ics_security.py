"""
KISWARM v4.3 — Module 29: ICS Cybersecurity Engine
===================================================
IEC 62443 Security Level assessment + 5 defensive security agents
+ MITRE ATT&CK for ICS incident correlation.

DESIGN PRINCIPLE: Observe, detect, and report — NEVER control, NEVER attack.
All agents are read-only and passive. No exploit generation.
"""

import hashlib
import json
import re
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# IEC 62443-3-3 Security Levels
SL_DESCRIPTIONS = {
    0: "No security requirement — not assessed",
    1: "Casual/coincidental violation — single-factor auth, basic logging",
    2: "Intentional simple-means attack — network segmentation, encrypted comms",
    3: "Sophisticated motivated attacker — MFA, anomaly detection, signed firmware",
    4: "State-sponsored attack — HSM, formal verification, air-gap",
}

# IEC 62443 SL requirements (simplified checklist)
SL_REQUIREMENTS: Dict[int, List[str]] = {
    1: ["authentication", "basic_logging", "account_management"],
    2: ["authentication", "basic_logging", "account_management",
        "network_segmentation", "encrypted_comms", "patch_management", "audit_logging"],
    3: ["authentication", "basic_logging", "account_management",
        "network_segmentation", "encrypted_comms", "patch_management", "audit_logging",
        "mfa", "anomaly_detection", "signed_firmware", "incident_response", "physical_security"],
    4: ["authentication", "basic_logging", "account_management",
        "network_segmentation", "encrypted_comms", "patch_management", "audit_logging",
        "mfa", "anomaly_detection", "signed_firmware", "incident_response", "physical_security",
        "hsm", "formal_verification", "air_gap", "red_team_testing"],
}

# MITRE ATT&CK for ICS — tactic + technique catalogue (subset)
MITRE_ICS: Dict[str, Dict[str, str]] = {
    "T0817": {"tactic": "Initial Access",          "name": "Drive-by Compromise"},
    "T0819": {"tactic": "Initial Access",          "name": "Exploit Public-Facing Application"},
    "T0865": {"tactic": "Initial Access",          "name": "Spearphishing Attachment"},
    "T0807": {"tactic": "Execution",               "name": "Command-Line Interface"},
    "T0871": {"tactic": "Execution",               "name": "Execution through API"},
    "T0859": {"tactic": "Persistence",             "name": "Valid Accounts"},
    "T0822": {"tactic": "Persistence",             "name": "External Remote Services"},
    "T0866": {"tactic": "Lateral Movement",        "name": "Exploitation of Remote Services"},
    "T0812": {"tactic": "Lateral Movement",        "name": "Default Credentials"},
    "T0802": {"tactic": "Collection",              "name": "Automated Collection"},
    "T0811": {"tactic": "Collection",              "name": "Data from Local System"},
    "T0878": {"tactic": "Inhibit Response",        "name": "Alarm Suppression"},
    "T0800": {"tactic": "Inhibit Response",        "name": "Activate Firmware Update Mode"},
    "T0836": {"tactic": "Impair Process Control",  "name": "Modify Parameter"},
    "T0855": {"tactic": "Impair Process Control",  "name": "Unauthorized Command Message"},
    "T0804": {"tactic": "Impair Process Control",  "name": "Block Reporting Message"},
    "T0813": {"tactic": "Impact",                  "name": "Denial of Control"},
    "T0826": {"tactic": "Impact",                  "name": "Loss of Availability"},
    "T0838": {"tactic": "Impact",                  "name": "Modify Alarm Settings"},
    "T0881": {"tactic": "Impact",                  "name": "Service Stop"},
}

# Industrial CVE database (representative subset for common OT software)
INDUSTRIAL_CVE_DB: List[Dict[str, Any]] = [
    {"cve": "CVE-2020-14480", "product": "KEPServerEX", "cvss": 9.8,
     "desc": "Remote code execution via unauthenticated OPC-UA", "protocol": "opc_ua"},
    {"cve": "CVE-2021-22657", "product": "Moxa MGate", "cvss": 9.8,
     "desc": "Buffer overflow in Modbus gateway firmware", "protocol": "modbus"},
    {"cve": "CVE-2022-34151", "product": "Siemens SIMATIC", "cvss": 9.4,
     "desc": "Improper input validation in S7comm processing", "protocol": "s7comm"},
    {"cve": "CVE-2019-10953", "product": "ABB Panel Builder", "cvss": 9.8,
     "desc": "Out-of-bounds read via crafted HMI project file", "protocol": "hmi"},
    {"cve": "CVE-2018-10952", "product": "Schneider Modicon", "cvss": 8.6,
     "desc": "Unencrypted Modbus function codes allow coil manipulation", "protocol": "modbus"},
    {"cve": "CVE-2021-44228", "product": "Any Log4j-based SCADA", "cvss": 10.0,
     "desc": "Log4Shell RCE via JNDI injection in logging calls", "protocol": "generic"},
    {"cve": "CVE-2022-26134", "product": "Confluence-based historian", "cvss": 9.8,
     "desc": "OGNL injection in Confluence Server/Data Center", "protocol": "generic"},
    {"cve": "CVE-2020-12501", "product": "Wago PFC100/PFC200", "cvss": 9.8,
     "desc": "Missing authentication for critical CoDeSys functions", "protocol": "codesys"},
    {"cve": "CVE-2021-32936", "product": "Inductive Automation Ignition", "cvss": 9.8,
     "desc": "Deserialization of untrusted data in OPC-UA tag browser", "protocol": "opc_ua"},
    {"cve": "CVE-2022-34753", "product": "Schneider Easy UPS", "cvss": 9.8,
     "desc": "Command injection in UPS network management card", "protocol": "snmp"},
]

# PLC code unsafe patterns for static analysis
UNSAFE_PLC_PATTERNS: List[Dict[str, Any]] = [
    {"id": "P001", "severity": "HIGH",   "name": "missing_watchdog",
     "pattern": r"PROGRAM\s+\w+(?!.*\bWD_|.*\bWATCHDOG|.*\bTON\b.*WD)",
     "desc": "Program block has no watchdog timer — fault detection gap"},
    {"id": "P002", "severity": "HIGH",   "name": "hardcoded_threshold",
     "pattern": r":=\s*\d{3,}\.?\d*\s*;",
     "desc": "Hardcoded numeric threshold — not adjustable via HMI"},
    {"id": "P003", "severity": "HIGH",   "name": "unbounded_loop",
     "pattern": r"WHILE\s+TRUE\s+DO",
     "desc": "Unbounded WHILE TRUE loop — potential CPU lockup"},
    {"id": "P004", "severity": "MEDIUM", "name": "missing_estop_check",
     "pattern": r"IF\s+(?!.*ESTOP|.*SAFETY|.*EMERGENCY)",
     "desc": "IF condition does not reference E-STOP or safety signal"},
    {"id": "P005", "severity": "HIGH",   "name": "direct_actuator_write",
     "pattern": r"(?:valve|pump|motor|actuator)\s*:=\s*TRUE",
     "desc": "Direct actuator write without interlock check"},
    {"id": "P006", "severity": "MEDIUM", "name": "uninitialized_var_risk",
     "pattern": r"VAR\s+\w+\s*:\s*REAL\s*;(?!\s*\w+\s*:=)",
     "desc": "REAL variable declared without initialisation"},
    {"id": "P007", "severity": "LOW",    "name": "missing_range_check",
     "pattern": r"PV\s*:=\s*\w+\s*;(?!\s*IF\s+PV)",
     "desc": "Process variable assigned without subsequent range validation"},
    {"id": "P008", "severity": "HIGH",   "name": "commented_safety_block",
     "pattern": r"\(\*.*(?:ESTOP|SAFETY|INTERLOCK).*\*\)",
     "desc": "Safety-related code block has been commented out"},
    {"id": "P009", "severity": "MEDIUM", "name": "magic_number_pid",
     "pattern": r"(?:KP|KI|KD)\s*:=\s*\d+\.?\d*\s*;",
     "desc": "PID gain hardcoded — should reference named constant"},
    {"id": "P010", "severity": "HIGH",   "name": "type_cast_real_int",
     "pattern": r"INT_TO_REAL\s*\(|REAL_TO_INT\s*\(",
     "desc": "Type cast between REAL and INT — potential precision loss"},
    {"id": "P011", "severity": "MEDIUM", "name": "no_fault_output",
     "pattern": r"END_PROGRAM(?!.*fault|.*alarm|.*error)",
     "desc": "Program has no fault/alarm output variable"},
    {"id": "P012", "severity": "HIGH",   "name": "network_variable_unvalidated",
     "pattern": r"(?:OPC|SCADA|HMI)_\w+\s*:=\s*\w+\s*;(?!\s*IF)",
     "desc": "Network-sourced variable assigned without validation"},
]


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SecurityFinding:
    finding_id: str
    severity: str          # CRITICAL / HIGH / MEDIUM / LOW / INFO
    category: str          # plc_code / network / scada_config / cve / correlation
    title: str
    description: str
    recommendation: str
    asset_id: str
    mitre_technique: Optional[str] = None
    confidence: float = 1.0
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "recommendation": self.recommendation,
            "asset_id": self.asset_id,
            "mitre_technique": self.mitre_technique,
            "mitre_tactic": MITRE_ICS.get(self.mitre_technique or "", {}).get("tactic"),
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


@dataclass
class IEC62443Assessment:
    asset_id: str
    target_sl: int
    achieved_sl: int
    compliant: bool
    controls_present: List[str]
    controls_missing: List[str]
    findings: List[SecurityFinding]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "target_sl": self.target_sl,
            "achieved_sl": self.achieved_sl,
            "compliant": self.compliant,
            "sl_description": SL_DESCRIPTIONS.get(self.achieved_sl, ""),
            "controls_present": self.controls_present,
            "controls_missing": self.controls_missing,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "timestamp": self.timestamp,
        }


@dataclass
class SecurityPosture:
    overall_score: float          # 0.0 (critical) – 1.0 (excellent)
    sl_achieved: int
    sl_target: int
    open_findings: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    last_scan: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return vars(self)


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY INCIDENT LEDGER (SHA-256 chained)
# ─────────────────────────────────────────────────────────────────────────────

class SecurityLedger:
    def __init__(self):
        self._entries: List[Dict[str, Any]] = []
        self._chain_hash = "0" * 64

    def append(self, finding: SecurityFinding) -> str:
        entry = {"finding": finding.to_dict(), "prev_hash": self._chain_hash}
        raw = json.dumps(entry, sort_keys=True).encode()
        self._chain_hash = hashlib.sha256(raw).hexdigest()
        entry["hash"] = self._chain_hash
        self._entries.append(entry)
        return self._chain_hash

    def verify_integrity(self) -> bool:
        prev = "0" * 64
        for e in self._entries:
            check = {"finding": e["finding"], "prev_hash": prev}
            raw = json.dumps(check, sort_keys=True).encode()
            expected = hashlib.sha256(raw).hexdigest()
            if e["hash"] != expected:
                return False
            prev = e["hash"]
        return True

    def get_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [e["finding"] for e in self._entries[-limit:]]

    def __len__(self) -> int:
        return len(self._entries)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 1: CVE INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

class CVEIntelligenceAgent:
    """Matches observed software/firmware against industrial CVE database."""

    def scan(self, asset_id: str, software_inventory: List[Dict[str, str]]) -> List[SecurityFinding]:
        """
        software_inventory: [{"product": "KEPServerEX", "version": "6.5.0"}, ...]
        """
        findings: List[SecurityFinding] = []
        for item in software_inventory:
            product = item.get("product", "").lower()
            for cve in INDUSTRIAL_CVE_DB:
                if cve["product"].lower() in product or product in cve["product"].lower():
                    sev = "CRITICAL" if cve["cvss"] >= 9.0 else ("HIGH" if cve["cvss"] >= 7.0 else "MEDIUM")
                    findings.append(SecurityFinding(
                        finding_id=f"CVE-{cve['cve']}-{asset_id[:8]}",
                        severity=sev,
                        category="cve",
                        title=f"Known CVE: {cve['cve']} in {cve['product']}",
                        description=f"{cve['desc']} (CVSS {cve['cvss']})",
                        recommendation="Apply vendor security patch immediately. Check vendor advisory for affected versions.",
                        asset_id=asset_id,
                        mitre_technique="T0866",
                        confidence=0.85,
                    ))
        return findings

    def lookup(self, protocol: str) -> List[Dict[str, Any]]:
        return [c for c in INDUSTRIAL_CVE_DB if c["protocol"] == protocol or c["protocol"] == "generic"]


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 2: NETWORK ANOMALY DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class NetworkAnomalyDetector:
    """Statistical baseline on OT protocol traffic; z-score anomaly alerts."""

    def __init__(self, window: int = 100, sigma_threshold: float = 3.0):
        self._window = window
        self._sigma = sigma_threshold
        # {protocol: {command: [rates]}}
        self._baselines: Dict[str, Dict[str, List[float]]] = {}
        self._known_ips: Dict[str, set] = {}  # {asset_id: {ip set}}
        self._events: List[Dict[str, Any]] = []

    def ingest_event(self, asset_id: str, protocol: str, command: str,
                     src_ip: str, rate_hz: float) -> Optional[SecurityFinding]:
        # Track known IPs
        if asset_id not in self._known_ips:
            self._known_ips[asset_id] = set()
        new_src = src_ip not in self._known_ips[asset_id]
        self._known_ips[asset_id].add(src_ip)

        # New unknown IP alert
        if new_src and len(self._known_ips[asset_id]) > 1:
            finding = SecurityFinding(
                finding_id=f"NET-NEWIP-{hashlib.md5(f'{asset_id}{src_ip}'.encode()).hexdigest()[:8]}",
                severity="HIGH",
                category="network",
                title=f"New unknown source IP {src_ip} on {protocol}",
                description=f"First-time communication from {src_ip} to asset {asset_id} over {protocol}/{command}.",
                recommendation="Verify if this IP belongs to an authorized engineering station. Block if unknown.",
                asset_id=asset_id,
                mitre_technique="T0866",
                confidence=0.92,
            )
            self._events.append({"ip": src_ip, "protocol": protocol})
            return finding

        # Update baseline
        key = f"{protocol}:{command}"
        if protocol not in self._baselines:
            self._baselines[protocol] = {}
        if command not in self._baselines[protocol]:
            self._baselines[protocol][command] = []
        history = self._baselines[protocol][command]
        history.append(rate_hz)
        if len(history) > self._window:
            history.pop(0)

        # Check anomaly if enough history
        if len(history) >= 10:
            mean = sum(history) / len(history)
            var = sum((x - mean) ** 2 for x in history) / len(history)
            std = math.sqrt(var) if var > 0 else 0.001
            z = abs(rate_hz - mean) / std

            if z > self._sigma:
                tactic = "T0855" if "write" in command.lower() else "T0804"
                return SecurityFinding(
                    finding_id=f"NET-ANOM-{hashlib.md5(f'{asset_id}{protocol}{command}'.encode()).hexdigest()[:8]}",
                    severity="HIGH" if z > 5.0 else "MEDIUM",
                    category="network",
                    title=f"Rate anomaly: {protocol}/{command} at {rate_hz:.1f} Hz (z={z:.1f}σ)",
                    description=f"Command rate {rate_hz:.2f} Hz is {z:.1f} standard deviations from baseline mean {mean:.2f} Hz.",
                    recommendation="Investigate source. May indicate replay attack, misconfigured device, or unauthorized automation.",
                    asset_id=asset_id,
                    mitre_technique=tactic,
                    confidence=min(0.5 + z * 0.05, 0.99),
                )
        return None

    def get_baselines(self) -> Dict[str, Any]:
        result = {}
        for proto, cmds in self._baselines.items():
            result[proto] = {}
            for cmd, history in cmds.items():
                if history:
                    mean = sum(history) / len(history)
                    var = sum((x - mean) ** 2 for x in history) / len(history)
                    result[proto][cmd] = {"mean_hz": round(mean, 3), "std_hz": round(math.sqrt(var), 3), "samples": len(history)}
        return result


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 3: PLC CODE SECURITY SCANNER
# ─────────────────────────────────────────────────────────────────────────────

class PLCCodeScanner:
    """Static analysis of IEC 61131-3 ST for 12 security anti-patterns."""

    def scan(self, source: str, program_name: str, asset_id: str) -> List[SecurityFinding]:
        findings: List[SecurityFinding] = []
        for pat in UNSAFE_PLC_PATTERNS:
            if re.search(pat["pattern"], source, re.IGNORECASE | re.DOTALL):
                sev = pat["severity"]
                mitre = {
                    "HIGH":   "T0836",
                    "MEDIUM": "T0855",
                    "LOW":    "T0804",
                }.get(sev, "T0836")
                findings.append(SecurityFinding(
                    finding_id=f"PLC-{pat['id']}-{hashlib.md5((program_name + pat['id']).encode()).hexdigest()[:8]}",
                    severity=sev,
                    category="plc_code",
                    title=f"[{pat['id']}] {pat['name']} in {program_name}",
                    description=pat["desc"],
                    recommendation=self._recommendation(pat["id"]),
                    asset_id=asset_id,
                    mitre_technique=mitre,
                    confidence=0.78,
                ))
        return findings

    @staticmethod
    def _recommendation(pat_id: str) -> str:
        recs = {
            "P001": "Add a watchdog timer (WD_) at program entry. Set timeout to 2× scan cycle.",
            "P002": "Replace magic numbers with named constants in VAR_GLOBAL block.",
            "P003": "Replace WHILE TRUE with bounded loop or restructure using cyclic task.",
            "P004": "Add ESTOP/SAFETY signal check at top of every IF chain controlling actuators.",
            "P005": "Gate all actuator writes with interlock conditions. Use E-STOP AND NOT fault.",
            "P006": "Initialize all REAL variables explicitly at declaration.",
            "P007": "Add range validation: IF PV < MIN_PV OR PV > MAX_PV THEN fault := TRUE; END_IF",
            "P008": "Restore and test commented safety block. Do not comment out safety logic.",
            "P009": "Move PID gains to VAR_GLOBAL constants with descriptive names.",
            "P010": "Validate bounds before type cast. Use LIMIT function where possible.",
            "P011": "Add fault output variable, set TRUE on any exception path.",
            "P012": "Validate all network-sourced values: range check, rate-of-change check, substitution on timeout.",
        }
        return recs.get(pat_id, "Review and remediate according to IEC 62443-3-3 SR 3.5.")


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 4: SCADA CONFIGURATION ASSESSOR
# ─────────────────────────────────────────────────────────────────────────────

class SCADAConfigAssessor:
    """Checks SCADA system configuration against IEC 62443 best practices."""

    def assess(self, asset_id: str, config: Dict[str, Any]) -> Tuple[List[SecurityFinding], List[str]]:
        """
        config keys (all optional, defaults to worst-case if missing):
          encryption_enabled, auth_type, patch_level_days,
          default_creds_changed, unnecessary_services, audit_log_enabled,
          remote_access_mfa, firmware_signed
        """
        findings: List[SecurityFinding] = []
        controls_present: List[str] = []

        checks = [
            ("encryption_enabled", True, "CRITICAL", "encrypted_comms",
             "Encrypted comms not enabled",
             "Enable TLS 1.2+ for all OPC-UA and historian connections.",
             "T0855"),
            ("auth_type", "strong", "HIGH", "authentication",
             "Weak or default authentication",
             "Use certificate-based or at minimum username+complex-password auth.",
             "T0812"),
            ("patch_level_days", 90, "HIGH", "patch_management",
             "System not patched within 90 days",
             "Apply latest vendor patches. Subscribe to ICS-CERT advisories.",
             "T0819"),
            ("default_creds_changed", True, "CRITICAL", "account_management",
             "Default credentials not changed",
             "Change all default passwords immediately. Enforce password policy.",
             "T0812"),
            ("unnecessary_services", False, "MEDIUM", "network_segmentation",
             "Unnecessary network services running",
             "Disable FTP, Telnet, HTTP, SNMP v1/v2 if not required.",
             "T0817"),
            ("audit_log_enabled", True, "HIGH", "audit_logging",
             "Audit logging not enabled",
             "Enable detailed audit logging for all login attempts and config changes.",
             "T0804"),
            ("remote_access_mfa", True, "HIGH", "mfa",
             "Remote access does not require MFA",
             "Enforce MFA for all remote engineering station access.",
             "T0822"),
            ("firmware_signed", True, "HIGH", "signed_firmware",
             "Firmware integrity not verified (signing disabled)",
             "Enable firmware signing verification on all field devices.",
             "T0800"),
        ]

        for key, good_val, sev, ctrl_name, bad_title, rec, mitre in checks:
            val = config.get(key)
            ok = False
            if val is None:
                ok = False  # conservative: missing = not present
            elif isinstance(good_val, bool):
                ok = bool(val) == good_val
            elif isinstance(good_val, (int, float)):
                ok = isinstance(val, (int, float)) and val <= good_val
            else:
                ok = str(val).lower() == str(good_val).lower()

            if ok:
                controls_present.append(ctrl_name)
            else:
                findings.append(SecurityFinding(
                    finding_id=f"SCADA-{ctrl_name.upper()[:6]}-{asset_id[:6]}",
                    severity=sev,
                    category="scada_config",
                    title=bad_title,
                    description=f"Asset {asset_id}: {bad_title.lower()}. IEC 62443 SL2+ requirement not met.",
                    recommendation=rec,
                    asset_id=asset_id,
                    mitre_technique=mitre,
                    confidence=0.95,
                ))

        return findings, controls_present


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 5: INCIDENT CORRELATION ENGINE (MITRE ATT&CK for ICS)
# ─────────────────────────────────────────────────────────────────────────────

class IncidentCorrelationEngine:
    """Correlates findings from all agents into unified incidents via ATT&CK."""

    # Tactic escalation chains (simplified kill chain)
    KILL_CHAIN_STAGES = [
        "Initial Access", "Execution", "Persistence", "Lateral Movement",
        "Collection", "Inhibit Response", "Impair Process Control", "Impact"
    ]

    def __init__(self):
        self._incidents: List[Dict[str, Any]] = []
        self._finding_buffer: List[SecurityFinding] = []

    def ingest(self, finding: SecurityFinding) -> None:
        self._finding_buffer.append(finding)

    def correlate(self) -> List[Dict[str, Any]]:
        """Group findings by MITRE tactic, detect kill-chain progression."""
        by_tactic: Dict[str, List[SecurityFinding]] = {}
        for f in self._finding_buffer:
            tactic = (MITRE_ICS.get(f.mitre_technique or "", {}).get("tactic") or "Unknown")
            by_tactic.setdefault(tactic, []).append(f)

        incidents = []
        for tactic, flist in by_tactic.items():
            # Multi-finding incidents escalate severity
            max_sev = max(
                ["LOW", "INFO", "MEDIUM", "HIGH", "CRITICAL"].index(f.severity)
                for f in flist
            )
            sev_name = ["LOW", "INFO", "MEDIUM", "HIGH", "CRITICAL"][max_sev]

            stage_idx = self.KILL_CHAIN_STAGES.index(tactic) if tactic in self.KILL_CHAIN_STAGES else -1

            incident = {
                "incident_id": hashlib.md5(f"{tactic}{len(flist)}".encode()).hexdigest()[:12],
                "tactic": tactic,
                "kill_chain_stage": stage_idx + 1 if stage_idx >= 0 else "N/A",
                "severity": sev_name,
                "finding_count": len(flist),
                "asset_ids": list({f.asset_id for f in flist}),
                "mitre_techniques": list({f.mitre_technique for f in flist if f.mitre_technique}),
                "summary": f"{len(flist)} finding(s) mapped to ATT&CK tactic: {tactic}",
                "correlated_at": datetime.datetime.now().isoformat(),
            }
            incidents.append(incident)
            self._incidents.append(incident)

        # Sort by kill chain stage (later = more dangerous)
        incidents.sort(key=lambda i: i["kill_chain_stage"] if isinstance(i["kill_chain_stage"], int) else 0, reverse=True)
        self._finding_buffer.clear()
        return incidents

    def get_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._incidents[-limit:]


# ─────────────────────────────────────────────────────────────────────────────
# IEC 62443 ASSESSOR
# ─────────────────────────────────────────────────────────────────────────────

class IEC62443Assessor:
    """Determine which Security Level an asset achieves."""

    def assess(self, asset_id: str, target_sl: int,
               controls_present: List[str],
               findings: List[SecurityFinding]) -> IEC62443Assessment:
        if target_sl not in range(1, 5):
            raise ValueError(f"target_sl must be 1–4, got {target_sl}")

        # Determine highest SL fully satisfied
        achieved = 0
        controls_missing_final: List[str] = []
        for sl in range(1, target_sl + 1):
            required = SL_REQUIREMENTS[sl]
            missing = [r for r in required if r not in controls_present]
            if missing:
                controls_missing_final = missing
                break
            achieved = sl

        compliant = achieved >= target_sl

        return IEC62443Assessment(
            asset_id=asset_id,
            target_sl=target_sl,
            achieved_sl=achieved,
            compliant=compliant,
            controls_present=controls_present,
            controls_missing=controls_missing_final,
            findings=findings,
        )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ICS SECURITY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class ICSSecurityEngine:
    """
    KISWARM v4.3 — ICS Cybersecurity Engine
    Integrates all 5 defensive agents with IEC 62443 + MITRE ATT&CK for ICS.
    DEFENSIVE ONLY — no exploit generation, no active probing.
    """

    def __init__(self):
        self.cve_agent        = CVEIntelligenceAgent()
        self.network_agent    = NetworkAnomalyDetector()
        self.plc_scanner      = PLCCodeScanner()
        self.scada_assessor   = SCADAConfigAssessor()
        self.correlator       = IncidentCorrelationEngine()
        self.iec62443         = IEC62443Assessor()
        self.ledger           = SecurityLedger()

        self._all_findings: List[SecurityFinding] = []
        self._posture_target_sl: int = 2
        self._stats: Dict[str, int] = {
            "plc_scans": 0,
            "network_events": 0,
            "cve_lookups": 0,
            "scada_assessments": 0,
            "iec62443_assessments": 0,
            "total_findings": 0,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def scan_plc(self, source: str, program_name: str, asset_id: str = "unknown") -> Dict[str, Any]:
        """Static security analysis of PLC Structured Text code."""
        findings = self.plc_scanner.scan(source, program_name, asset_id)
        for f in findings:
            self._record(f)
        self._stats["plc_scans"] += 1
        return {
            "program_name": program_name,
            "asset_id": asset_id,
            "finding_count": len(findings),
            "findings": [f.to_dict() for f in findings],
            "severity_summary": self._sev_summary(findings),
        }

    def ingest_network_event(self, asset_id: str, protocol: str, command: str,
                              src_ip: str, rate_hz: float) -> Dict[str, Any]:
        """Ingest OT network event metadata for anomaly analysis."""
        finding = self.network_agent.ingest_event(asset_id, protocol, command, src_ip, rate_hz)
        self._stats["network_events"] += 1
        result: Dict[str, Any] = {"anomaly": False}
        if finding:
            self._record(finding)
            result = {"anomaly": True, "finding": finding.to_dict()}
        return result

    def scan_cve(self, asset_id: str, software_inventory: List[Dict[str, str]]) -> Dict[str, Any]:
        """Match software inventory against industrial CVE database."""
        findings = self.cve_agent.scan(asset_id, software_inventory)
        for f in findings:
            self._record(f)
        self._stats["cve_lookups"] += 1
        return {
            "asset_id": asset_id,
            "matches": len(findings),
            "findings": [f.to_dict() for f in findings],
        }

    def assess_scada_config(self, asset_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Assess SCADA system configuration against IEC 62443 controls."""
        findings, controls_present = self.scada_assessor.assess(asset_id, config)
        for f in findings:
            self._record(f)
        self._stats["scada_assessments"] += 1
        return {
            "asset_id": asset_id,
            "controls_present": controls_present,
            "finding_count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }

    def iec62443_assess(self, asset_id: str, target_sl: int,
                         controls_present: Optional[List[str]] = None,
                         scada_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Full IEC 62443 Security Level assessment for an asset."""
        all_controls: List[str] = list(controls_present or [])
        all_findings: List[SecurityFinding] = []

        if scada_config:
            f, c = self.scada_assessor.assess(asset_id, scada_config)
            all_controls.extend(c)
            all_findings.extend(f)

        assessment = self.iec62443.assess(asset_id, target_sl, all_controls, all_findings)
        for f in all_findings:
            self._record(f)

        self._posture_target_sl = target_sl
        self._stats["iec62443_assessments"] += 1
        return assessment.to_dict()

    def get_incidents(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Correlate buffered findings into MITRE ATT&CK incidents."""
        # Ingest all recent findings into correlator
        for f in self._all_findings[-100:]:
            self.correlator.ingest(f)
        incidents = self.correlator.correlate()
        return incidents[:limit]

    def get_posture(self) -> Dict[str, Any]:
        """Current security posture summary."""
        findings = self._all_findings
        sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
        for f in findings:
            sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1

        # Score: start at 1.0, deduct per finding severity
        score = 1.0
        score -= sev_counts["CRITICAL"] * 0.20
        score -= sev_counts["HIGH"] * 0.08
        score -= sev_counts["MEDIUM"] * 0.03
        score -= sev_counts["LOW"] * 0.01
        score = max(0.0, score)

        # Rough SL from score
        if score >= 0.85:
            sl = 3
        elif score >= 0.65:
            sl = 2
        elif score >= 0.40:
            sl = 1
        else:
            sl = 0

        return SecurityPosture(
            overall_score=round(score, 3),
            sl_achieved=sl,
            sl_target=self._posture_target_sl,
            open_findings=len(findings),
            critical_count=sev_counts["CRITICAL"],
            high_count=sev_counts["HIGH"],
            medium_count=sev_counts["MEDIUM"],
            low_count=sev_counts["LOW"],
            last_scan=findings[-1].timestamp if findings else None,
        ).to_dict()

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "total_findings": len(self._all_findings),
            "ledger_entries": len(self.ledger),
            "ledger_intact": self.ledger.verify_integrity(),
        }

    def get_ledger(self, limit: int = 50) -> Dict[str, Any]:
        return {
            "entries": self.ledger.get_all(limit),
            "total": len(self.ledger),
            "intact": self.ledger.verify_integrity(),
        }

    def cve_lookup(self, protocol: str) -> List[Dict[str, Any]]:
        self._stats["cve_lookups"] += 1
        return self.cve_agent.lookup(protocol)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _record(self, finding: SecurityFinding) -> None:
        self._all_findings.append(finding)
        self.ledger.append(finding)
        self._stats["total_findings"] = len(self._all_findings)

    @staticmethod
    def _sev_summary(findings: List[SecurityFinding]) -> Dict[str, int]:
        s: Dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            s[f.severity] = s.get(f.severity, 0) + 1
        return s
