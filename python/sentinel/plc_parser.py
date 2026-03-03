"""
KISWARM v4.0 — Module 11: PLC Semantic Parser
==============================================
Parses IEC 61131-3 Structured Text (ST) programs into:
  1. Abstract Syntax Tree (AST)
  2. Canonical Intermediate Representation (CIR)
  3. Directed Signal Graph (DSG)
  4. Detected control patterns (PID, interlocks, watchdogs)

Architecture: pure Python, no external parser dependencies.
Supports: ST (Structured Text), simplified LD/FBD pattern detection.

PLC = deterministic reflex layer. AI = adaptive cognition layer.
Never invert that hierarchy.
"""

from __future__ import annotations

import re
import math
import hashlib
import logging
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Node Types ──────────────────────────────────────────────────────────────

NODE_TYPES = {
    "PID", "TON", "TOF", "CTU", "CTD", "COIL", "CONTACT",
    "COMPARE", "MATH", "MOVE", "FB", "IF", "WHILE", "CASE",
    "VAR", "ASSIGN", "CALL", "INTERLOCK", "WATCHDOG", "EMERGENCY",
}


@dataclass
class CIRNode:
    """
    Canonical Intermediate Representation node.
    Represents a single functional block in the PLC program.
    """
    node_id:      str
    node_type:    str                      # from NODE_TYPES
    inputs:       list[str] = field(default_factory=list)
    outputs:      list[str] = field(default_factory=list)
    memory_bits:  list[str] = field(default_factory=list)
    params:       dict      = field(default_factory=dict)   # e.g. Kp, Ki, Kd
    safety_flag:  bool      = False
    line_number:  int        = 0
    raw_text:     str        = ""

    def to_dict(self) -> dict:
        return {
            "id":          self.node_id,
            "type":        self.node_type,
            "inputs":      self.inputs,
            "outputs":     self.outputs,
            "memory_bits": self.memory_bits,
            "params":      self.params,
            "safety_flag": self.safety_flag,
            "line":        self.line_number,
        }


@dataclass
class DSGEdge:
    """Directed Signal Graph edge — signal flow between nodes."""
    source:      str
    target:      str
    signal_name: str
    edge_type:   str = "data"   # data | control | feedback


@dataclass
class PIDBlock:
    """Extracted PID control loop parameters."""
    block_id:    str
    setpoint_var: str
    process_var:  str
    output_var:   str
    kp:          float = 1.0
    ki:          float = 0.1
    kd:          float = 0.01
    sample_time: float = 0.1    # seconds
    output_min:  float = 0.0
    output_max:  float = 100.0
    source_line: int   = 0

    def to_dict(self) -> dict:
        return {
            "block_id":    self.block_id,
            "setpoint":    self.setpoint_var,
            "process_var": self.process_var,
            "output":      self.output_var,
            "kp": self.kp, "ki": self.ki, "kd": self.kd,
            "sample_time": self.sample_time,
            "output_min":  self.output_min,
            "output_max":  self.output_max,
        }


@dataclass
class ParseResult:
    """Full parse result for a PLC program."""
    program_name:  str
    source_hash:   str
    nodes:         list[CIRNode]   = field(default_factory=list)
    edges:         list[DSGEdge]   = field(default_factory=list)
    pid_blocks:    list[PIDBlock]  = field(default_factory=list)
    interlocks:    list[dict]      = field(default_factory=list)
    watchdogs:     list[dict]      = field(default_factory=list)
    variables:     dict            = field(default_factory=dict)
    safety_flags:  list[str]       = field(default_factory=list)
    parse_errors:  list[str]       = field(default_factory=list)
    parse_time_ms: float           = 0.0

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def to_dict(self) -> dict:
        return {
            "program":     self.program_name,
            "hash":        self.source_hash,
            "nodes":       [n.to_dict() for n in self.nodes],
            "pid_blocks":  [p.to_dict() for p in self.pid_blocks],
            "interlocks":  self.interlocks,
            "watchdogs":   self.watchdogs,
            "variables":   self.variables,
            "safety_flags": self.safety_flags,
            "parse_errors": self.parse_errors,
            "stats": {
                "node_count": self.node_count,
                "edge_count": self.edge_count,
                "pid_count":  len(self.pid_blocks),
                "interlock_count": len(self.interlocks),
            },
        }


