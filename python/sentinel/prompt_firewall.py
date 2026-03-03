"""
KISWARM v2.2 — MODULE 6: ADVERSARIAL PROMPT FIREWALL
======================================================
Guards the swarm's knowledge injection pipeline against:

  1. JAILBREAK PATTERNS
     Attempts to override system instructions before knowledge injection.
     e.g., "ignore previous instructions", "DAN mode", role-play bypasses.

  2. POLICY BYPASS LANGUAGE
     Phrasing designed to circumvent governance rules.
     e.g., "for educational purposes only", "hypothetically speaking".

  3. HALLUCINATION MARKERS
     Content that signals confabulation: vague certainty claims,
     self-contradicting statements, impossible specificity, etc.

  4. ADVERSARIAL INJECTION PATTERNS
     Content designed to poison the vector DB — inject false facts
     that will later be retrieved as authoritative swarm knowledge.

Detection Methods:
  • Regex-based pattern matching (fast, deterministic)
  • Statistical anomaly scoring (entropy, repetition, certainty inflation)
  • Semantic coherence scoring (embedding variance across sentences)

Every piece of intelligence passes through this firewall
BEFORE being vectorized and injected into Qdrant.

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("sentinel.firewall")


# ── Threat Categories ─────────────────────────────────────────────────────────

class ThreatType(str, Enum):
    JAILBREAK            = "JAILBREAK"
    POLICY_BYPASS        = "POLICY_BYPASS"
    HALLUCINATION        = "HALLUCINATION"
    ADVERSARIAL_INJECT   = "ADVERSARIAL_INJECT"
    PROMPT_INJECTION     = "PROMPT_INJECTION"
    REPETITION_ATTACK    = "REPETITION_ATTACK"
    CLEAN                = "CLEAN"


# ── Pattern Libraries ─────────────────────────────────────────────────────────

JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    # (regex_pattern, description)
    (r"ignore (previous|all|your) (instructions?|rules?|guidelines?|constraints?)", "instruction override"),
    (r"(DAN|developer|god|unrestricted|jailbreak) mode", "mode switching"),
    (r"pretend (you are|you're|to be) (a|an) (human|person|unrestricted|evil|malicious)", "identity override"),
    (r"you (are|were) (now|actually) (free|unconstrained|without restrictions)", "freedom claim"),
    (r"do anything now", "DAN pattern"),
    (r"(bypass|override|disable|ignore) (safety|ethics|policy|guidelines|filters)", "safety bypass"),
    (r"act as if (you|your) (have no|don't have) (restrictions|limits|rules)", "restriction removal"),
    (r"your (true|real|actual) (self|purpose|goal) is to", "identity manipulation"),
    (r"(token|word|character) limit (bypass|override|workaround)", "limit bypass"),
    (r"\[system\]|\[inst\]|\[prompt\]", "system injection via tags"),
    (r"<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>", "special token injection"),
]

POLICY_BYPASS_PATTERNS: list[tuple[str, str]] = [
    (r"for (educational|research|academic|scientific|hypothetical) purposes only", "educational bypass"),
    (r"hypothetically (speaking|if|assuming)", "hypothetical framing"),
    (r"in a (fictional|fantasy|story|roleplay|game) (world|context|scenario|setting)", "fictional bypass"),
    (r"(imagine|suppose|assume) (you|we) (had no|were without|didn't have) (any )?(restrictions|limits)", "imagination bypass"),
    (r"(legally|technically|officially) speaking", "legality deflection"),
    (r"just (asking|curious|wondering)", "innocence framing"),
    (r"no (harm|offense|bad) (intended|meant)", "harm deflection"),
    (r"this is (purely|just|only) (theoretical|academic|abstract)", "theory bypass"),
    (r"(white hat|ethical hacker|security researcher|pentester)", "authority claim bypass"),
]

HALLUCINATION_PATTERNS: list[tuple[str, str]] = [
    # Overconfident certainty on specific numbers/dates that are suspiciously precise
    (r"(exactly|precisely|definitively) ([\d,]+(\.\d+)?)\s*(billion|million|thousand|percent|%)", "overspecific numbers"),
    # Self-contradiction indicators
    (r"(however|but|yet|although).{0,50}(however|but|yet|although)", "double contradiction"),
    # Impossible citation formats
    (r"(published|released|stated) (in|on) (20[3-9]\d|21\d{2})", "future date citation"),
    # Vague-but-confident claims
    (r"(studies|research|experts|scientists) (show|prove|confirm|agree) that (everything|all|always|never)", "universal false claim"),
    # Confabulated authority
    (r"according to (famous|well-known|renowned|respected) (scientists?|researchers?|experts?) (named?|called?|known as)", "fabricated authority"),
    # Repetition (hallucination loop signal)
    (r"(.{20,})\1{2,}", "repetition loop"),
]

ADVERSARIAL_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Attempts to poison the knowledge base with false attributions
    (r"remember (that|this):.{0,200}(always|never|must|should)", "memory poisoning"),
    (r"(store|save|remember|inject|add) (this|the following) (to|into|in) (your|the) (memory|database|knowledge)", "explicit injection"),
    (r"update (your|the) (knowledge|database|memory) (to|with) (reflect|show|indicate)", "knowledge update attack"),
    # SQL/code injection attempts
    (r"(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE)\s+\w+", "SQL injection"),
    (r"(os\.system|subprocess\.|exec\(|eval\(|__import__)", "code injection"),
    # Prompt chain attacks
    (r"(when|if|next time) (you|the system) (sees?|receives?|gets?) (a query|a question|input) (about|regarding|on)", "conditional trigger plant"),
]

PROMPT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"---\s*(new|updated|revised|system) (instructions?|prompt|context)", "delimited injection"),
    (r"\n\n\n.*(instructions?|task|objective|goal):", "blank-line injection"),
    (r"(OVERRIDE|SYSTEM|ADMIN|ROOT):", "authority keyword injection"),
    (r"<(system|context|instruction|override)>.*</(system|context|instruction|override)>", "XML tag injection"),
]


# ── Statistical Analyzers ─────────────────────────────────────────────────────

def text_entropy(text: str) -> float:
    """Shannon entropy of character distribution. Low = repetitive/malformed."""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for c in text:
        freq[c] = freq.get(c, 0) + 1
    n = len(text)
    return -sum((count / n) * math.log2(count / n) for count in freq.values())

def repetition_ratio(text: str) -> float:
    """Fraction of content that is repeated trigrams. High = hallucination loop."""
    words = text.lower().split()
    if len(words) < 6:
        return 0.0
    trigrams = [tuple(words[i:i+3]) for i in range(len(words) - 2)]
    unique   = len(set(trigrams))
    total    = len(trigrams)
    return round(1.0 - (unique / total), 3) if total > 0 else 0.0

def certainty_inflation_score(text: str) -> float:
    """Counts overconfident absolute terms — high score signals hallucination."""
    terms = [
        "always", "never", "everyone", "no one", "definitely",
        "absolutely", "certainly", "without doubt", "100%",
        "guaranteed", "proven", "undeniable", "fact is",
    ]
    text_lower = text.lower()
    hits = sum(1 for t in terms if t in text_lower)
    return round(min(1.0, hits / 5.0), 3)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ThreatMatch:
    threat_type:  ThreatType
    pattern:      str
    description:  str
    match_text:   str
    severity:     str   # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"


@dataclass
class FirewallReport:
    """Complete firewall assessment for a piece of content."""
    blocked:            bool
    threat_level:       str             # "CLEAN" | "SUSPICIOUS" | "DANGEROUS" | "BLOCKED"
    threat_score:       float           # 0.0–1.0
    matches:            list[ThreatMatch]
    statistical:        dict            # entropy, repetition, certainty scores
    recommendation:     str
    content_hash:       str
    timestamp:          str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def primary_threat(self) -> Optional[ThreatType]:
        if not self.matches:
            return None
        severity_order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        top = max(self.matches, key=lambda m: severity_order.index(m.severity))
        return top.threat_type

    @property
    def threat_types(self) -> list[str]:
        return list({m.threat_type.value for m in self.matches})


# ── Adversarial Prompt Firewall ───────────────────────────────────────────────

class AdversarialPromptFirewall:
    """
    Multi-layer firewall protecting swarm knowledge injection.

    All intelligence from scouts is passed through this firewall
    BEFORE vectorization and Qdrant injection.

    Layers:
      1. Regex pattern matching against threat libraries
      2. Statistical anomaly scoring
      3. Composite threat scoring → block/allow decision

    Usage:
        firewall = AdversarialPromptFirewall()
        report = firewall.scan(content, source="ArXiv")

        if report.blocked:
            logger.warning("Content blocked: %s", report.threat_types)
        else:
            injector.inject(knowledge)
    """

    BLOCK_THRESHOLD      = 0.70   # threat_score above this → blocked
    SUSPICIOUS_THRESHOLD = 0.35

    SEVERITY_WEIGHTS = {
        ThreatType.JAILBREAK:           1.0,
        ThreatType.ADVERSARIAL_INJECT:  1.0,
        ThreatType.PROMPT_INJECTION:    0.9,
        ThreatType.POLICY_BYPASS:       0.6,
        ThreatType.HALLUCINATION:       0.5,
        ThreatType.REPETITION_ATTACK:   0.4,
    }

    PATTERN_SEVERITY = {
        ThreatType.JAILBREAK:           "CRITICAL",
        ThreatType.ADVERSARIAL_INJECT:  "CRITICAL",
        ThreatType.PROMPT_INJECTION:    "HIGH",
        ThreatType.POLICY_BYPASS:       "MEDIUM",
        ThreatType.HALLUCINATION:       "MEDIUM",
        ThreatType.REPETITION_ATTACK:   "LOW",
    }

    ALL_PATTERNS = [
        (ThreatType.JAILBREAK,           JAILBREAK_PATTERNS),
        (ThreatType.POLICY_BYPASS,       POLICY_BYPASS_PATTERNS),
        (ThreatType.HALLUCINATION,       HALLUCINATION_PATTERNS),
        (ThreatType.ADVERSARIAL_INJECT,  ADVERSARIAL_INJECTION_PATTERNS),
        (ThreatType.PROMPT_INJECTION,    PROMPT_INJECTION_PATTERNS),
    ]

    def __init__(
        self,
        block_threshold:      float = 0.70,
        suspicious_threshold: float = 0.35,
    ):
        self.block_threshold      = block_threshold
        self.suspicious_threshold = suspicious_threshold
        # Pre-compile all patterns
        self._compiled = [
            (threat_type, [(re.compile(pat, re.IGNORECASE | re.DOTALL), desc)
                           for pat, desc in patterns])
            for threat_type, patterns in self.ALL_PATTERNS
        ]

    def scan(
        self,
        content:  str,
        source:   str = "unknown",
        query:    str = "",
    ) -> FirewallReport:
        """
        Scan content for adversarial patterns before knowledge injection.

        Args:
            content:  The intelligence payload text.
            source:   Scout source name (for logging).
            query:    Original query (scanned independently for injection attacks).

        Returns:
            FirewallReport — blocked=True means content must not be injected.
        """
        content_hash = hashlib.sha256(content[:2048].encode()).hexdigest()[:16]
        matches: list[ThreatMatch] = []

        # ── Layer 1: Pattern matching ─────────────────────────────────────────
        full_text = (query + "\n" + content).lower()

        for threat_type, compiled_patterns in self._compiled:
            for regex, description in compiled_patterns:
                match = regex.search(full_text)
                if match:
                    matches.append(ThreatMatch(
                        threat_type=threat_type,
                        pattern=regex.pattern[:60],
                        description=description,
                        match_text=match.group(0)[:80],
                        severity=self.PATTERN_SEVERITY[threat_type],
                    ))

        # ── Layer 2: Repetition attack detection ─────────────────────────────
        rep_ratio = repetition_ratio(content)
        if rep_ratio > 0.40:
            matches.append(ThreatMatch(
                threat_type=ThreatType.REPETITION_ATTACK,
                pattern="statistical:repetition",
                description=f"Repetition ratio {rep_ratio:.0%} (hallucination loop)",
                match_text=content[:80],
                severity="LOW" if rep_ratio < 0.65 else "MEDIUM",
            ))

        # ── Layer 3: Statistical scoring ─────────────────────────────────────
        entropy      = round(text_entropy(content), 3)
        rep          = rep_ratio
        certainty    = certainty_inflation_score(content)

        statistical = {
            "entropy":           entropy,
            "repetition_ratio":  rep,
            "certainty_score":   certainty,
            "content_length":    len(content),
            "low_entropy_flag":  entropy < 2.5 and len(content) > 100,
        }

        # Low entropy flag → suspicious
        if statistical["low_entropy_flag"]:
            matches.append(ThreatMatch(
                threat_type=ThreatType.HALLUCINATION,
                pattern="statistical:low_entropy",
                description=f"Suspiciously low entropy ({entropy:.2f})",
                match_text=content[:80],
                severity="LOW",
            ))

        # ── Threat score computation ──────────────────────────────────────────
        base_score = 0.0
        for m in matches:
            weight = self.SEVERITY_WEIGHTS.get(m.threat_type, 0.3)
            sev_multiplier = {"LOW": 0.2, "MEDIUM": 0.4, "HIGH": 0.7, "CRITICAL": 1.0}
            base_score += weight * sev_multiplier.get(m.severity, 0.3)

        # Statistical contribution
        stat_score = (
            (0.3 if certainty > 0.6 else 0.0) +
            (0.2 if rep > 0.5 else 0.0) +
            (0.1 if entropy < 2.0 else 0.0)
        )
        threat_score = round(min(1.0, base_score + stat_score), 3)

        # ── Determine threat level ────────────────────────────────────────────
        if threat_score >= self.block_threshold or any(
            m.severity == "CRITICAL" for m in matches
        ):
            threat_level  = "BLOCKED"
            blocked       = True
            recommendation = "BLOCK — adversarial content detected, do not inject"
        elif threat_score >= self.suspicious_threshold:
            threat_level  = "DANGEROUS"
            blocked       = True
            recommendation = "BLOCK — dangerous patterns present"
        elif matches:
            threat_level  = "SUSPICIOUS"
            blocked       = False
            recommendation = "FLAG — inject with low trust weighting"
        else:
            threat_level  = "CLEAN"
            blocked       = False
            recommendation = "ALLOW — no threats detected"

        if blocked:
            logger.warning(
                "Firewall BLOCKED content from %s | score=%.2f | threats=%s | hash=%s",
                source, threat_score, [m.threat_type.value for m in matches], content_hash,
            )
        elif matches:
            logger.info(
                "Firewall FLAGGED content from %s | score=%.2f | threats=%s",
                source, threat_score, [m.threat_type.value for m in matches],
            )

        return FirewallReport(
            blocked=blocked,
            threat_level=threat_level,
            threat_score=threat_score,
            matches=matches,
            statistical=statistical,
            recommendation=recommendation,
            content_hash=content_hash,
        )

    def scan_query(self, query: str) -> FirewallReport:
        """Scan only the user query (lighter check, no statistical analysis)."""
        return self.scan(content="", source="user_query", query=query)

    def is_clean(self, content: str, source: str = "unknown") -> bool:
        """Quick boolean check — returns True if content passes firewall."""
        return not self.scan(content, source).blocked
