"""
KISWARM v2.2 — MODULE 3: MODEL PERFORMANCE TRACKER
===================================================
Tracks per-model debate outcomes vs later validation results.
Builds reliability scores for each Ollama model in the swarm —
so the Swarm Debate Engine can weight votes by track record.

Metrics tracked per model:
  • Total debates participated in
  • Votes cast (A / B / SYNTHESIS)
  • Times voted with the winning side
  • Times validated correct (post-hoc)
  • Reliability score (ELO-adjacent, updated per outcome)
  • Calibration score (confidence vs accuracy)

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("sentinel.tracker")

KISWARM_HOME     = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR      = os.path.join(KISWARM_HOME, "KISWARM")
TRACKER_STORE    = os.path.join(KISWARM_DIR, "model_performance.json")

# ELO constants
ELO_K           = 32
ELO_DEFAULT     = 1200.0


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ModelRecord:
    """Per-model performance record."""
    model_name:         str
    elo_score:          float = ELO_DEFAULT
    debates:            int   = 0
    wins:               int   = 0        # voted with winning side
    losses:             int   = 0
    validated_correct:  int   = 0        # post-hoc external validation
    validated_wrong:    int   = 0
    votes_A:            int   = 0
    votes_B:            int   = 0
    votes_synthesis:    int   = 0
    last_active:        float = field(default_factory=time.time)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return round(self.wins / total, 3) if total > 0 else 0.5

    @property
    def validation_accuracy(self) -> float:
        total = self.validated_correct + self.validated_wrong
        return round(self.validated_correct / total, 3) if total > 0 else 0.5

    @property
    def reliability_score(self) -> float:
        """
        Composite reliability: blend ELO rank + validation accuracy.
        Normalised to 0.0–1.0.
        """
        elo_norm = max(0.0, min(1.0, (self.elo_score - 800) / 800))
        return round(0.6 * elo_norm + 0.4 * self.validation_accuracy, 3)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["win_rate"]            = self.win_rate
        d["validation_accuracy"] = self.validation_accuracy
        d["reliability_score"]   = self.reliability_score
        return d


@dataclass
class DebateEvent:
    """A single recorded debate event."""
    debate_id:      str
    query:          str
    participating:  list[str]   # model names
    votes:          dict        # {model: stance}
    winner_stance:  str
    timestamp:      float = field(default_factory=time.time)
    validated:      Optional[bool] = None   # set later after external validation
    validator:      Optional[str]  = None


@dataclass
class LeaderboardEntry:
    rank:              int
    model:             str
    elo:               float
    reliability:       float
    win_rate:          float
    debates:           int
    validated_correct: int


# ── Model Performance Tracker ─────────────────────────────────────────────────

class ModelPerformanceTracker:
    """
    Tracks reliability of each Ollama model across swarm debates.

    Example workflow:
        tracker = ModelPerformanceTracker()

        # After a debate:
        tracker.record_debate(
            debate_id="d001", query="Is X true?",
            votes={"llama3:8b": "A", "qwen2.5:7b": "B"},
            winner_stance="A"
        )

        # After external validation (optional but improves accuracy):
        tracker.validate_debate("d001", correct=True, validator="human")

        # Get weighted votes for future debate:
        weights = tracker.get_vote_weights(["llama3:8b", "qwen2.5:7b"])
    """

    def __init__(self, store_path: str = TRACKER_STORE):
        self._store_path = store_path
        self._models:  dict[str, ModelRecord]  = {}
        self._debates: dict[str, DebateEvent]  = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store_path):
            try:
                with open(self._store_path) as f:
                    raw = json.load(f)
                for name, data in raw.get("models", {}).items():
                    d = {k: v for k, v in data.items()
                         if k not in ("win_rate", "validation_accuracy", "reliability_score")}
                    self._models[name] = ModelRecord(**d)
                for did, data in raw.get("debates", {}).items():
                    self._debates[did] = DebateEvent(**data)
                logger.info("Tracker loaded: %d models, %d debates", len(self._models), len(self._debates))
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("Tracker load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
            payload = {
                "models":  {n: r.to_dict() for n, r in self._models.items()},
                "debates": {d: asdict(e) for d, e in self._debates.items()},
            }
            with open(self._store_path, "w") as f:
                json.dump(payload, f, indent=2)
        except OSError as exc:
            logger.error("Tracker save failed: %s", exc)

    def _ensure_model(self, name: str) -> ModelRecord:
        if name not in self._models:
            self._models[name] = ModelRecord(model_name=name)
        return self._models[name]

    # ── ELO update ────────────────────────────────────────────────────────────

    @staticmethod
    def _elo_expected(rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400))

    @staticmethod
    def _elo_new(rating: float, expected: float, actual: float) -> float:
        return rating + ELO_K * (actual - expected)

    # ── Core API ──────────────────────────────────────────────────────────────

    def record_debate(
        self,
        debate_id:     str,
        query:         str,
        votes:         dict[str, str],      # {model_name: "A"|"B"|"SYNTHESIS"}
        winner_stance: str,
    ) -> dict[str, float]:
        """
        Record a completed debate and update all participating model scores.

        Returns: {model_name: new_reliability_score, ...}
        """
        event = DebateEvent(
            debate_id=debate_id,
            query=query,
            participating=list(votes.keys()),
            votes=votes,
            winner_stance=winner_stance,
        )
        self._debates[debate_id] = event

        winners  = [m for m, v in votes.items() if v == winner_stance]
        losers   = [m for m, v in votes.items() if v != winner_stance]

        # ELO update: winners vs losers pairwise
        for w in winners:
            for l in losers:
                rec_w = self._ensure_model(w)
                rec_l = self._ensure_model(l)
                exp_w = self._elo_expected(rec_w.elo_score, rec_l.elo_score)
                exp_l = 1.0 - exp_w
                rec_w.elo_score = self._elo_new(rec_w.elo_score, exp_w, 1.0)
                rec_l.elo_score = self._elo_new(rec_l.elo_score, exp_l, 0.0)

        # Update counters
        for model, stance in votes.items():
            rec = self._ensure_model(model)
            rec.debates += 1
            rec.last_active = time.time()
            if stance == "A":
                rec.votes_A += 1
            elif stance == "B":
                rec.votes_B += 1
            else:
                rec.votes_synthesis += 1

            if stance == winner_stance:
                rec.wins += 1
            else:
                rec.losses += 1

        self._save()

        scores = {m: self._models[m].reliability_score for m in votes}
        logger.info(
            "Debate recorded: id=%s | winner=%s | participants=%d",
            debate_id, winner_stance, len(votes),
        )
        return scores

    def validate_debate(
        self,
        debate_id:  str,
        correct:    bool,
        validator:  str = "unknown",
    ):
        """
        Post-hoc validation: was the winning stance actually correct?
        Updates validated_correct / validated_wrong for all voters on winning side.
        """
        if debate_id not in self._debates:
            logger.warning("Cannot validate unknown debate: %s", debate_id)
            return

        event = self._debates[debate_id]
        event.validated = correct
        event.validator = validator

        winning_voters = [m for m, v in event.votes.items() if v == event.winner_stance]
        losing_voters  = [m for m, v in event.votes.items() if v != event.winner_stance]

        for model in winning_voters:
            rec = self._ensure_model(model)
            if correct:
                rec.validated_correct += 1
            else:
                rec.validated_wrong += 1

        # Losing voters were "actually right" if winner was wrong
        for model in losing_voters:
            rec = self._ensure_model(model)
            if not correct:
                rec.validated_correct += 1
            else:
                rec.validated_wrong += 1

        self._save()
        logger.info("Validated debate %s: correct=%s | validator=%s", debate_id, correct, validator)

    def get_vote_weights(self, models: list[str]) -> dict[str, float]:
        """
        Return reliability-based vote weights for a list of models.
        Weights are normalised to sum to 1.0.
        Used by SwarmDebateEngine to apply weighted voting.
        """
        raw = {}
        for model in models:
            rec = self._models.get(model)
            raw[model] = rec.reliability_score if rec else 0.5

        total = sum(raw.values()) or 1.0
        return {m: round(w / total, 4) for m, w in raw.items()}

    def get_leaderboard(self, top_k: int = 10) -> list[LeaderboardEntry]:
        """Return top-k models ranked by reliability score."""
        ranked = sorted(
            self._models.values(),
            key=lambda r: r.reliability_score,
            reverse=True,
        )[:top_k]

        return [
            LeaderboardEntry(
                rank=i + 1,
                model=rec.model_name,
                elo=round(rec.elo_score, 1),
                reliability=rec.reliability_score,
                win_rate=rec.win_rate,
                debates=rec.debates,
                validated_correct=rec.validated_correct,
            )
            for i, rec in enumerate(ranked)
        ]

    def get_model_stats(self, model: str) -> Optional[dict]:
        rec = self._models.get(model)
        return rec.to_dict() if rec else None

    def all_stats(self) -> list[dict]:
        return [rec.to_dict() for rec in self._models.values()]