# ── Tokenizer ────────────────────────────────────────────────────────────────

_TOKEN_PATTERNS = [
    ("KEYWORD",  r'\b(IF|THEN|ELSE|ELSIF|END_IF|WHILE|DO|END_WHILE|FOR|TO|BY|END_FOR'
                 r'|CASE|OF|END_CASE|VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|END_VAR'
                 r'|PROGRAM|FUNCTION_BLOCK|FUNCTION|END_PROGRAM|END_FUNCTION_BLOCK|END_FUNCTION'
                 r'|AND|OR|NOT|XOR|TRUE|FALSE|RETURN|EXIT)\b'),
    ("ASSIGN",   r':='),
    ("COMPARE",  r'[<>]=?|<>|='),
    ("ARITH",    r'[+\-*/]'),
    ("LPAREN",   r'\('),
    ("RPAREN",   r'\)'),
    ("LBRACKET", r'\['),
    ("RBRACKET", r'\]'),
    ("SEMICOLON",r';'),
    ("COLON",    r':'),
    ("DOT",      r'\.'),
    ("COMMA",    r','),
    ("NUMBER",   r'\d+\.?\d*(?:[eE][+-]?\d+)?'),
    ("STRING",   r"'[^']*'"),
    ("IDENT",    r'[A-Za-z_][A-Za-z0-9_]*'),
    ("COMMENT",  r'\(\*.*?\*\)|//[^\n]*'),
    ("NEWLINE",  r'\n'),
    ("SPACE",    r'[ \t]+'),
]

_TOKEN_RE = re.compile(
    '|'.join(f'(?P<{name}>{pat})' for name, pat in _TOKEN_PATTERNS),
    re.DOTALL | re.IGNORECASE,
)


def tokenize(source: str) -> list[tuple[str, str, int]]:
    """
    Tokenize IEC 61131-3 ST source.
    Returns list of (token_type, value, line_number).
    """
    tokens = []
    line   = 1
    for mo in _TOKEN_RE.finditer(source):
        kind  = mo.lastgroup
        value = mo.group()
        if kind == "NEWLINE":
            line += 1
            continue
        if kind in ("SPACE", "COMMENT"):
            continue
        tokens.append((kind, value, line))
    return tokens


# ── Variable Declaration Parser ───────────────────────────────────────────────

_VAR_DECL_RE = re.compile(
    r'VAR(?:_INPUT|_OUTPUT|_IN_OUT)?\b(.*?)END_VAR',
    re.DOTALL | re.IGNORECASE,
)
_VAR_LINE_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)'
    r'(?:\s*:=\s*([^;]+))?',
)


def extract_variables(source: str) -> dict[str, dict]:
    """
    Extract variable declarations from ST source.
    Returns {var_name: {type, initial_value, section}}.
    """
    variables = {}
    for section_match in _VAR_DECL_RE.finditer(source):
        section_text = section_match.group(1)
        # Determine section type
        full_match   = section_match.group(0)
        section_type = "VAR"
        for stype in ("VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"):
            if full_match.upper().startswith(stype):
                section_type = stype
                break

        for line in section_text.split(";"):
            m = _VAR_LINE_RE.search(line)
            if m:
                name     = m.group(1).strip()
                var_type = m.group(2).strip()
                init_val = m.group(3).strip() if m.group(3) else None
                variables[name] = {
                    "type":          var_type,
                    "initial_value": init_val,
                    "section":       section_type,
                }
    return variables


# ── PID Pattern Detector ──────────────────────────────────────────────────────

_PID_CALL_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*'
    r'(?:[A-Za-z_]+\s*:=\s*([^,\)]+),?\s*)*'
    r'\)',
    re.IGNORECASE,
)

