"""
KISWARM v4.1 — Module 18: Full IEC 61131-3 AST Parser
======================================================
Implements a complete recursive-descent parser for IEC 61131-3
Structured Text (ST) producing:
  - Abstract Syntax Tree (AST)
  - Control Flow Graph (CFG)
  - Data Dependency Graph (DDG)
  - Signal Dependency Graph (SDG)

EBNF grammar implemented:
  program     ::= PROGRAM id var_section? stmt_list END_PROGRAM
  var_section ::= VAR (var_decl)+ END_VAR
  var_decl    ::= id_list ':' type ('=' expr)? ';'
  stmt_list   ::= (statement)*
  statement   ::= assign_stmt | if_stmt | case_stmt | for_stmt |
                  while_stmt | repeat_stmt | fb_call | return_stmt
  assign_stmt ::= variable ':=' expr ';'
  if_stmt     ::= IF expr THEN stmt_list (ELSIF ... )* (ELSE ...)? END_IF ';'?
  fb_call     ::= id '(' param_list? ')' ';'?
  expr        ::= logical chain → comparison → arithmetic → factor
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any

# ─────────────────────────────────────────────────────────────────────────────
# TOKEN TYPES
# ─────────────────────────────────────────────────────────────────────────────

KEYWORDS = {
    "PROGRAM", "END_PROGRAM", "VAR", "VAR_INPUT", "VAR_OUTPUT",
    "VAR_IN_OUT", "END_VAR", "IF", "THEN", "ELSIF", "ELSE", "END_IF",
    "CASE", "OF", "END_CASE", "FOR", "TO", "BY", "DO", "END_FOR",
    "WHILE", "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT",
    "RETURN", "EXIT", "FUNCTION", "END_FUNCTION", "FUNCTION_BLOCK",
    "END_FUNCTION_BLOCK", "AND", "OR", "NOT", "XOR", "MOD",
    "TRUE", "FALSE",
}

TK_KEYWORD  = "KEYWORD"
TK_IDENT    = "IDENT"
TK_NUMBER   = "NUMBER"
TK_STRING   = "STRING"
TK_ASSIGN   = "ASSIGN"   # :=
TK_OP       = "OP"
TK_LPAREN   = "LPAREN"
TK_RPAREN   = "RPAREN"
TK_SEMI     = "SEMI"
TK_COLON    = "COLON"
TK_COMMA    = "COMMA"
TK_DOT      = "DOT"
TK_EOF      = "EOF"

@dataclass
class Token:
    kind:  str
    value: str
    line:  int

# ─────────────────────────────────────────────────────────────────────────────
# LEXER
# ─────────────────────────────────────────────────────────────────────────────

_TOKEN_PATTERNS = [
    ("COMMENT",  r'\(\*.*?\*\)|//[^\n]*'),
    ("ASSIGN",   r':='),
    ("OP",       r'<=|>=|<>|:=|[+\-*/=<>]'),
    ("NUMBER",   r'\d+\.?\d*(?:[eE][+\-]?\d+)?'),
    ("STRING",   r"'[^']*'"),
    ("LPAREN",   r'\('),
    ("RPAREN",   r'\)'),
    ("SEMI",     r';'),
    ("COLON",    r':'),
    ("COMMA",    r','),
    ("DOT",      r'\.'),
    ("IDENT",    r'[A-Za-z_][A-Za-z0-9_]*'),
    ("WS",       r'\s+'),
]
_MASTER_RE = re.compile(
    '|'.join(f'(?P<{name}>{pat})' for name, pat in _TOKEN_PATTERNS),
    re.DOTALL | re.IGNORECASE,
)

def tokenize(source: str) -> List[Token]:
    tokens: List[Token] = []
    line = 1
    for m in _MASTER_RE.finditer(source):
        kind  = m.lastgroup
        value = m.group()
        if kind in ("WS", "COMMENT"):
            line += value.count('\n')
            continue
        if kind == "IDENT" and value.upper() in KEYWORDS:
            tokens.append(Token(TK_KEYWORD, value.upper(), line))
        elif kind == "IDENT":
            tokens.append(Token(TK_IDENT, value, line))
        elif kind == "ASSIGN":
            tokens.append(Token(TK_ASSIGN, ":=", line))
        else:
            tokens.append(Token(kind, value, line))
        line += value.count('\n')
    tokens.append(Token(TK_EOF, "", line))
    return tokens

# ─────────────────────────────────────────────────────────────────────────────
# AST NODE TYPES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ASTNode:
    node_type: str
    children:  List["ASTNode"] = field(default_factory=list)
    attrs:     Dict[str, Any]  = field(default_factory=dict)
    line:      int = 0

    def to_dict(self) -> dict:
        return {
            "node_type": self.node_type,
            "attrs":     self.attrs,
            "children":  [c.to_dict() for c in self.children],
            "line":      self.line,
        }

# Convenience constructors
def ProgramNode(name: str, vars: List, stmts: List, line: int = 0) -> ASTNode:
    n = ASTNode("Program", children=vars + stmts, attrs={"name": name}, line=line)
    return n

def VarDeclNode(name: str, vtype: str, init=None, line: int = 0) -> ASTNode:
    return ASTNode("VarDecl", attrs={"name": name, "type": vtype, "init": init}, line=line)

def AssignNode(target: str, expr: ASTNode, line: int = 0) -> ASTNode:
    return ASTNode("Assign", children=[expr], attrs={"target": target}, line=line)

def IfNode(cond: ASTNode, then: List, elsifs: List, else_: List, line: int = 0) -> ASTNode:
    return ASTNode("If", children=[cond] + then + else_,
                   attrs={"elsifs": elsifs}, line=line)

def FBCallNode(name: str, params: dict, line: int = 0) -> ASTNode:
    return ASTNode("FBCall", attrs={"name": name, "params": params}, line=line)

def ExprNode(op: str, left: Any = None, right: Any = None,
             value: Any = None, line: int = 0) -> ASTNode:
    children = []
    if left  is not None: children.append(left)
    if right is not None: children.append(right)
    return ASTNode("Expr", children=children, attrs={"op": op, "value": value}, line=line)

# ─────────────────────────────────────────────────────────────────────────────
# RECURSIVE-DESCENT PARSER
# ─────────────────────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens: List[Token]):
        self._toks = tokens
        self._pos  = 0

    # ── Lookahead ─────────────────────────────────────────────────────────────

    def _peek(self) -> Token:
        return self._toks[min(self._pos, len(self._toks) - 1)]

    def _consume(self) -> Token:
        tok = self._peek()
        self._pos += 1
        return tok

    def _expect(self, kind: str, value: str = None) -> Token:
        tok = self._consume()
        if tok.kind != kind:
            raise SyntaxError(f"L{tok.line}: expected {kind!r}, got {tok.kind!r}({tok.value!r})")
        if value and tok.value.upper() != value.upper():
            raise SyntaxError(f"L{tok.line}: expected {value!r}, got {tok.value!r}")
        return tok

    def _match(self, kind: str, value: str = None) -> bool:
        tok = self._peek()
        if tok.kind != kind:
            return False
        if value and tok.value.upper() != value.upper():
            return False
        return True

    def _skip_if(self, kind: str, value: str = None) -> bool:
        if self._match(kind, value):
            self._consume()
            return True
        return False

    # ── Grammar ──────────────────────────────────────────────────────────────

    def parse_program(self) -> ASTNode:
        self._expect(TK_KEYWORD, "PROGRAM")
        name = self._consume().value
        vars_  = self._parse_var_section() if self._match(TK_KEYWORD, "VAR") else []
        stmts  = self._parse_stmt_list()
        self._skip_if(TK_KEYWORD, "END_PROGRAM")
        return ProgramNode(name, vars_, stmts)

    def _parse_var_section(self) -> List[ASTNode]:
        nodes: List[ASTNode] = []
        while self._match(TK_KEYWORD, "VAR") or \
              self._match(TK_KEYWORD, "VAR_INPUT") or \
              self._match(TK_KEYWORD, "VAR_OUTPUT") or \
              self._match(TK_KEYWORD, "VAR_IN_OUT"):
            self._consume()
            while not (self._match(TK_KEYWORD, "END_VAR") or self._match(TK_EOF, "")):
                names = self._parse_id_list()
                self._expect(TK_COLON)
                vtype = self._consume().value
                init  = None
                if self._skip_if(TK_ASSIGN):
                    init = self._consume().value
                self._skip_if(TK_SEMI)
                for n in names:
                    nodes.append(VarDeclNode(n, vtype, init))
            self._skip_if(TK_KEYWORD, "END_VAR")
        return nodes

    def _parse_id_list(self) -> List[str]:
        names = [self._consume().value]
        while self._skip_if(TK_COMMA):
            names.append(self._consume().value)
        return names

    def _parse_stmt_list(self) -> List[ASTNode]:
        stmts = []
        stop_kw = {"END_IF", "END_PROGRAM", "END_FOR", "END_WHILE", "END_REPEAT",
                   "END_CASE", "END_FUNCTION", "END_FUNCTION_BLOCK",
                   "ELSE", "ELSIF", "UNTIL", "OF"}
        while not (self._match(TK_EOF, "") or
                   any(self._match(TK_KEYWORD, k) for k in stop_kw)):
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
        return stmts

    def _parse_statement(self) -> Optional[ASTNode]:
        tok = self._peek()
        if tok.kind == TK_EOF:
            return None
        if tok.kind == TK_KEYWORD:
            kw = tok.value.upper()
            if kw == "IF":      return self._parse_if()
            if kw == "FOR":     return self._parse_for()
            if kw == "WHILE":   return self._parse_while()
            if kw == "REPEAT":  return self._parse_repeat()
            if kw == "CASE":    return self._parse_case()
            if kw == "RETURN":
                self._consume()
                self._skip_if(TK_SEMI)
                return ASTNode("Return", line=tok.line)
            if kw == "EXIT":
                self._consume()
                self._skip_if(TK_SEMI)
                return ASTNode("Exit", line=tok.line)
            if kw in {"END_IF","END_FOR","END_WHILE","END_REPEAT",
                      "END_CASE","ELSE","ELSIF","UNTIL","OF"}:
                return None
        if tok.kind == TK_IDENT:
            # Lookahead: assign or FB call?
            save = self._pos
            self._consume()  # id
            next_tok = self._peek()
            self._pos = save
            if next_tok.kind == TK_ASSIGN:
                return self._parse_assign()
            else:
                return self._parse_fb_call()
        # Unknown token — skip it
        self._consume()
        return None

    def _parse_assign(self) -> ASTNode:
        line   = self._peek().line
        target = self._consume().value
        self._expect(TK_ASSIGN)
        expr = self._parse_expr()
        self._skip_if(TK_SEMI)
        return AssignNode(target, expr, line)

    def _parse_fb_call(self) -> ASTNode:
        line = self._peek().line
        name = self._consume().value
        params: dict = {}
        if self._skip_if(TK_LPAREN):
            while not self._match(TK_RPAREN) and not self._match(TK_EOF, ""):
                if self._peek().kind == TK_IDENT and self._toks[self._pos+1].kind == TK_ASSIGN:
                    pname = self._consume().value
                    self._consume()  # :=
                    pval  = self._consume().value
                    params[pname] = pval
                else:
                    params[f"arg{len(params)}"] = self._consume().value
                self._skip_if(TK_COMMA)
            self._skip_if(TK_RPAREN)
        self._skip_if(TK_SEMI)
        return FBCallNode(name, params, line)

    def _parse_if(self) -> ASTNode:
        line = self._peek().line
        self._expect(TK_KEYWORD, "IF")
        cond  = self._parse_expr()
        self._skip_if(TK_KEYWORD, "THEN")
        then  = self._parse_stmt_list()
        elsifs: List[Tuple] = []
        else_  = []
        while self._match(TK_KEYWORD, "ELSIF"):
            self._consume()
            ec = self._parse_expr()
            self._skip_if(TK_KEYWORD, "THEN")
            eb = self._parse_stmt_list()
            elsifs.append((ec, eb))
        if self._skip_if(TK_KEYWORD, "ELSE"):
            else_ = self._parse_stmt_list()
        self._skip_if(TK_KEYWORD, "END_IF")
        self._skip_if(TK_SEMI)
        return IfNode(cond, then, elsifs, else_, line)

    def _parse_for(self) -> ASTNode:
        line = self._peek().line
        self._consume()  # FOR
        var = self._consume().value
        self._skip_if(TK_ASSIGN)
        start = self._parse_expr()
        self._skip_if(TK_KEYWORD, "TO")
        end = self._parse_expr()
        by  = None
        if self._skip_if(TK_KEYWORD, "BY"):
            by = self._parse_expr()
        self._skip_if(TK_KEYWORD, "DO")
        body = self._parse_stmt_list()
        self._skip_if(TK_KEYWORD, "END_FOR")
        return ASTNode("For", children=[start, end] + body,
                       attrs={"var": var, "by": by}, line=line)

    def _parse_while(self) -> ASTNode:
        line = self._peek().line
        self._consume()
        cond = self._parse_expr()
        self._skip_if(TK_KEYWORD, "DO")
        body = self._parse_stmt_list()
        self._skip_if(TK_KEYWORD, "END_WHILE")
        return ASTNode("While", children=[cond] + body, line=line)

    def _parse_repeat(self) -> ASTNode:
        line = self._peek().line
        self._consume()
        body = self._parse_stmt_list()
        self._skip_if(TK_KEYWORD, "UNTIL")
        cond = self._parse_expr()
        self._skip_if(TK_KEYWORD, "END_REPEAT")
        return ASTNode("Repeat", children=[cond] + body, line=line)

    def _parse_case(self) -> ASTNode:
        line = self._peek().line
        self._consume()
        expr = self._parse_expr()
        self._skip_if(TK_KEYWORD, "OF")
        branches: List[ASTNode] = []
        while not (self._match(TK_KEYWORD, "END_CASE") or self._match(TK_EOF, "")):
            if self._match(TK_KEYWORD, "ELSE"):
                self._consume()
                body = self._parse_stmt_list()
                branches.append(ASTNode("CaseElse", children=body))
            elif self._peek().kind in (TK_NUMBER, TK_IDENT):
                val = self._consume().value
                self._skip_if(TK_COLON)
                body = self._parse_stmt_list()
                branches.append(ASTNode("CaseBranch", children=body, attrs={"value": val}))
            else:
                self._consume()
        self._skip_if(TK_KEYWORD, "END_CASE")
        return ASTNode("Case", children=[expr] + branches, line=line)

    # ── Expression parser (precedence climbing) ───────────────────────────────

    def _parse_expr(self) -> ASTNode:
        return self._parse_logical_or()

    def _parse_logical_or(self) -> ASTNode:
        left = self._parse_logical_and()
        while self._match(TK_KEYWORD, "OR") or self._match(TK_KEYWORD, "XOR"):
            op = self._consume().value.upper()
            right = self._parse_logical_and()
            left = ExprNode(op, left, right)
        return left

    def _parse_logical_and(self) -> ASTNode:
        left = self._parse_comparison()
        while self._match(TK_KEYWORD, "AND"):
            self._consume()
            right = self._parse_comparison()
            left = ExprNode("AND", left, right)
        return left

    def _parse_comparison(self) -> ASTNode:
        left = self._parse_additive()
        while self._peek().kind == TK_OP and \
              self._peek().value in ("<", ">", "<=", ">=", "=", "<>"):
            op = self._consume().value
            right = self._parse_additive()
            left = ExprNode(op, left, right)
        return left

    def _parse_additive(self) -> ASTNode:
        left = self._parse_term()
        while self._peek().kind == TK_OP and self._peek().value in ("+", "-"):
            op = self._consume().value
            right = self._parse_term()
            left = ExprNode(op, left, right)
        return left

    def _parse_term(self) -> ASTNode:
        left = self._parse_factor()
        while self._peek().kind == TK_OP and self._peek().value in ("*", "/") or \
              self._match(TK_KEYWORD, "MOD"):
            op = self._consume().value
            right = self._parse_factor()
            left = ExprNode(op, left, right)
        return left

    def _parse_factor(self) -> ASTNode:
        tok = self._peek()
        if tok.kind == TK_KEYWORD and tok.value.upper() == "NOT":
            self._consume()
            operand = self._parse_factor()
            return ExprNode("NOT", left=operand, line=tok.line)
        if tok.kind == TK_KEYWORD and tok.value.upper() in ("TRUE", "FALSE"):
            self._consume()
            return ExprNode("LITERAL", value=tok.value.upper() == "TRUE", line=tok.line)
        if tok.kind == TK_NUMBER:
            self._consume()
            return ExprNode("LITERAL", value=float(tok.value), line=tok.line)
        if tok.kind == TK_STRING:
            self._consume()
            return ExprNode("LITERAL", value=tok.value, line=tok.line)
        if tok.kind == TK_LPAREN:
            self._consume()
            expr = self._parse_expr()
            self._skip_if(TK_RPAREN)
            return expr
        if tok.kind == TK_IDENT:
            # Could be variable or function call
            save = self._pos
            self._consume()
            if self._match(TK_LPAREN):
                self._pos = save
                return self._parse_fb_call_expr()
            return ExprNode("VAR", value=tok.value, line=tok.line)
        if tok.kind == TK_OP and tok.value == "-":
            self._consume()
            operand = self._parse_factor()
            return ExprNode("NEG", left=operand, line=tok.line)
        # Skip unknown
        self._consume()
        return ExprNode("UNKNOWN", value=tok.value, line=tok.line)

    def _parse_fb_call_expr(self) -> ASTNode:
        name = self._consume().value
        params: dict = {}
        if self._skip_if(TK_LPAREN):
            while not self._match(TK_RPAREN) and not self._match(TK_EOF, ""):
                params[f"arg{len(params)}"] = self._consume().value
                self._skip_if(TK_COMMA)
            self._skip_if(TK_RPAREN)
        return ASTNode("FuncCall", attrs={"name": name, "params": params})


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CFGNode:
    """Control Flow Graph node."""
    node_id:    str
    kind:       str        # "entry", "exit", "basic_block", "branch", "loop"
    stmts:      List[str]  = field(default_factory=list)
    successors: List[str]  = field(default_factory=list)

@dataclass
class DDGEdge:
    """Data Dependency Graph edge: source defines variable used by target."""
    source: str   # AST node id
    target: str
    var:    str

@dataclass
class SDGEdge:
    """Signal Dependency Graph: signal flow between blocks."""
    from_block: str
    to_block:   str
    signal:     str
    direction:  str = "forward"   # "forward" | "feedback"


class GraphBuilder:
    """Converts AST into CFG, DDG, and SDG."""

    def __init__(self):
        self._counter = 0

    def _fresh_id(self, prefix: str = "N") -> str:
        self._counter += 1
        return f"{prefix}{self._counter:04d}"

    # ── CFG ──────────────────────────────────────────────────────────────────

    def build_cfg(self, program: ASTNode) -> Dict[str, CFGNode]:
        cfg: Dict[str, CFGNode] = {}
        entry = self._fresh_id("CFG")
        cfg[entry] = CFGNode(entry, "entry")
        prev = [entry]
        for child in program.children:
            if child.node_type in ("VarDecl",):
                continue
            new_nodes, exits = self._cfg_stmt(child, cfg)
            for p in prev:
                if p in cfg:
                    cfg[p].successors.extend(new_nodes)
            prev = exits
        ex = self._fresh_id("CFG")
        cfg[ex] = CFGNode(ex, "exit")
        for p in prev:
            if p in cfg:
                cfg[p].successors.append(ex)
        return cfg

    def _cfg_stmt(self, node: ASTNode, cfg: dict) -> Tuple[List[str], List[str]]:
        nid = self._fresh_id("CFG")
        if node.node_type == "Assign":
            cfg[nid] = CFGNode(nid, "basic_block",
                               stmts=[f"{node.attrs.get('target')} := ..."])
            return [nid], [nid]
        if node.node_type == "FBCall":
            cfg[nid] = CFGNode(nid, "basic_block",
                               stmts=[f"CALL {node.attrs.get('name')}"])
            return [nid], [nid]
        if node.node_type == "If":
            cfg[nid] = CFGNode(nid, "branch", stmts=["IF condition"])
            then_first, then_exits = self._cfg_stmtlist(node.children[1:], cfg)
            else_first, else_exits = [], [nid]
            if node.attrs.get("elsifs") or any(c.node_type not in ("Assign","FBCall")
                                                for c in node.children):
                else_first = [nid]
            cfg[nid].successors = then_first + else_first
            return [nid], then_exits + else_exits
        if node.node_type in ("For", "While", "Repeat"):
            cfg[nid] = CFGNode(nid, "loop", stmts=[f"{node.node_type} loop"])
            body_first, body_exits = self._cfg_stmtlist(node.children, cfg)
            cfg[nid].successors = body_first + [nid]
            return [nid], body_exits + [nid]
        cfg[nid] = CFGNode(nid, "basic_block", stmts=[node.node_type])
        return [nid], [nid]

    def _cfg_stmtlist(self, nodes: List[ASTNode], cfg: dict) -> Tuple[List[str], List[str]]:
        if not nodes:
            return [], []
        first_ids, _ = self._cfg_stmt(nodes[0], cfg)
        prev = first_ids
        for node in nodes[1:]:
            new, exits = self._cfg_stmt(node, cfg)
            for p in prev:
                if p in cfg:
                    cfg[p].successors.extend(new)
            prev = exits
        return first_ids, prev

    # ── DDG ──────────────────────────────────────────────────────────────────

    def build_ddg(self, program: ASTNode) -> List[DDGEdge]:
        """Build Data Dependency Graph: def-use chains."""
        defs: Dict[str, str] = {}  # var → node_id that defines it
        edges: List[DDGEdge] = []

        def visit(node: ASTNode, node_id: str) -> None:
            if node.node_type == "Assign":
                target = node.attrs.get("target", "")
                # Uses in RHS
                for used in self._collect_vars(node.children[0] if node.children else None):
                    if used in defs:
                        edges.append(DDGEdge(defs[used], node_id, used))
                # This node defines target
                defs[target] = node_id
            for child in node.children:
                visit(child, self._fresh_id("D"))

        visit(program, self._fresh_id("D"))
        return edges

    def _collect_vars(self, node: Optional[ASTNode]) -> Set[str]:
        if node is None:
            return set()
        result = set()
        if node.node_type in ("Expr",) and node.attrs.get("op") == "VAR":
            v = node.attrs.get("value")
            if v:
                result.add(v)
        for child in node.children:
            result |= self._collect_vars(child)
        return result

    # ── SDG ──────────────────────────────────────────────────────────────────

    def build_sdg(self, program: ASTNode) -> List[SDGEdge]:
        """Build Signal Dependency Graph from FB calls and PID patterns."""
        edges: List[SDGEdge] = []
        blocks: Dict[str, Dict] = {}

        for node in self._walk(program):
            if node.node_type == "FBCall":
                name   = node.attrs.get("name", "")
                params = node.attrs.get("params", {})
                blocks[name] = params
                # Signal edges: outputs of one block feed inputs of another
                for pname, pval in params.items():
                    if pval and pval in blocks:
                        edges.append(SDGEdge(pval, name, pname, "forward"))

        # Detect feedback: assign output of block back to its own input
        assigned: Dict[str, str] = {}
        for node in self._walk(program):
            if node.node_type == "Assign":
                assigned[node.attrs.get("target", "")] = node.attrs.get("target", "")

        for name, params in blocks.items():
            for pname, pval in params.items():
                if pval in assigned:
                    edges.append(SDGEdge(name, name, pval, "feedback"))
                    break

        return edges

    def _walk(self, node: ASTNode):
        yield node
        for child in node.children:
            yield from self._walk(child)


# ─────────────────────────────────────────────────────────────────────────────
# PATTERN DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

SAFETY_KEYWORDS = {"ESTOP", "FAULT", "ALARM", "SAFETY", "EMERGENCY", "TRIP",
                   "SHUTDOWN", "STO", "SIL", "INTERLOCK"}

@dataclass
class DetectedPID:
    name:        str
    kp:          Optional[float]
    ki:          Optional[float]
    kd:          Optional[float]
    setpoint:    Optional[str]
    process_var: Optional[str]
    output:      Optional[str]

@dataclass
class DetectedInterlock:
    name:      str
    condition: str
    action:    str
    safety:    bool

@dataclass
class DetectedDeadCode:
    description: str
    line:        int


class PatternDetector:
    """Detect PID blocks, state machines, interlocks, dead branches."""

    def detect_pid(self, program: ASTNode) -> List[DetectedPID]:
        pids = []
        for node in self._walk(program):
            if node.node_type == "FBCall":
                name = node.attrs.get("name", "").upper()
                if "PID" in name or name in ("FB_PID", "PIDC", "PID_CTRL"):
                    params = node.attrs.get("params", {})
                    pids.append(DetectedPID(
                        name        = node.attrs.get("name", ""),
                        kp          = self._safe_float(params.get("KP") or
                                                        params.get("Kp") or
                                                        params.get("GAIN")),
                        ki          = self._safe_float(params.get("KI") or params.get("Ki")),
                        kd          = self._safe_float(params.get("KD") or params.get("Kd")),
                        setpoint    = params.get("SP") or params.get("SETPOINT"),
                        process_var = params.get("PV") or params.get("PROCESS_VAR"),
                        output      = params.get("OUT") or params.get("OUTPUT"),
                    ))
            # Detect PID pattern via assignment chain: error = SP - PV; out = PID(error)
            if node.node_type == "Assign":
                target = node.attrs.get("target", "").lower()
                if "error" in target or "err" in target:
                    if self._is_subtraction(node):
                        pids.append(DetectedPID(
                            name="DetectedPIDChain", kp=None, ki=None, kd=None,
                            setpoint=None, process_var=None, output=target,
                        ))
        return pids

    def detect_interlocks(self, program: ASTNode) -> List[DetectedInterlock]:
        interlocks = []
        for node in self._walk(program):
            if node.node_type == "If":
                cond_text = self._expr_to_str(node.children[0] if node.children else None)
                is_safety = any(kw in cond_text.upper() for kw in SAFETY_KEYWORDS)
                if is_safety or self._has_stop_action(node.children[1:]):
                    action_text = " ".join(
                        n.attrs.get("target", n.attrs.get("name", ""))
                        for n in node.children[1:] if n.node_type in ("Assign", "FBCall")
                    )
                    interlocks.append(DetectedInterlock(
                        name      = "SafetyInterlock",
                        condition = cond_text,
                        action    = action_text or "STOP",
                        safety    = True,
                    ))
        return interlocks

    def detect_dead_code(self, program: ASTNode) -> List[DetectedDeadCode]:
        dead = []
        for node in self._walk(program):
            if node.node_type == "If":
                cond = node.children[0] if node.children else None
                if cond and cond.attrs.get("op") == "LITERAL":
                    val = cond.attrs.get("value")
                    if val is False:
                        dead.append(DetectedDeadCode("Unreachable branch (IF FALSE)", node.line))
                    elif val is True:
                        dead.append(DetectedDeadCode("Unconditional branch (IF TRUE)", node.line))
        return dead

    def _walk(self, node: ASTNode):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _expr_to_str(self, node: Optional[ASTNode]) -> str:
        if node is None:
            return ""
        op  = node.attrs.get("op", "")
        val = node.attrs.get("value", "")
        if op == "VAR":  return str(val)
        if op == "LITERAL": return str(val)
        if node.children:
            return " ".join(
                [self._expr_to_str(c) for c in node.children] + ([op] if op else [])
            )
        return op or str(val)

    def _is_subtraction(self, node: ASTNode) -> bool:
        return any(
            c.attrs.get("op") == "-" for c in self._walk(node)
        )

    def _has_stop_action(self, stmts: List[ASTNode]) -> bool:
        stop_kw = {"STOP", "HALT", "DISABLE", "OFF", "FALSE", "RESET"}
        for n in stmts:
            if n.node_type == "Assign":
                val = str(n.attrs.get("target", "")).upper()
                if any(kw in val for kw in stop_kw):
                    return True
        return False

    @staticmethod
    def _safe_float(v) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# PARSE RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ASTParseResult:
    program_name: str
    ast:          Optional[ASTNode]
    cfg:          Dict[str, CFGNode]
    ddg_edges:    List[DDGEdge]
    sdg_edges:    List[SDGEdge]
    pid_blocks:   List[DetectedPID]
    interlocks:   List[DetectedInterlock]
    dead_code:    List[DetectedDeadCode]
    var_count:    int
    stmt_count:   int
    source_hash:  str
    errors:       List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "program_name": self.program_name,
            "source_hash":  self.source_hash,
            "var_count":    self.var_count,
            "stmt_count":   self.stmt_count,
            "cfg_nodes":    len(self.cfg),
            "ddg_edges":    len(self.ddg_edges),
            "sdg_edges":    len(self.sdg_edges),
            "pid_blocks":   [{"name": p.name, "kp": p.kp, "ki": p.ki, "kd": p.kd}
                              for p in self.pid_blocks],
            "interlocks":   [{"name": i.name, "condition": i.condition, "safety": i.safety}
                              for i in self.interlocks],
            "dead_code":    [{"description": d.description, "line": d.line}
                              for d in self.dead_code],
            "errors":       self.errors,
        }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

class IEC61131ASTParser:
    """Full IEC 61131-3 ST parser: tokenize → AST → CFG/DDG/SDG → patterns."""

    def __init__(self):
        self._graph   = GraphBuilder()
        self._pattern = PatternDetector()
        self._cache: Dict[str, ASTParseResult] = {}
        self._parse_count = 0

    def parse(self, source: str, program_name: str = "UNKNOWN") -> ASTParseResult:
        src_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
        if src_hash in self._cache:
            return self._cache[src_hash]

        self._parse_count += 1
        errors: List[str] = []
        ast_root: Optional[ASTNode] = None

        try:
            tokens   = tokenize(source)
            parser   = _Parser(tokens)
            ast_root = parser.parse_program()
            if ast_root.attrs.get("name"):
                program_name = ast_root.attrs["name"]
        except Exception as e:
            errors.append(f"Parse error: {e}")

        cfg       = self._graph.build_cfg(ast_root)  if ast_root else {}
        ddg_edges = self._graph.build_ddg(ast_root)  if ast_root else []
        sdg_edges = self._graph.build_sdg(ast_root)  if ast_root else []

        pid_blocks  = self._pattern.detect_pid(ast_root)        if ast_root else []
        interlocks  = self._pattern.detect_interlocks(ast_root) if ast_root else []
        dead_code   = self._pattern.detect_dead_code(ast_root)  if ast_root else []

        var_count  = sum(1 for n in self._walk(ast_root) if n.node_type == "VarDecl") \
                     if ast_root else 0
        stmt_count = sum(1 for n in self._walk(ast_root)
                         if n.node_type in ("Assign","FBCall","If","For","While","Repeat","Case")) \
                     if ast_root else 0

        result = ASTParseResult(
            program_name = program_name,
            ast          = ast_root,
            cfg          = cfg,
            ddg_edges    = ddg_edges,
            sdg_edges    = sdg_edges,
            pid_blocks   = pid_blocks,
            interlocks   = interlocks,
            dead_code    = dead_code,
            var_count    = var_count,
            stmt_count   = stmt_count,
            source_hash  = src_hash,
            errors       = errors,
        )
        self._cache[src_hash] = result
        return result

    def get_stats(self) -> dict:
        return {
            "parse_count": self._parse_count,
            "cache_size":  len(self._cache),
        }

    def _walk(self, node: Optional[ASTNode]):
        if node is None:
            return
        yield node
        for child in node.children:
            yield from self._walk(child)