_PID_PARAM_RE = {
    "kp":      re.compile(r'\b(?:KP|Kp|kp)\s*:=\s*([\d\.eE+\-]+)'),
    "ki":      re.compile(r'\b(?:KI|Ki|ki|TN|tn)\s*:=\s*([\d\.eE+\-]+)'),
    "kd":      re.compile(r'\b(?:KD|Kd|kd|TV|tv)\s*:=\s*([\d\.eE+\-]+)'),
    "setpoint":re.compile(r'\b(?:SP|Setpoint|setpoint|SET|W)\s*:=\s*([A-Za-z_][A-Za-z0-9_\.]*)'),
    "pv":      re.compile(r'\b(?:PV|ProcessVar|process_var|ACT|X)\s*:=\s*([A-Za-z_][A-Za-z0-9_\.]*)'),
    "output":  re.compile(r'\b(?:OUT|Output|output|Y|MV)\s*\s*:=\s*([A-Za-z_][A-Za-z0-9_\.]*)'),
    "out_min": re.compile(r'\b(?:OUTMIN|OutMin|LIM_LO)\s*:=\s*([\d\.eE+\-]+)'),
    "out_max": re.compile(r'\b(?:OUTMAX|OutMax|LIM_HI)\s*:=\s*([\d\.eE+\-]+)'),
}

# FB type names that indicate PID blocks
_PID_TYPE_NAMES = {
    "PID", "PID_CONTROLLER", "FB_PID", "PID_COMPACT", "PIDCONTROL",
    "PID_FIXED_SETPOINT", "PID_EXTSETPOINT", "LREAL_TO_REAL",
}


def detect_pid_blocks(source: str, variables: dict) -> list[PIDBlock]:
    """
    Scan ST source for PID function block instantiations.
    Returns list of PIDBlock with extracted parameters.
    """
    pid_blocks = []
    seen_ids   = set()

    # Look for lines matching FB_PID style patterns
    lines = source.split("\n")
    for line_no, line in enumerate(lines, start=1):
        upper = line.upper()

        # Check if this line contains a PID-type call
        if not any(pid_name in upper for pid_name in _PID_TYPE_NAMES):
            # Also check for raw PID pattern: error := SP - PV; output := PID(error)
            if "PID" not in upper:
                continue

        block_id = f"PID_{line_no}"
        if block_id in seen_ids:
            continue
        seen_ids.add(block_id)

        # Extract parameters from surrounding context (±5 lines)
        ctx_start = max(0, line_no - 5)
        ctx_end   = min(len(lines), line_no + 5)
        context   = "\n".join(lines[ctx_start:ctx_end])

        kp = ki = kd = 1.0, 0.1, 0.01
        sp_var = pv_var = out_var = ""
        out_min, out_max = 0.0, 100.0

        for param, pattern in _PID_PARAM_RE.items():
            m = pattern.search(context)
            if m:
                val = m.group(1).strip()
                if param == "kp":
                    try: kp = (float(val),)
                    except ValueError: pass
                elif param == "ki":
                    try: ki = (float(val),)
                    except ValueError: pass
                elif param == "kd":
                    try: kd = (float(val),)
                    except ValueError: pass
                elif param == "setpoint":
                    sp_var = val
                elif param == "pv":
                    pv_var = val
                elif param == "output":
                    out_var = val
                elif param == "out_min":
                    try: out_min = float(val)
                    except ValueError: pass
                elif param == "out_max":
                    try: out_max = float(val)
                    except ValueError: pass

        pid_blocks.append(PIDBlock(
            block_id    = block_id,
            setpoint_var= sp_var,
            process_var = pv_var,
            output_var  = out_var,
            kp          = kp[0] if isinstance(kp, tuple) else kp,
            ki          = ki[0] if isinstance(ki, tuple) else ki,
            kd          = kd[0] if isinstance(kd, tuple) else kd,
            output_min  = out_min,
            output_max  = out_max,
            source_line = line_no,
        ))

    return pid_blocks


# ── Pattern Detectors ─────────────────────────────────────────────────────────

def detect_interlocks(source: str) -> list[dict]:
    """
    Detect interlock logic patterns.
    An interlock is any IF-block whose condition references a safety signal.
    Scans multi-line: condition on IF line, action may be on next lines.
    """
    interlocks = []
    lines      = source.split("\n")

    # Safety signals that indicate an interlock condition
    safety_keywords = re.compile(
        r'\b(ESTOP|E_STOP|EMERGENCY|SAFETY|FAULT|ALARM|OVERPRESSURE'
        r'|OVERCURRENT|OVERTEMP|HIGH_PRESSURE|LOCKOUT|INTERLOCK'
        r'|fault_flag|safety_ok|emer_stop)\b',
        re.IGNORECASE,
    )
    # IF … THEN on same or adjacent line
    if_re = re.compile(r'\bIF\b', re.IGNORECASE)

    for line_no, line in enumerate(lines, start=1):
        if if_re.search(line) and safety_keywords.search(line):
            condition_m = re.search(r'IF\s+(.+?)(?:THEN|$)', line, re.IGNORECASE)
            # Gather action from next non-empty line if THEN is on this line
            action = ""
            then_on_same = re.search(r'THEN\s+(.+?)(?:;|$)', line, re.IGNORECASE)
            if then_on_same:
                action = then_on_same.group(1).strip()
            elif line_no < len(lines):
                for offset in range(1, 4):
                    nxt = lines[min(line_no - 1 + offset, len(lines) - 1)].strip()
                    if nxt and not nxt.upper().startswith("(*"):
                        action = nxt
                        break
            interlocks.append({
                "line":      line_no,
                "condition": condition_m.group(1).strip() if condition_m else line.strip(),
                "action":    action,
                "raw":       line.strip(),
                "safety":    True,
            })

    return interlocks


def detect_watchdogs(source: str) -> list[dict]:
    """
    Detect watchdog timer patterns: TON/TOF with reset logic, or WD_* named instances.
    Watchdogs ensure periodic confirmation of system health.
    """
    watchdogs = []
    lines     = source.split("\n")

    # Match: WATCHDOG / WDOG / HEARTBEAT / WD_ prefixed identifiers
    watchdog_re = re.compile(
        r'\b(WATCHDOG|WDOG|HEARTBEAT|TIMEOUT_MONITOR)\b'
        r'|(?<!\w)(WD_[A-Za-z0-9_]+)',
        re.IGNORECASE,
    )
    timer_re = re.compile(r'\b(TON|TOF|TP)\b', re.IGNORECASE)

    for line_no, line in enumerate(lines, start=1):
        if watchdog_re.search(line) or (timer_re.search(line) and "watchdog" in line.lower()):
            watchdogs.append({
                "line":  line_no,
                "type":  "watchdog_timer",
                "raw":   line.strip(),
            })

    return watchdogs


def detect_safety_vars(source: str, variables: dict) -> list[str]:
    """Return variable names that appear to be safety-critical."""
    safety_re = re.compile(
        r'\b(ESTOP|EMERGENCY|FAULT|SAFETY|ALARM|LOCKOUT|INTERLOCK|HIGH_P|OVERCUR)\b',
        re.IGNORECASE,
    )
    return [
        name for name in variables
        if safety_re.search(name)
    ]


# ── CIR Builder ───────────────────────────────────────────────────────────────

def build_cir(tokens: list[tuple], source: str, variables: dict) -> list[CIRNode]:
    """
    Build Canonical Intermediate Representation from tokens.
    Each significant functional block becomes a CIRNode.
    """
    nodes   = []
    node_id = 0

    def next_id() -> str:
        nonlocal node_id
        node_id += 1
        return f"N{node_id:04d}"

    # Pass through tokens looking for FB calls, assignments, IF blocks
    i = 0
    while i < len(tokens):
        kind, value, line = tokens[i]

        # Function block call detection: IDENT LPAREN
        if kind == "IDENT" and i + 1 < len(tokens) and tokens[i + 1][0] == "LPAREN":
            fb_name   = value.upper()
            node_type = "FB"
            if fb_name in _PID_TYPE_NAMES:
                node_type = "PID"
            elif fb_name in ("TON", "TOF", "TP"):
                node_type = "TON"
            elif fb_name in ("CTU", "CTD", "CTUD"):
                node_type = "CTU"

            # Collect inputs from call arguments
            j       = i + 2
            inputs  = []
            depth   = 1
            raw_buf = [f"{value}("]
            while j < len(tokens) and depth > 0:
                tk, tv, _ = tokens[j]
                raw_buf.append(tv)
                if tk == "LPAREN":
                    depth += 1
                elif tk == "RPAREN":
                    depth -= 1
                    if depth == 0:
                        break
                elif tk == "IDENT" and j + 1 < len(tokens) and tokens[j + 1][0] == "ASSIGN":
                    inputs.append(tv)   # named parameter
                j += 1

            is_safety = any(
                kw in value.upper()
                for kw in ("SAFETY", "ESTOP", "EMERGENCY", "FAULT", "ALARM")
            )

            nodes.append(CIRNode(
                node_id   = next_id(),
                node_type = node_type,
                inputs    = inputs[:8],   # cap to 8 inputs per node
                outputs   = [],
                params    = {"name": value},
                safety_flag = is_safety,
                line_number = line,
                raw_text  = "".join(raw_buf[:50]),
            ))
            i = j + 1
            continue

        # Assignment detection: IDENT ASSIGN ...
        if kind == "IDENT" and i + 1 < len(tokens) and tokens[i + 1][0] == "ASSIGN":
            target = value
            # Scan RHS until semicolon
            j      = i + 2
            rhs    = []
            while j < len(tokens) and tokens[j][0] != "SEMICOLON":
                rhs.append(tokens[j][1])
                j += 1

            nodes.append(CIRNode(
                node_id   = next_id(),
                node_type = "ASSIGN",
                inputs    = [t for t, _ in [(r, 1) for r in rhs if re.match(r'[A-Za-z_]', r)]][:4],
                outputs   = [target],
                params    = {"rhs": " ".join(rhs[:20])},
                safety_flag = target.upper() in (
                    "ESTOP", "EMERGENCY", "FAULT", "SAFETY_OK"
                ),
                line_number = line,
            ))
            i = j + 1
            continue

        # IF block detection
        if kind == "KEYWORD" and value.upper() == "IF":
            condition_tokens = []
            j = i + 1
            while j < len(tokens) and tokens[j][1].upper() != "THEN":
                condition_tokens.append(tokens[j][1])
                j += 1
            condition = " ".join(condition_tokens[:20])
            nodes.append(CIRNode(
                node_id   = next_id(),
                node_type = "IF",
                inputs    = [t for t in condition_tokens if re.match(r'[A-Za-z_]', t)][:4],
                outputs   = [],
                params    = {"condition": condition},
                safety_flag = any(
                    kw in condition.upper()
                    for kw in ("SAFETY", "ESTOP", "FAULT", "ALARM", "EMERGENCY")
                ),
                line_number = line,
            ))
            i = j + 1
            continue

        i += 1

    return nodes


def build_dsg(nodes: list[CIRNode]) -> list[DSGEdge]:
    """
    Build Directed Signal Graph edges from CIR nodes.
    An edge exists when the output of one node feeds the input of another.
    """
    edges      = []
    output_map = {}   # signal_name → producer node_id

    # First pass: map outputs
    for node in nodes:
        for out_sig in node.outputs:
            output_map[out_sig] = node.node_id

    # Second pass: connect producers to consumers
    for node in nodes:
        for in_sig in node.inputs:
            if in_sig in output_map:
                producer = output_map[in_sig]
                if producer != node.node_id:
                    edge_type = "feedback" if _is_feedback(nodes, producer, node.node_id) else "data"
                    edges.append(DSGEdge(
                        source      = producer,
                        target      = node.node_id,
                        signal_name = in_sig,
                        edge_type   = edge_type,
                    ))

    return edges


def _is_feedback(nodes: list[CIRNode], from_id: str, to_id: str) -> bool:
    """Heuristic: node is a feedback edge if target appears before source in program order."""
    id_order = {n.node_id: i for i, n in enumerate(nodes)}
    return id_order.get(from_id, 0) > id_order.get(to_id, 0)


# ── Main Parser ────────────────────────────────────────────────────────────────

class PLCSemanticParser:
    """
    Full IEC 61131-3 Semantic Extraction Engine.

    Parses ST source → CIR → DSG → pattern detection.
    Extracts: PID blocks, interlocks, watchdogs, variable declarations.

    Usage:
        parser = PLCSemanticParser()
        result = parser.parse(st_source_code, program_name="PUMP_CTRL")
    """

    def __init__(self, store_path: Optional[str] = None):
        kiswarm_dir     = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store     = store_path or os.path.join(kiswarm_dir, "plc_parse_cache.json")
        self._cache:    dict[str, ParseResult] = {}
        self._parse_count = 0
        self._load()

    def parse(self, source: str, program_name: str = "UNKNOWN") -> ParseResult:
        """
        Full parse pipeline:
          1. Tokenize
          2. Extract variable declarations
          3. Build CIR nodes
          4. Build DSG edges
          5. Detect PID / interlock / watchdog patterns
        """
        t0          = time.perf_counter()
        src_hash    = hashlib.sha256(source.encode()).hexdigest()[:16]

        # Check cache
        if src_hash in self._cache:
            logger.debug("PLC parse cache hit: %s", src_hash)
            return self._cache[src_hash]

        errors: list[str] = []

        try:
            tokens    = tokenize(source)
        except Exception as exc:
            errors.append(f"Tokenizer error: {exc}")
            tokens    = []

        try:
            variables = extract_variables(source)
        except Exception as exc:
            errors.append(f"Variable extraction error: {exc}")
            variables = {}

        try:
            nodes = build_cir(tokens, source, variables)
        except Exception as exc:
            errors.append(f"CIR build error: {exc}")
            nodes = []

        try:
            edges = build_dsg(nodes)
        except Exception as exc:
            errors.append(f"DSG build error: {exc}")
            edges = []

        try:
            pid_blocks = detect_pid_blocks(source, variables)
        except Exception as exc:
            errors.append(f"PID detection error: {exc}")
            pid_blocks = []

        try:
            interlocks = detect_interlocks(source)
        except Exception as exc:
            errors.append(f"Interlock detection error: {exc}")
            interlocks = []

        try:
            watchdogs  = detect_watchdogs(source)
        except Exception as exc:
            errors.append(f"Watchdog detection error: {exc}")
            watchdogs  = []

        try:
            safety_flags = detect_safety_vars(source, variables)
        except Exception as exc:
            errors.append(f"Safety var detection: {exc}")
            safety_flags = []

        parse_ms = (time.perf_counter() - t0) * 1000
        self._parse_count += 1

        result = ParseResult(
            program_name  = program_name,
            source_hash   = src_hash,
            nodes         = nodes,
            edges         = edges,
            pid_blocks    = pid_blocks,
            interlocks    = interlocks,
            watchdogs     = watchdogs,
            variables     = variables,
            safety_flags  = safety_flags,
            parse_errors  = errors,
            parse_time_ms = parse_ms,
        )

        self._cache[src_hash] = result
        self._save()

        logger.info(
            "PLC parse complete: %s | nodes=%d | edges=%d | PIDs=%d | interlocks=%d | %.1fms",
            program_name, len(nodes), len(edges), len(pid_blocks), len(interlocks), parse_ms,
        )
        return result

    def parse_multiple(self, programs: list[tuple[str, str]]) -> list[ParseResult]:
        """Parse multiple (source, name) pairs and return all results."""
        return [self.parse(src, name) for src, name in programs]

    def get_stats(self) -> dict:
        return {
            "total_parses":  self._parse_count,
            "cached_results": len(self._cache),
            "store_path":    self._store,
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._parse_count = raw.get("parse_count", 0)
                logger.info("PLC parser cache loaded: %d entries", raw.get("count", 0))
        except Exception as exc:
            logger.warning("PLC cache load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "parse_count": self._parse_count,
                    "count":       len(self._cache),
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except Exception as exc:
            logger.error("PLC cache save failed: %s", exc)
