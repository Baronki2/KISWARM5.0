"""
KISWARM v3.0 — SENTINEL API SERVER
====================================
REST interface covering all 10 modules.
Port: 11436

Endpoints (29 total):
  --- Core AKE ---
  POST /sentinel/extract         Trigger knowledge extraction
  POST /sentinel/debate          Swarm Debate for conflicts
  GET  /sentinel/search          Search swarm memory
  GET  /sentinel/status          Full system status

  --- v2.2 Modules ---
  POST /firewall/scan            M6: Adversarial pattern scan
  GET  /decay/scan               M2: Decay scan + revalidation list
  GET  /decay/record/<hash_id>   M2: Single entry confidence
  POST /decay/revalidate         M2: Mark entry revalidated
  GET  /ledger/status            M4: Merkle summary
  GET  /ledger/verify            M4: Full tamper detection
  GET  /ledger/proof/<hash_id>   M4: Inclusion proof
  POST /conflict/analyze         M1: Contradiction clusters
  POST /conflict/quick           M1: Two-text cosine check
  GET  /tracker/leaderboard      M3: ELO + reliability ranking
  GET  /tracker/model/<name>     M3: Per-model stats
  POST /tracker/validate         M3: Post-hoc validation
  POST /guard/assess             M5: Retrieval trust assessment

  --- v3.0 Industrial AI Modules ---
  POST /fuzzy/classify           M7: Fuzzy membership classification
  POST /fuzzy/update             M7: Feed outcome for online tuning
  POST /fuzzy/tune               M7: Run auto-tuning cycle
  GET  /fuzzy/stats              M7: Tuner state + Lyapunov energy
  POST /rl/act                   M8: Get constrained RL action
  POST /rl/learn                 M8: Feed reward + costs for learning
  GET  /rl/stats                 M8: Policy stats + Lagrange multipliers
  POST /twin/evaluate            M9: Digital twin mutation evaluation
  GET  /twin/stats               M9: Promotion/rejection history
  POST /mesh/share               M10: Submit node parameter share
  POST /mesh/register            M10: Register new mesh node
  GET  /mesh/leaderboard         M10: Node trust leaderboard
  GET  /mesh/stats               M10: Global mesh state

  GET  /health                   Service health ping

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 3.0
"""

import asyncio
import datetime
import json
import logging
import os
import sys
import subprocess
from pathlib import Path

try:
    from flask import Flask, jsonify, request
    from flask_cors import CORS
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "flask-cors"], check=True)
    from flask import Flask, jsonify, request
    from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).parent.parent))

from sentinel.sentinel_bridge import SentinelBridge, IntelligencePacket
from sentinel.swarm_debate import SwarmDebateEngine
from sentinel.semantic_conflict import SemanticConflictDetector
from sentinel.knowledge_decay import KnowledgeDecayEngine
from sentinel.model_tracker import ModelPerformanceTracker
from sentinel.crypto_ledger import CryptographicKnowledgeLedger
from sentinel.retrieval_guard import DifferentialRetrievalGuard
from sentinel.prompt_firewall import AdversarialPromptFirewall
# v3.0
from sentinel.fuzzy_tuner import FuzzyAutoTuner
from sentinel.constrained_rl import ConstrainedRLAgent, SwarmState, SwarmAction
from sentinel.digital_twin import DigitalTwin
from sentinel.federated_mesh import (
    FederatedMeshCoordinator, FederatedMeshNode,
    NodeShare, compute_attestation,
)

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

LOG_DIR = os.path.join(os.environ.get("KISWARM_HOME", os.path.expanduser("~")), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SENTINEL-API] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "sentinel_api.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sentinel_api")

# ── Singletons ────────────────────────────────────────────────────────────────
_bridge    = SentinelBridge()
_debate    = SwarmDebateEngine()
_conflict  = SemanticConflictDetector()
_decay     = KnowledgeDecayEngine()
_tracker   = ModelPerformanceTracker()
_ledger    = CryptographicKnowledgeLedger()
_guard     = DifferentialRetrievalGuard(ledger=_ledger, decay_engine=_decay)
_firewall  = AdversarialPromptFirewall()
# v3.0
_fuzzy     = FuzzyAutoTuner()
_rl        = ConstrainedRLAgent()
_twin      = DigitalTwin()
_mesh      = FederatedMeshCoordinator(param_dim=8)

_start = datetime.datetime.now()
_stats = {
    "extractions": 0, "debates": 0, "searches": 0,
    "firewall_scans": 0, "firewall_blocked": 0,
    "decay_scans": 0, "ledger_verifications": 0, "guard_assessments": 0,
    # v3.0
    "fuzzy_classifies": 0, "fuzzy_tune_cycles": 0,
    "rl_actions": 0, "rl_learn_steps": 0,
    "twin_evaluations": 0, "twin_accepted": 0,
    "mesh_rounds": 0, "mesh_shares": 0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE AKE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    uptime = (datetime.datetime.now() - _start).total_seconds()
    return jsonify({
        "status":   "active",
        "service":  "KISWARM-SENTINEL-BRIDGE",
        "version":  "3.0",
        "port":     11436,
        "modules":  10,
        "endpoints": 29,
        "uptime":   round(uptime, 1),
        "stats":    _stats,
        "timestamp": datetime.datetime.now().isoformat(),
    })


@app.route("/health/check-all")
def health_check_all():
    """
    Comprehensive startup health check that validates all endpoints and components.
    Returns detailed status of each subsystem.
    """
    import traceback
    checks = {}
    all_healthy = True

    # 1. Core Components Health
    components = [
        ("bridge", lambda: _bridge is not None),
        ("debate_engine", lambda: _debate is not None),
        ("conflict_detector", lambda: _conflict is not None),
        ("decay_engine", lambda: _decay is not None),
        ("model_tracker", lambda: _tracker is not None),
        ("crypto_ledger", lambda: _ledger is not None),
        ("retrieval_guard", lambda: _guard is not None),
        ("prompt_firewall", lambda: _firewall is not None),
        ("fuzzy_tuner", lambda: _fuzzy is not None),
        ("rl_agent", lambda: _rl is not None),
        ("digital_twin", lambda: _twin is not None),
        ("federated_mesh", lambda: _mesh is not None),
    ]

    for name, check_fn in components:
        try:
            healthy = check_fn()
            checks[name] = {"status": "ok" if healthy else "degraded", "error": None}
            if not healthy:
                all_healthy = False
        except Exception as e:
            checks[name] = {"status": "error", "error": str(e)}
            all_healthy = False

    # 2. Memory System Check (Qdrant)
    try:
        memory_ok = _bridge.memory.client is not None
        checks["qdrant_memory"] = {"status": "ok" if memory_ok else "degraded",
                                   "connected": memory_ok}
    except Exception as e:
        checks["qdrant_memory"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # 3. Ollama LLM Check
    try:
        import ollama
        models = ollama.list()
        # Handle both dict and ListResponse object types
        if hasattr(models, 'models'):
            model_count = len(models.models) if models.models else 0
        elif isinstance(models, dict):
            model_count = len(models.get('models', []))
        else:
            model_count = 0
        checks["ollama"] = {"status": "ok", "models_available": model_count}
    except Exception as e:
        checks["ollama"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # 4. Ledger Integrity Check
    try:
        ledger_valid = _ledger.size >= 0  # Basic sanity check
        checks["ledger_integrity"] = {
            "status": "ok",
            "entries": _ledger.size,
            "root": _ledger.root[:16] + "..." if _ledger.root else "empty"
        }
    except Exception as e:
        checks["ledger_integrity"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # 5. Decay Engine Check
    try:
        report = _decay.scan()
        checks["decay_engine"] = {
            "status": "ok",
            "scanned": report.scanned,
            "needs_revalidation": report.needs_revalidation
        }
    except Exception as e:
        checks["decay_engine"] = {"status": "error", "error": str(e)}

    # 6. Model Tracker Check
    try:
        board = _tracker.get_leaderboard()
        checks["model_tracker"] = {"status": "ok", "models_tracked": len(board)}
    except Exception as e:
        checks["model_tracker"] = {"status": "error", "error": str(e)}

    # 7. Firewall Check
    try:
        test_scan = _firewall.scan("test query")
        checks["firewall"] = {"status": "ok", "test_passed": not test_scan.blocked}
    except Exception as e:
        checks["firewall"] = {"status": "error", "error": str(e)}

    # 8. Sentence Transformers Check (semantic embeddings)
    try:
        from sentence_transformers import SentenceTransformer
        checks["sentence_transformers"] = {"status": "ok", "available": True}
    except ImportError:
        checks["sentence_transformers"] = {"status": "degraded",
                                           "error": "Not installed - semantic embeddings unavailable"}
    except Exception as e:
        checks["sentence_transformers"] = {"status": "error", "error": str(e)}

    # 9. CIEC Modules Availability Check
    ciec_modules = []
    for mod_name in ["plc_parser", "scada_observer", "physics_twin",
                     "rule_engine", "knowledge_graph", "actor_critic"]:
        try:
            __import__(f"sentinel.{mod_name}")
            ciec_modules.append({"name": mod_name, "status": "available"})
        except ImportError as e:
            ciec_modules.append({"name": mod_name, "status": "missing", "error": str(e)})
    checks["ciec_modules"] = {"status": "partial" if any(m["status"]=="missing" for m in ciec_modules) else "ok",
                              "modules": ciec_modules}

    # Summary
    uptime = (datetime.datetime.now() - _start).total_seconds()
    return jsonify({
        "overall_status": "healthy" if all_healthy else "degraded",
        "uptime_seconds": round(uptime, 1),
        "timestamp": datetime.datetime.now().isoformat(),
        "checks": checks,
        "stats": _stats,
    })


@app.route("/sentinel/extract", methods=["POST"])
def extract():
    data  = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"status": "error", "error": "query required"}), 400

    # v3.0: firewall-check query before AKE
    fw = _firewall.scan_query(query)
    if fw.blocked:
        return jsonify({"status": "blocked", "firewall": {
            "threat_score": fw.threat_score, "threats": list(fw.threat_types),
        }}), 403

    force     = bool(data.get("force", False))
    threshold = float(data.get("threshold", 0.85))
    _bridge.ckm.threshold = threshold

    try:
        result = run_async(_bridge.run(query, force=force))
        _stats["extractions"] += 1
        return jsonify(result)
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/sentinel/debate", methods=["POST"])
def debate():
    data = request.get_json() or {}
    for f in ["query", "content_a", "content_b"]:
        if not data.get(f):
            return jsonify({"status": "error", "error": f"{f} required"}), 400

    # Firewall both content payloads
    for key in ["content_a", "content_b"]:
        fw = _firewall.scan(data[key])
        if fw.blocked:
            return jsonify({"status": "blocked", "field": key,
                            "threat_score": fw.threat_score}), 403

    try:
        verdict = run_async(_debate.debate(
            query=data["query"],
            content_a=data["content_a"], content_b=data["content_b"],
            source_a_name=data.get("source_a", "Source A"),
            source_b_name=data.get("source_b", "Source B"),
        ))
        _stats["debates"] += 1
        return jsonify({
            "status":          "success",
            "winning_content": verdict.winning_content,
            "confidence":      verdict.confidence,
            "vote_tally":      verdict.vote_tally,
            "dissenting":      verdict.dissenting_models,
            "synthesis":       verdict.synthesis,
            "timestamp":       verdict.timestamp,
        })
    except Exception as exc:
        logger.error("Debate failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/sentinel/search")
def search():
    query = request.args.get("q", "").strip()
    top_k = int(request.args.get("top_k", 5))
    if not query:
        return jsonify({"status": "error", "error": "q required"}), 400
    try:
        results = _bridge.memory.search(query, top_k=top_k)
        _stats["searches"] += 1
        return jsonify({"status": "success", "query": query, "results": results, "total": len(results)})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/sentinel/status")
def sentinel_status():
    uptime = (datetime.datetime.now() - _start).total_seconds()
    return jsonify({
        "system":    "KISWARM-SENTINEL-v3.0",
        "status":    "operational",
        "uptime":    round(uptime, 1),
        "stats":     _stats,
        "threshold": _bridge.ckm.threshold,
        "scouts":    [s.__class__.__name__ for s in _bridge.scouts],
        "qdrant":    _bridge.memory.client is not None,
        "ledger":    {"entries": _ledger.size, "root": _ledger.root[:16] + "..."},
        "mesh":      _mesh.get_stats(),
        "timestamp": datetime.datetime.now().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# v2.2 MODULE ENDPOINTS (preserved)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/firewall/scan", methods=["POST"])
def firewall_scan():
    data    = request.get_json() or {}
    content = data.get("content", "")
    source  = data.get("source", "unknown")
    if not content:
        return jsonify({"status": "error", "error": "content required"}), 400
    report = _firewall.scan(content, source=source)
    _stats["firewall_scans"] += 1
    if report.blocked:
        _stats["firewall_blocked"] += 1
    return jsonify({
        "status":       "success",
        "blocked":      report.blocked,
        "threat_level": report.threat_level,
        "threat_score": report.threat_score,
        "threats":      report.threat_types,
        "matches":      [{"pattern": m.pattern_name, "severity": m.severity} for m in report.matches],
        "statistical":  report.statistical,
        "recommendation": report.recommendation,
    })


@app.route("/decay/scan")
def decay_scan():
    report = _decay.scan()
    _stats["decay_scans"] += 1
    return jsonify({
        "status":             "success",
        "needs_revalidation": report.needs_revalidation,
        "retired":            report.retired,
        "healthy":            report.healthy,
        "total":              report.scanned,
    })


@app.route("/decay/record/<hash_id>")
def decay_record(hash_id: str):
    conf = _decay.get_confidence(hash_id)
    return jsonify({"status": "success", "hash_id": hash_id, "confidence": conf})


@app.route("/decay/revalidate", methods=["POST"])
def decay_revalidate():
    data    = request.get_json() or {}
    hash_id = data.get("hash_id", "")
    new_conf = float(data.get("confidence", 0.9))
    if not hash_id:
        return jsonify({"status": "error", "error": "hash_id required"}), 400
    _decay.mark_revalidated(hash_id, new_conf)
    return jsonify({"status": "success", "hash_id": hash_id, "new_confidence": new_conf})


@app.route("/ledger/status")
def ledger_status():
    return jsonify({
        "status":  "success",
        "entries": _ledger.size,
        "root":    _ledger.root,
        "valid":   _ledger.size > 0,
    })


@app.route("/ledger/verify")
def ledger_verify():
    report = _ledger.verify_integrity()
    _stats["ledger_verifications"] += 1
    return jsonify({
        "status":          "success",
        "valid":           report.valid,
        "total_entries":   report.total_entries,
        "tampered_entries": report.tampered_entries,
        "root_match":      report.root_match,
    })


@app.route("/ledger/proof/<hash_id>")
def ledger_proof(hash_id: str):
    proof = _ledger.get_proof(hash_id)
    if proof is None:
        return jsonify({"status": "error", "error": "entry not found"}), 404
    return jsonify({"status": "success", "hash_id": hash_id, "proof": proof})


@app.route("/conflict/analyze", methods=["POST"])
def conflict_analyze():
    data    = request.get_json() or {}
    packets = data.get("packets", [])
    if not packets:
        return jsonify({"status": "error", "error": "packets required"}), 400
    ips = [IntelligencePacket(
        content=p.get("content", ""),
        source=p.get("source", "unknown"),
        confidence=float(p.get("confidence", 0.5)),
    ) for p in packets]
    report = _conflict.analyze(ips)
    return jsonify({
        "status":           "success",
        "total_pairs":      report.total_pairs,
        "conflict_pairs":   len(report.conflict_pairs),
        "resolution_needed": report.resolution_needed,
        "clusters":         len(report.clusters),
        "severity":         report.severity_label,
        "pairs": [{
            "source_a":  p.source_a, "source_b": p.source_b,
            "similarity": p.similarity, "severity": p.severity,
        } for p in report.conflict_pairs],
    })


@app.route("/conflict/quick", methods=["POST"])
def conflict_quick():
    data = request.get_json() or {}
    a, b = data.get("text_a", ""), data.get("text_b", "")
    if not a or not b:
        return jsonify({"status": "error", "error": "text_a and text_b required"}), 400
    sim, severity = _conflict.quick_check(a, b)
    return jsonify({"status": "success", "similarity": sim, "severity": severity})


@app.route("/tracker/leaderboard")
def tracker_leaderboard():
    board = _tracker.get_leaderboard()
    return jsonify({
        "status":      "success",
        "leaderboard": [{"rank": e.rank, "model": e.model_id, "elo": e.elo,
                         "reliability": e.reliability_score, "debates": e.total_debates,
                         "win_rate": e.win_rate} for e in board],
    })


@app.route("/tracker/model/<path:model_name>")
def tracker_model(model_name: str):
    rec = _tracker.get_model(model_name)
    if rec is None:
        return jsonify({"status": "error", "error": "model not found"}), 404
    return jsonify({"status": "success", "model": rec.model_id, "elo": rec.elo,
                    "reliability_score": rec.reliability_score,
                    "debates": rec.total_debates, "win_rate": rec.win_rate,
                    "validation_accuracy": rec.validation_accuracy})


@app.route("/tracker/validate", methods=["POST"])
def tracker_validate():
    data = request.get_json() or {}
    debate_id = data.get("debate_id", "")
    correct_winner = data.get("correct_winner", "")
    if not debate_id or not correct_winner:
        return jsonify({"status": "error", "error": "debate_id and correct_winner required"}), 400
    _tracker.validate_debate(debate_id, correct_winner)
    return jsonify({"status": "success", "debate_id": debate_id, "validated_winner": correct_winner})


@app.route("/guard/assess", methods=["POST"])
def guard_assess():
    data = request.get_json() or {}
    hash_id   = data.get("hash_id", "")
    query     = data.get("query", "")
    retrieved = data.get("retrieved_content", "")
    fresh     = data.get("fresh_content", None)
    if not hash_id or not query or not retrieved:
        return jsonify({"status": "error", "error": "hash_id, query, retrieved_content required"}), 400
    report = _guard.assess(hash_id=hash_id, query=query, retrieved_content=retrieved, fresh_content=fresh)
    _stats["guard_assessments"] += 1
    return jsonify({
        "status":       "success",
        "trust_level":  report.trust_level,
        "trust_score":  report.trust_score,
        "recommendation": report.recommendation,
        "flags":        report.flags,
        "decay_confidence": report.decay_confidence,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# v3.0 MODULE 7 — FUZZY MEMBERSHIP AUTO-TUNING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/fuzzy/classify", methods=["POST"])
def fuzzy_classify():
    """
    Classify a confidence value through fuzzy membership functions.
    Body: { "confidence": 0.72 }
    Returns: { "label": "HIGH", "membership": 0.85, "all_memberships": {...} }
    """
    data = request.get_json() or {}
    x    = float(data.get("confidence", 0.5))
    label, mu = _fuzzy.classify(x)
    all_mu    = _fuzzy.all_memberships(x)
    _stats["fuzzy_classifies"] += 1
    return jsonify({
        "status":          "success",
        "confidence":      x,
        "label":           label,
        "membership":      mu,
        "all_memberships": all_mu,
    })


@app.route("/fuzzy/update", methods=["POST"])
def fuzzy_update():
    """
    Feed a validation outcome to the online tuner.
    Body: { "confidence": 0.72, "actual_quality": true, "actuation": 0.1 }
    """
    data       = request.get_json() or {}
    confidence = float(data.get("confidence", 0.5))
    quality    = bool(data.get("actual_quality", True))
    actuation  = float(data.get("actuation", 0.0))
    _fuzzy.update(confidence, quality, actuation)
    return jsonify({"status": "success", "recorded": True,
                    "buffer_size": len(_fuzzy._errors)})


@app.route("/fuzzy/tune", methods=["POST"])
def fuzzy_tune():
    """
    Run one auto-tuning cycle (evolutionary micro-mutations + Lyapunov check).
    Body: { "n_mutations": 5 }
    """
    data        = request.get_json() or {}
    n_mutations = int(data.get("n_mutations", 5))
    report      = _fuzzy.tune_cycle(n_mutations=n_mutations)
    _stats["fuzzy_tune_cycles"] += 1
    return jsonify({"status": "success", "tune_report": report})


@app.route("/fuzzy/stats")
def fuzzy_stats():
    """Get fuzzy tuner state: parameters, Lyapunov energy, iteration count."""
    return jsonify({"status": "success", "fuzzy": _fuzzy.get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v3.0 MODULE 8 — CONSTRAINED REINFORCEMENT LEARNING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/rl/act", methods=["POST"])
def rl_act():
    """
    Get the RL agent's recommended action for the current swarm state.
    Body: {
      "queue_depth": 0.6, "memory_pressure": 0.5,
      "model_availability": 0.9, "extraction_latency": 0.2,
      "scout_success_rate": 0.8, "debate_load": 0.1
    }
    """
    data  = request.get_json() or {}
    state = SwarmState(
        knowledge_queue_depth = float(data.get("queue_depth", 0.5)),
        memory_pressure       = float(data.get("memory_pressure", 0.3)),
        model_availability    = float(data.get("model_availability", 0.9)),
        extraction_latency    = float(data.get("extraction_latency", 0.2)),
        scout_success_rate    = float(data.get("scout_success_rate", 0.8)),
        debate_load           = float(data.get("debate_load", 0.1)),
    )
    action = _rl.act(state)
    _stats["rl_actions"] += 1
    return jsonify({
        "status":             "success",
        "scout_priority":     action.scout_priority,
        "extraction_rate":    action.extraction_rate,
        "debate_threshold":   action.debate_threshold,
        "cache_eviction_rate": action.cache_eviction_rate,
        "episode":            _rl._episode,
        "lambdas":            _rl.lagrange.lambdas,
    })


@app.route("/rl/learn", methods=["POST"])
def rl_learn():
    """
    Feed reward + constraint costs back to the RL agent for learning.
    Body: {
      "state": {...}, "action": {...},
      "reward": 0.8,
      "costs": [0.6, 0.3, 0.2],  // [memory, latency, model_load]
      "shielded": false
    }
    """
    data    = request.get_json() or {}
    s_data  = data.get("state", {})
    a_data  = data.get("action", {})
    reward  = float(data.get("reward", 0.0))
    costs   = data.get("costs", [0.3, 0.2, 0.1])
    shielded = bool(data.get("shielded", False))

    state  = SwarmState(
        knowledge_queue_depth = float(s_data.get("queue_depth", 0.5)),
        memory_pressure       = float(s_data.get("memory_pressure", 0.3)),
        model_availability    = float(s_data.get("model_availability", 0.9)),
        extraction_latency    = float(s_data.get("extraction_latency", 0.2)),
        scout_success_rate    = float(s_data.get("scout_success_rate", 0.8)),
        debate_load           = float(s_data.get("debate_load", 0.1)),
    )
    action = SwarmAction(
        scout_priority      = float(a_data.get("scout_priority", 0.5)),
        extraction_rate     = float(a_data.get("extraction_rate", 0.5)),
        debate_threshold    = float(a_data.get("debate_threshold", 0.3)),
        cache_eviction_rate = float(a_data.get("cache_eviction_rate", 0.1)),
    )

    _rl.learn(state=state, action=action, reward=reward,
              costs=costs, shielded=shielded)
    _stats["rl_learn_steps"] += 1
    return jsonify({"status": "success", "episode": _rl._episode,
                    "lambdas": _rl.lagrange.lambdas})


@app.route("/rl/stats")
def rl_stats():
    """Get RL agent state: episode count, mean reward, Lagrange multipliers."""
    return jsonify({"status": "success", "rl": _rl.get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v3.0 MODULE 9 — DIGITAL TWIN MUTATION EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/twin/evaluate", methods=["POST"])
def twin_evaluate():
    """
    Evaluate a candidate parameter set through the Digital Twin pipeline.
    Runs 75+ scenarios (Monte Carlo + rare-event + adversarial).
    Applies acceptance rules + EVT tail-risk analysis.

    Body: {
      "scout": 0.6, "rate": 0.55, "threshold": 0.3, "eviction": 0.12,
      "label": "candidate_v3",
      "set_as_baseline": false
    }
    """
    data      = request.get_json() or {}
    scout     = float(data.get("scout",     0.5))
    rate      = float(data.get("rate",      0.5))
    threshold = float(data.get("threshold", 0.3))
    eviction  = float(data.get("eviction",  0.1))
    label     = data.get("label", "api_candidate")
    as_baseline = bool(data.get("set_as_baseline", False))

    if as_baseline:
        _twin.set_baseline(scout, rate, threshold, eviction)
        return jsonify({"status": "success", "action": "baseline_set"})

    report = _twin.evaluate(scout, rate, threshold, eviction, label=label)
    _stats["twin_evaluations"] += 1
    if report.accepted:
        _stats["twin_accepted"] += 1

    return jsonify({
        "status":                   "success",
        "accepted":                 report.accepted,
        "rejection_reasons":        report.rejection_reasons,
        "n_scenarios":              report.n_scenarios,
        "hard_violations":          report.hard_violations,
        "stability_margin":         report.stability_margin_mean,
        "stability_baseline":       report.stability_margin_baseline,
        "efficiency_gain_pct":      round(report.efficiency_gain * 100, 2),
        "recovery_time":            report.recovery_time_mean,
        "recovery_time_baseline":   report.recovery_time_baseline,
        "tail_heavier":             report.tail_heavier,
        "tail_index_baseline":      report.tail_index_baseline,
        "tail_index_candidate":     report.tail_index_candidate,
        "adversarial_violations":   report.adversarial_violations,
        "timestamp":                report.timestamp,
    })


@app.route("/twin/stats")
def twin_stats():
    """Get digital twin promotion/rejection history and rates."""
    return jsonify({"status": "success", "twin": _twin.get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v3.0 MODULE 10 — FEDERATED ADAPTIVE MESH
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/mesh/register", methods=["POST"])
def mesh_register():
    """
    Register a new node in the federated mesh.
    Body: { "node_id": "ollama_node_01", "initial_trust": 0.8 }
    """
    data    = request.get_json() or {}
    node_id = data.get("node_id", "")
    trust   = float(data.get("initial_trust", 0.8))
    if not node_id:
        return jsonify({"status": "error", "error": "node_id required"}), 400
    _mesh.register_node(node_id, trust)
    return jsonify({"status": "success", "node_id": node_id,
                    "registered_nodes": len(_mesh._nodes)})


@app.route("/mesh/share", methods=["POST"])
def mesh_share():
    """
    Submit a node's parameter share for the next aggregation round.
    Triggers aggregation when at least 2 shares are received.

    Body: {
      "node_id": "ollama_node_01",
      "param_delta": [0.01, -0.02, ...],
      "perf_delta": 0.05,
      "stability_cert": 0.85,
      "uptime": 0.99
    }
    """
    data     = request.get_json() or {}
    node_id  = data.get("node_id", "")
    if not node_id:
        return jsonify({"status": "error", "error": "node_id required"}), 400

    import time
    ts    = float(data.get("timestamp", time.time()))
    delta = data.get("param_delta", [0.0] * 8)
    stab  = float(data.get("stability_cert", 0.8))
    perf  = float(data.get("perf_delta", 0.0))
    upt   = float(data.get("uptime", 1.0))

    share = NodeShare(
        node_id=node_id,
        param_delta=delta,
        perf_delta=perf,
        stability_cert=stab,
        uptime=upt,
        timestamp=ts,
    )
    share.sign()   # compute attestation

    # Auto-aggregate: collect shares and run round
    if not hasattr(app, "_pending_shares"):
        app._pending_shares = []
    app._pending_shares.append(share)

    _stats["mesh_shares"] += 1

    # Run aggregation round if we have at least 2 shares
    if len(app._pending_shares) >= 2:
        report = _mesh.aggregate_round(app._pending_shares)
        app._pending_shares = []
        _stats["mesh_rounds"] += 1
        return jsonify({
            "status":         "success",
            "aggregation":    "completed",
            "participating":  report.participating,
            "rejected":       report.rejected,
            "quorum":         report.quorum_reached,
            "global_params":  _mesh.global_params,
        })

    return jsonify({
        "status":         "success",
        "aggregation":    "pending",
        "shares_queued":  len(app._pending_shares),
    })


@app.route("/mesh/leaderboard")
def mesh_leaderboard():
    """Node trust leaderboard ranked by reliability weight."""
    board = _mesh.node_leaderboard()
    return jsonify({"status": "success", "nodes": board})


@app.route("/mesh/stats")
def mesh_stats():
    """Global mesh state: round count, node count, global params."""
    return jsonify({"status": "success", "mesh": _mesh.get_stats()})



# ═══════════════════════════════════════════════════════════════════════════════
# CIEC v4.0 — LAZY MODULE SINGLETONS
# ═══════════════════════════════════════════════════════════════════════════════

def _lazy(attr, factory):
    if not hasattr(app, attr):
        setattr(app, attr, factory())
    return getattr(app, attr)

def _plc():
    from sentinel.plc_parser import PLCSemanticParser
    return _lazy("_plc_parser", PLCSemanticParser)

def _scada():
    from sentinel.scada_observer import SCADAObserver
    return _lazy("_scada_obs", SCADAObserver)

def _ptwin():
    from sentinel.physics_twin import PhysicsTwin
    return _lazy("_physics_twin", PhysicsTwin)

def _rules():
    from sentinel.rule_engine import RuleConstraintEngine
    return _lazy("_rule_eng", RuleConstraintEngine)

def _kg():
    from sentinel.knowledge_graph import KnowledgeGraph
    return _lazy("_know_graph", KnowledgeGraph)

def _acrl():
    from sentinel.actor_critic import IndustrialActorCritic
    return _lazy("_actor_crit", lambda: IndustrialActorCritic(state_dim=32))


# ─── MODULE 11: PLC SEMANTIC PARSER ──────────────────────────────────────────

@app.route("/plc/parse", methods=["POST"])
def plc_parse():
    """Parse IEC 61131-3 ST source into CIR + DSG + PID/interlock/watchdog.
    Body: {"source": "<ST code>", "program_name": "optional"}
    """
    d = request.get_json(force=True) or {}
    if not d.get("source"):
        return jsonify({"status": "error", "error": "source required"}), 400
    result = _plc().parse(d["source"], d.get("program_name", "UNKNOWN"))
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/plc/stats")
def plc_stats():
    """PLC parser cache and performance statistics."""
    return jsonify({"status": "success", "stats": _plc().get_stats()})


# ─── MODULE 12: SCADA / OPC / SQL OBSERVATION LAYER ──────────────────────────

@app.route("/scada/push", methods=["POST"])
def scada_push():
    """Ingest real-time tag readings (OPC UA callback).
    Body: {"tag": "temperature", "value": 45.2}
      OR  {"snapshot": {"tag1": v1, "tag2": v2}}
    """
    d = request.get_json(force=True) or {}
    if "snapshot" in d:
        _scada().push_snapshot(d["snapshot"])
        return jsonify({"status": "success", "ingested": len(d["snapshot"])})
    tag, value = d.get("tag"), d.get("value")
    if tag is None or value is None:
        return jsonify({"status": "error", "error": "tag and value required"}), 400
    _scada().push_reading(tag, float(value))
    return jsonify({"status": "success", "ingested": 1})

@app.route("/scada/ingest-history", methods=["POST"])
def scada_ingest_history():
    """Batch ingest SQL historian records.
    Body: {"records": [{"tag": "t1", "value": 1.0, "timestamp": 1700000000.0}]}
    """
    d = request.get_json(force=True) or {}
    records = d.get("records", [])
    if not records:
        return jsonify({"status": "error", "error": "records array required"}), 400
    count = _scada().ingest_history(records)
    return jsonify({"status": "success", "ingested": count})

@app.route("/scada/state")
def scada_state():
    """Build and return current plant state vector S(t)."""
    sv = _scada().build_state_vector()
    return jsonify({"status": "success", "state": sv.to_dict()})

@app.route("/scada/anomalies")
def scada_anomalies():
    """Return tags showing anomalous values (z-score based)."""
    threshold = float(request.args.get("threshold", 3.0))
    anomalies = _scada().get_anomalies(std_threshold=threshold)
    return jsonify({"status": "success", "anomalies": anomalies, "count": len(anomalies)})

@app.route("/scada/stats")
def scada_stats():
    """SCADA observer statistics and subscribed tag list."""
    return jsonify({"status": "success", "stats": _scada().get_stats()})


# ─── MODULE 13: DIGITAL TWIN PHYSICS ENGINE ──────────────────────────────────

@app.route("/ciec-twin/run", methods=["POST"])
def ciec_twin_run():
    """Run a physics simulation episode (thermal + pump + battery + power).
    Body: {"steps": 100, "dt": 0.1, "q_in": 2000, "dp": 2.0,
           "i_charge": 10, "i_disch": 8, "inject_faults": false}
    """
    d = request.get_json(force=True) or {}
    result = _ptwin().run(
        steps=int(d.get("steps", 100)), dt=float(d.get("dt", 0.1)),
        q_in=float(d.get("q_in", 2000.0)), dp=float(d.get("dp", 2.0)),
        i_charge=float(d.get("i_charge", 10.0)), i_disch=float(d.get("i_disch", 8.0)),
        inject_faults=bool(d.get("inject_faults", False)),
    )
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/ciec-twin/evaluate", methods=["POST"])
def ciec_twin_evaluate():
    """Evaluate a mutation candidate against the digital twin.
    Body: {"params": {...mutation params...}, "n_runs": 3}
    Returns: {"promoted": bool, "metrics": {...fitness scores...}}
    """
    d = request.get_json(force=True) or {}
    promote, metrics = _ptwin().evaluate_mutation(
        d.get("params", {}), n_runs=int(d.get("n_runs", 3))
    )
    return jsonify({"status": "success", "promoted": promote, "metrics": metrics})

@app.route("/ciec-twin/stats")
def ciec_twin_stats():
    """Physics twin simulation statistics."""
    return jsonify({"status": "success", "stats": _ptwin().get_stats()})


# ─── MODULE 14: RULE CONSTRAINT ENGINE ───────────────────────────────────────

@app.route("/constraints/validate", methods=["POST"])
def constraints_validate():
    """Validate proposed parameter action against all safety constraints.
    Body: {"state": {"pressure": 3.0, "battery_soc": 0.8}, "action": {"delta_kp": 0.02}}
    Returns: {"allowed": bool, "total_penalty": float, "hard_violations": [...]}
    """
    d = request.get_json(force=True) or {}
    result = _rules().validate(d.get("state", {}), d.get("action", {}))
    return jsonify({"status": "success", "validation": result.to_dict()})

@app.route("/constraints/check-state", methods=["POST"])
def constraints_check_state():
    """Quick hard-constraint safety check on current plant state.
    Body: {"state": {"pressure": 3.0, ...}}
    """
    d = request.get_json(force=True) or {}
    return jsonify({"status": "success",
                    "safe": _rules().is_safe_state(d.get("state", {}))})

@app.route("/constraints/list")
def constraints_list():
    """List all registered hard and soft constraints."""
    return jsonify({"status": "success",
                    "constraints": _rules().get_constraints()})

@app.route("/constraints/violations")
def constraints_violations():
    """Recent constraint violation audit log."""
    n = int(request.args.get("n", 50))
    hist = _rules().get_violation_history(n)
    return jsonify({"status": "success", "violations": hist, "count": len(hist)})

@app.route("/constraints/stats")
def constraints_stats():
    """Constraint engine statistics: check count, block rate, violations by category."""
    return jsonify({"status": "success", "stats": _rules().get_stats()})


# ─── MODULE 15: CROSS-PROJECT KNOWLEDGE GRAPH ────────────────────────────────

@app.route("/kg/add-pid", methods=["POST"])
def kg_add_pid():
    """Store a proven PID configuration in the cross-project knowledge graph.
    Body: {"title": "...", "kp": 1.2, "ki": 0.3, "kd": 0.05,
           "sample_time": 0.1, "output_min": 0, "output_max": 100,
           "plant_type": "pump", "site_id": "PLANT_A", "project_id": "2024"}
    """
    d = request.get_json(force=True) or {}
    node = _kg().add_pid_config(
        title=d.get("title", "PID Config"),
        kp=float(d.get("kp", 1.0)), ki=float(d.get("ki", 0.1)),
        kd=float(d.get("kd", 0.01)), sample_time=float(d.get("sample_time", 0.1)),
        output_min=float(d.get("output_min", 0.0)), output_max=float(d.get("output_max", 100.0)),
        plant_type=d.get("plant_type", "generic"),
        site_id=d.get("site_id", ""), project_id=d.get("project_id", ""),
        tags=d.get("tags"),
    )
    return jsonify({"status": "success", "node_id": node.node_id})

@app.route("/kg/add-failure", methods=["POST"])
def kg_add_failure():
    """Record a failure signature and proven fix template.
    Body: {"title": "...", "symptoms": [...], "root_cause": "...",
           "fix_template": {...}, "site_id": "...", "project_id": "..."}
    """
    d = request.get_json(force=True) or {}
    node = _kg().add_failure_signature(
        title=d.get("title", "Failure"), symptoms=d.get("symptoms", []),
        root_cause=d.get("root_cause", ""), fix_template=d.get("fix_template", {}),
        site_id=d.get("site_id", ""), project_id=d.get("project_id", ""),
    )
    return jsonify({"status": "success", "node_id": node.node_id})

@app.route("/kg/find-similar", methods=["POST"])
def kg_find_similar():
    """Query knowledge graph for nodes similar to current context.
    Body: {"vector": [1.2, 0.3, 0.05], "tags": ["pump"], "kind": "PIDConfig", "top_k": 5}
    """
    d = request.get_json(force=True) or {}
    matches = _kg().find_similar(
        query_vector=d.get("vector", []), query_tags=d.get("tags", []),
        kind_filter=d.get("kind"), top_k=int(d.get("top_k", 5)),
        min_similarity=float(d.get("min_similarity", 0.1)),
    )
    return jsonify({"status": "success", "matches": [m.to_dict() for m in matches]})

@app.route("/kg/find-by-symptoms", methods=["POST"])
def kg_find_by_symptoms():
    """Match failure signatures to observed symptoms.
    Body: {"symptoms": ["pressure_drop", "high_vibration"], "top_k": 5}
    """
    d = request.get_json(force=True) or {}
    matches = _kg().find_by_symptoms(d.get("symptoms", []),
                                      top_k=int(d.get("top_k", 5)))
    return jsonify({"status": "success", "matches": [m.to_dict() for m in matches]})

@app.route("/kg/recurring-patterns")
def kg_recurring_patterns():
    """Detect problems solved multiple times across projects/sites.
    This is the 'you solved this pump cavitation 4 times in 8 years' detector.
    """
    min_occ  = int(request.args.get("min_occurrences", 2))
    patterns = _kg().detect_recurring_patterns(min_occurrences=min_occ)
    return jsonify({"status": "success", "patterns": patterns, "count": len(patterns)})

@app.route("/kg/export-bundle")
def kg_export_bundle():
    """Export signed knowledge diff bundle for federated multi-site sync."""
    since  = float(request.args.get("since", 0.0))
    bundle = _kg().export_diff_bundle(since_timestamp=since)
    return jsonify({"status": "success", "bundle": bundle})

@app.route("/kg/import-bundle", methods=["POST"])
def kg_import_bundle():
    """Import a verified knowledge diff bundle from another site."""
    d        = request.get_json(force=True) or {}
    bundle   = d.get("bundle", d)
    imported = _kg().import_diff_bundle(bundle)
    return jsonify({"status": "success", "imported": imported})

@app.route("/kg/nodes")
def kg_nodes():
    """List knowledge graph nodes (optionally filtered by kind)."""
    nodes = _kg().list_nodes(kind=request.args.get("kind"),
                              limit=int(request.args.get("limit", 50)))
    return jsonify({"status": "success", "nodes": nodes, "count": len(nodes)})

@app.route("/kg/stats")
def kg_stats():
    """Knowledge graph statistics: node count, edge count, sites, projects."""
    return jsonify({"status": "success", "stats": _kg().get_stats()})


# ─── MODULE 16: INDUSTRIAL ACTOR-CRITIC RL ───────────────────────────────────

@app.route("/ciec-rl/act", methods=["POST"])
def ciec_rl_act():
    """Get a constrained bounded parameter-shift action from the CIEC RL policy.
    Body: {"state": [0.1, 0.2, ...32 floats], "deterministic": false, "shield": true}
    Returns: {"action": {"delta_kp": 0.02, ...}, "info": {"shielded": false, ...}}
    Note: shield=true routes action through Rule Constraint Engine first.
    """
    d             = request.get_json(force=True) or {}
    state         = d.get("state", [0.0] * 32)
    deterministic = bool(d.get("deterministic", False))
    ce            = _rules() if bool(d.get("shield", True)) else None
    action, info  = _acrl().select_action(state, deterministic=deterministic,
                                           constraint_check=ce)
    return jsonify({"status": "success", "action": action, "info": info})

@app.route("/ciec-rl/observe", methods=["POST"])
def ciec_rl_observe():
    """Feed a transition into the constrained RL replay buffer.
    Body: {"state": [...], "action": [...], "reward": 1.0,
           "next_state": [...], "done": false, "cost": 0.0}
    """
    d = request.get_json(force=True) or {}
    if not d.get("state"):
        return jsonify({"status": "error", "error": "state required"}), 400
    _acrl().observe(state=d["state"], action=d.get("action", []),
                    reward=float(d.get("reward", 0.0)),
                    next_state=d.get("next_state", d["state"]),
                    done=bool(d.get("done", False)),
                    cost=float(d.get("cost", 0.0)))
    return jsonify({"status": "success", "buffer_size": len(_acrl().buffer)})

@app.route("/ciec-rl/update", methods=["POST"])
def ciec_rl_update():
    """Trigger one constrained actor-critic gradient update with Lagrangian penalty."""
    d      = request.get_json(force=True) or {}
    result = _acrl().update(batch_size=int(d.get("batch_size", 64)))
    return jsonify({"status": "success", "update": result})

@app.route("/ciec-rl/stats")
def ciec_rl_stats():
    """CIEC RL statistics: steps, episodes, shield rate, lambda values, PLC bounds."""
    return jsonify({"status": "success", "stats": _acrl().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 CIEC INDUSTRIAL EVOLUTION — LAZY INIT
# ═══════════════════════════════════════════════════════════════════════════════

_td3_inst   = None
_ast_inst   = None
_ephys_inst = None
_vmw_inst   = None
_fv_inst    = None
_byz_inst   = None
_mgov_inst  = None

def _td3():
    global _td3_inst
    if _td3_inst is None:
        from python.sentinel.td3_controller import TD3IndustrialController
        _td3_inst = TD3IndustrialController(state_dim=256)
    return _td3_inst

def _ast():
    global _ast_inst
    if _ast_inst is None:
        from python.sentinel.ast_parser import IEC61131ASTParser
        _ast_inst = IEC61131ASTParser()
    return _ast_inst

def _ephys():
    global _ephys_inst
    if _ephys_inst is None:
        from python.sentinel.extended_physics import ExtendedPhysicsTwin
        _ephys_inst = ExtendedPhysicsTwin()
    return _ephys_inst

def _vmw():
    global _vmw_inst
    if _vmw_inst is None:
        from python.sentinel.vmware_orchestrator import VMwareOrchestrator
        _vmw_inst = VMwareOrchestrator()
    return _vmw_inst

def _fv():
    global _fv_inst
    if _fv_inst is None:
        from python.sentinel.formal_verification import FormalVerificationEngine
        _fv_inst = FormalVerificationEngine()
    return _fv_inst

def _byz():
    global _byz_inst
    if _byz_inst is None:
        from python.sentinel.byzantine_aggregator import ByzantineFederatedAggregator
        _byz_inst = ByzantineFederatedAggregator()
    return _byz_inst

def _mgov():
    global _mgov_inst
    if _mgov_inst is None:
        from python.sentinel.mutation_governance import MutationGovernanceEngine
        _mgov_inst = MutationGovernanceEngine()
    return _mgov_inst


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 17 — TD3 INDUSTRIAL CONTROLLER
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/td3/act", methods=["POST"])
def td3_act():
    """Select TD3 action for a given state vector."""
    data  = request.get_json() or {}
    state = data.get("state", [0.0] * 256)
    det   = data.get("deterministic", False)
    noise = data.get("exploration_noise", 0.05)
    action, info = _td3().select_action(state, deterministic=det,
                                         exploration_noise=noise)
    return jsonify({"status": "success", "action": action, "info": info})

@app.route("/td3/observe", methods=["POST"])
def td3_observe():
    """Push a transition into the replay buffer."""
    data = request.get_json() or {}
    _td3().observe(
        state      = data.get("state",      [0.0] * 256),
        action     = data.get("action",     [0.0] * 8),
        reward     = float(data.get("reward", 0.0)),
        next_state = data.get("next_state", [0.0] * 256),
        done       = bool(data.get("done",   False)),
        cost       = float(data.get("cost",  0.0)),
    )
    return jsonify({"status": "success", "buffer_size": len(_td3().buffer)})

@app.route("/td3/update", methods=["POST"])
def td3_update():
    """Perform one TD3 gradient update."""
    data   = request.get_json() or {}
    result = _td3().update(batch_size=data.get("batch_size"))
    return jsonify({"status": "success", "result": result})

@app.route("/td3/reward", methods=["POST"])
def td3_reward():
    """Compute CIEC reward from performance metrics."""
    data = request.get_json() or {}
    r = _td3().compute_reward(
        stability_score    = float(data.get("stability_score",    0.8)),
        efficiency_score   = float(data.get("efficiency_score",   0.7)),
        actuator_cycles    = float(data.get("actuator_cycles",    0.1)),
        boundary_violation = float(data.get("boundary_violation", 0.0)),
        oscillation        = float(data.get("oscillation",        0.05)),
    )
    return jsonify({"status": "success", "reward": round(r, 6)})

@app.route("/td3/stats", methods=["GET"])
def td3_stats():
    return jsonify({"status": "success", "stats": _td3().get_stats()})

@app.route("/td3/checkpoint", methods=["GET"])
def td3_checkpoint():
    return jsonify({"status": "success", "checkpoint": _td3().checkpoint()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 18 — IEC 61131-3 FULL AST PARSER
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/ast/parse", methods=["POST"])
def ast_parse():
    """Parse IEC 61131-3 ST source → AST + CFG + DDG + SDG."""
    data   = request.get_json() or {}
    source = data.get("source", "PROGRAM empty END_PROGRAM")
    name   = data.get("program_name", "UNKNOWN")
    result = _ast().parse(source, name)
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/ast/detect-patterns", methods=["POST"])
def ast_detect():
    """Parse + return detected PID blocks, interlocks, dead code."""
    data   = request.get_json() or {}
    source = data.get("source", "PROGRAM empty END_PROGRAM")
    result = _ast().parse(source)
    return jsonify({
        "status":      "success",
        "pid_blocks":  [{"name": p.name, "kp": p.kp, "ki": p.ki, "kd": p.kd}
                         for p in result.pid_blocks],
        "interlocks":  [{"name": i.name, "condition": i.condition,
                          "safety": i.safety} for i in result.interlocks],
        "dead_code":   [{"description": d.description, "line": d.line}
                         for d in result.dead_code],
        "var_count":   result.var_count,
        "stmt_count":  result.stmt_count,
    })

@app.route("/ast/cfg", methods=["POST"])
def ast_cfg():
    """Return Control Flow Graph for a given ST program."""
    data   = request.get_json() or {}
    source = data.get("source", "PROGRAM empty END_PROGRAM")
    result = _ast().parse(source)
    return jsonify({
        "status":    "success",
        "cfg_nodes": len(result.cfg),
        "cfg":       {k: {"kind": v.kind, "successors": v.successors,
                           "stmts": v.stmts}
                      for k, v in result.cfg.items()},
    })

@app.route("/ast/stats", methods=["GET"])
def ast_stats():
    return jsonify({"status": "success", "stats": _ast().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 19 — EXTENDED PHYSICS TWIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/ephys/step", methods=["POST"])
def ephys_step():
    """Advance all physics blocks by dt seconds."""
    data    = request.get_json() or {}
    inputs  = data.get("inputs", {})
    dt      = float(data.get("dt", 0.1))
    method  = data.get("method", "rk4")
    ss      = _ephys().step(inputs, dt, method)
    return jsonify({
        "status":         "success",
        "step":           ss.step,
        "t":              ss.t,
        "state":          ss.state,
        "hard_violation": ss.hard_violation,
        "fault_active":   ss.fault_active,
    })

@app.route("/ephys/episode", methods=["POST"])
def ephys_episode():
    """Run a complete simulation episode with optional fault injection."""
    data    = request.get_json() or {}
    n_steps = int(data.get("n_steps", 100))
    dt      = float(data.get("dt", 0.1))
    result  = _ephys().run_episode(n_steps=n_steps, dt=dt)
    return jsonify({"status": "success", "result": result})

@app.route("/ephys/evaluate-mutation", methods=["POST"])
def ephys_evaluate_mutation():
    """Evaluate parameter mutation across Monte Carlo episodes."""
    data         = request.get_json() or {}
    param_deltas = data.get("param_deltas", {})
    n_runs       = int(data.get("n_runs", 5))
    promoted, metrics = _ephys().evaluate_mutation(param_deltas, n_runs)
    return jsonify({"status": "success", "promoted": promoted, "metrics": metrics})

@app.route("/ephys/pump", methods=["POST"])
def ephys_pump():
    """Direct pump algebraic computation (Q, H, P)."""
    from python.sentinel.extended_physics import PumpBlock
    data   = request.get_json() or {}
    rpm    = float(data.get("RPM", 1450))
    params = data.get("params", {})
    result = PumpBlock().compute(rpm, params)
    return jsonify({"status": "success", "result": result})

@app.route("/ephys/battery-voltage", methods=["POST"])
def ephys_battery_voltage():
    """Compute battery terminal voltage."""
    from python.sentinel.extended_physics import BatteryBlock
    data  = request.get_json() or {}
    soc   = float(data.get("SOC",   0.8))
    I     = float(data.get("I",     10.0))
    R_int = float(data.get("R_int", 0.05))
    v     = BatteryBlock().compute_voltage(soc, I, R_int)
    return jsonify({"status": "success", "voltage": round(v, 4)})

@app.route("/ephys/stats", methods=["GET"])
def ephys_stats():
    return jsonify({"status": "success", "stats": _ephys().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 20 — VMWARE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/vmw/vms", methods=["GET"])
def vmw_list():
    return jsonify({"status": "success", "vms": _vmw().list_vms()})

@app.route("/vmw/vm/<vm_id>", methods=["GET"])
def vmw_get(vm_id):
    vm = _vmw().get_vm(vm_id)
    if not vm:
        return jsonify({"status": "error", "error": "VM not found"}), 404
    return jsonify({"status": "success", "vm": vm})

@app.route("/vmw/snapshot", methods=["POST"])
def vmw_snapshot():
    data = request.get_json() or {}
    result = _vmw().create_snapshot(
        vm_id    = data.get("vm_id", "VM-C"),
        snap_name= data.get("snap_name", f"snap_{int(time.time())}"),
        actor    = data.get("actor", "api"),
    )
    return jsonify({"status": "success" if result.get("ok") else "error",
                    "result": result})

@app.route("/vmw/revert", methods=["POST"])
def vmw_revert():
    data = request.get_json() or {}
    result = _vmw().revert_snapshot(
        vm_id     = data.get("vm_id"),
        snap_name = data.get("snap_name"),
        actor     = data.get("actor", "api"),
    )
    return jsonify({"status": "success" if result.get("ok") else "error",
                    "result": result})

@app.route("/vmw/clone", methods=["POST"])
def vmw_clone():
    data = request.get_json() or {}
    result = _vmw().clone_vm(
        src_vm_id      = data.get("src_vm_id", "VM-C"),
        clone_name     = data.get("clone_name"),
        isolate_network= bool(data.get("isolate_network", True)),
        actor          = data.get("actor", "api"),
    )
    return jsonify({"status": "success" if result.get("ok") else "error",
                    "result": result})

@app.route("/vmw/mutation/begin", methods=["POST"])
def vmw_mutation_begin():
    data = request.get_json() or {}
    mid  = _vmw().begin_mutation(
        source_vm    = data.get("source_vm", "VM-C"),
        param_deltas = data.get("param_deltas", {}),
        actor        = data.get("actor", "api"),
    )
    return jsonify({"status": "success", "mutation_id": mid})

@app.route("/vmw/mutation/promote", methods=["POST"])
def vmw_mutation_promote():
    data   = request.get_json() or {}
    result = _vmw().promote_mutation(
        mutation_id  = data.get("mutation_id"),
        approval_code= data.get("approval_code", ""),
    )
    return jsonify({"status": "success" if result.get("ok") else "error",
                    "result": result})

@app.route("/vmw/audit", methods=["GET"])
def vmw_audit():
    limit = int(request.args.get("limit", 50))
    return jsonify({"status": "success", "audit": _vmw().get_audit_log(limit)})

@app.route("/vmw/stats", methods=["GET"])
def vmw_stats():
    return jsonify({"status": "success", "stats": _vmw().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 21 — FORMAL VERIFICATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/fv/lyapunov", methods=["POST"])
def fv_lyapunov():
    """Verify Lyapunov stability of a linearized system matrix A."""
    data = request.get_json() or {}
    A    = data.get("A")
    if not A:
        # Default: 2x2 stable system
        A = [[0.9, 0.1], [-0.05, 0.85]]
    mid    = data.get("mutation_id", "api_check")
    result = _fv().verify_linearized(A, mutation_id=mid)
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/fv/barrier", methods=["POST"])
def fv_barrier():
    """Verify barrier certificate via sampling."""
    import math
    data     = request.get_json() or {}
    safe_set = data.get("safe_set", [[-1.0, 1.0], [-1.0, 1.0]])
    n_samples= int(data.get("n_samples", 200))
    mid      = data.get("mutation_id", "api_barrier")
    # Use quadratic B(x) = sum(xi^2) - bound as default
    bound    = float(data.get("barrier_bound", 1.5))
    def B(x): return bound - sum(xi*xi for xi in x)
    def f(x): return [-0.1*xi for xi in x]   # stable attractor
    result = _fv().verify_barrier(B, f, safe_set, n_samples, mutation_id=mid)
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/fv/full", methods=["POST"])
def fv_full():
    """Full verification: Lyapunov + optional barrier."""
    data = request.get_json() or {}
    A    = data.get("A", [[0.9, 0.0], [0.0, 0.85]])
    mid  = data.get("mutation_id", "api_full")
    result = _fv().verify_full(A, mutation_id=mid)
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/fv/ledger", methods=["GET"])
def fv_ledger():
    limit = int(request.args.get("limit", 50))
    return jsonify({
        "status":  "success",
        "ledger":  _fv().ledger.get_all(limit),
        "intact":  _fv().ledger.verify_integrity(),
    })

@app.route("/fv/stats", methods=["GET"])
def fv_stats():
    return jsonify({"status": "success", "stats": _fv().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 22 — BYZANTINE FEDERATED AGGREGATOR
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/byz/register-site", methods=["POST"])
def byz_register():
    data   = request.get_json() or {}
    result = _byz().register_site(
        site_id  = data.get("site_id", "site_1"),
        metadata = data.get("metadata", {}),
    )
    return jsonify({"status": "success", "result": result})

@app.route("/byz/aggregate", methods=["POST"])
def byz_aggregate():
    """Aggregate gradient updates from multiple sites."""
    from python.sentinel.byzantine_aggregator import SiteUpdate
    data    = request.get_json() or {}
    raw_upd = data.get("updates", [])
    method  = data.get("method", "trimmed_mean")
    updates = [
        SiteUpdate(
            site_id    = u.get("site_id", f"site_{i}"),
            gradient   = u.get("gradient", [0.0] * 8),
            param_dim  = u.get("param_dim", 8),
            step       = int(u.get("step", 0)),
            performance= float(u.get("performance", 0.0)),
            n_samples  = int(u.get("n_samples", 1)),
        )
        for i, u in enumerate(raw_upd)
    ]
    result = _byz().aggregate(updates, method=method)
    return jsonify({"status": "success", "result": result.to_dict()})

@app.route("/byz/export-params", methods=["GET"])
def byz_export():
    return jsonify({"status": "success", "params": _byz().export_global_params()})

@app.route("/byz/leaderboard", methods=["GET"])
def byz_leaderboard():
    return jsonify({"status": "success",
                    "leaderboard": _byz().get_site_leaderboard()})

@app.route("/byz/anomalies", methods=["GET"])
def byz_anomalies():
    limit = int(request.args.get("limit", 50))
    return jsonify({"status": "success",
                    "anomalies": _byz().get_anomaly_log(limit)})

@app.route("/byz/stats", methods=["GET"])
def byz_stats():
    return jsonify({"status": "success", "stats": _byz().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 MODULE 23 — MUTATION GOVERNANCE PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/gov/begin", methods=["POST"])
def gov_begin():
    """Begin a new 11-step mutation pipeline."""
    data   = request.get_json() or {}
    mid    = _mgov().begin_mutation(
        plc_program  = data.get("plc_program",  "PLC_PROG"),
        param_deltas = data.get("param_deltas", {}),
    )
    return jsonify({"status": "success", "mutation_id": mid})

@app.route("/gov/step", methods=["POST"])
def gov_step():
    """Execute a specific pipeline step (1-7, 9-10)."""
    data    = request.get_json() or {}
    mid     = data.get("mutation_id")
    step_id = int(data.get("step_id", 1))
    context = data.get("context", {})
    result  = _mgov().run_step(mid, step_id, context=context)
    return jsonify({"status": "success" if result.get("passed") else "error",
                    "result": result})

@app.route("/gov/approve", methods=["POST"])
def gov_approve():
    """Step 8: Human approval gate — Baron Marco Paolo Ialongo only."""
    data   = request.get_json() or {}
    result = _mgov().approve(
        mutation_id  = data.get("mutation_id"),
        approval_code= data.get("approval_code", ""),
    )
    return jsonify({
        "status": "success" if result.get("approved") else "error",
        "result": result,
    })

@app.route("/gov/release-key", methods=["POST"])
def gov_release_key():
    """Step 11: Release production deployment key."""
    data   = request.get_json() or {}
    result = _mgov().release_production_key(data.get("mutation_id"))
    return jsonify({
        "status": "success" if result.get("deployed") else "error",
        "result": result,
    })

@app.route("/gov/mutation/<mutation_id>", methods=["GET"])
def gov_get_mutation(mutation_id):
    m = _mgov().get_mutation(mutation_id)
    if not m:
        return jsonify({"status": "error", "error": "Not found"}), 404
    return jsonify({"status": "success", "mutation": m})

@app.route("/gov/mutation/<mutation_id>/evidence", methods=["GET"])
def gov_get_evidence(mutation_id):
    ev = _mgov().get_full_evidence(mutation_id)
    if ev is None:
        return jsonify({"status": "error", "error": "Not found"}), 404
    return jsonify({"status": "success", "evidence": ev})

@app.route("/gov/list", methods=["GET"])
def gov_list():
    status = request.args.get("status")
    limit  = int(request.args.get("limit", 50))
    return jsonify({"status": "success",
                    "mutations": _mgov().list_mutations(status, limit)})

@app.route("/gov/stats", methods=["GET"])
def gov_stats():
    return jsonify({"status": "success", "stats": _mgov().get_stats()})


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# v4.1 ENDPOINTS — CIEC Advanced (6 new modules, 28 new endpoints)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_td3():
    if not hasattr(app, "_td3"):
        from .td3_controller import TD3IndustrialController
        app._td3 = TD3IndustrialController(state_dim=256)
    return app._td3

def _get_ast_parser():
    if not hasattr(app, "_ast_parser"):
        from .ast_parser import IEC61131ASTParser
        app._ast_parser = IEC61131ASTParser()
    return app._ast_parser

def _get_ext_physics():
    if not hasattr(app, "_ext_physics"):
        from .extended_physics import ExtendedPhysicsTwin, FaultConfig
        app._ext_physics = ExtendedPhysicsTwin()
        app._FaultConfig = FaultConfig
    return app._ext_physics

def _get_vmware():
    if not hasattr(app, "_vmware"):
        from .vmware_orchestrator import VMwareOrchestrator
        app._vmware = VMwareOrchestrator()
    return app._vmware

def _get_formal():
    if not hasattr(app, "_formal"):
        from .formal_verification import FormalVerificationEngine
        app._formal = FormalVerificationEngine()
    return app._formal

def _get_byzantine():
    if not hasattr(app, "_byzantine"):
        from .byzantine_aggregator import ByzantineFederatedAggregator
        app._byzantine = ByzantineFederatedAggregator(f_tolerance=1)
    return app._byzantine

def _get_governance():
    if not hasattr(app, "_governance"):
        from .mutation_governance import MutationGovernanceEngine
        app._governance = MutationGovernanceEngine()
    return app._governance
# ── Extended Physics Twin ─────────────────────────────────────────────────────

@app.route("/physics/step", methods=["POST"])
def physics_step():
    """POST /physics/step — advance all physics blocks by dt"""
    data = request.get_json() or {}
    try:
        twin   = _get_ext_physics()
        inputs = data.get("inputs", {})
        dt     = float(data.get("dt", 0.1))
        method = data.get("method", "rk4")
        ss     = twin.step(inputs, dt, method)
        return jsonify({
            "status":          "ok",
            "step":            ss.step,
            "t":               round(ss.t, 4),
            "state":           {k: round(v, 6) for k, v in ss.state.items()},
            "hard_violation":  ss.hard_violation,
            "fault_active":    ss.fault_active,
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/physics/episode", methods=["POST"])
def physics_episode():
    """POST /physics/episode — run full episode with optional fault injection"""
    data = request.get_json() or {}
    try:
        twin = _get_ext_physics()
        FaultConfig = app._FaultConfig if hasattr(app, "_FaultConfig") else None
        faults = []
        for fc in data.get("faults", []):
            if FaultConfig:
                faults.append(FaultConfig(
                    category   = fc.get("category", "sensor_bias"),
                    target     = fc.get("target", "Q_in"),
                    magnitude  = float(fc.get("magnitude", 1.0)),
                    onset_step = int(fc.get("onset_step", 0)),
                    duration   = int(fc.get("duration", -1)),
                ))
        result = twin.run_episode(
            n_steps = int(data.get("n_steps", 100)),
            dt      = float(data.get("dt", 0.1)),
            faults  = faults,
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/physics/evaluate-mutation", methods=["POST"])
def physics_evaluate_mutation():
    """POST /physics/evaluate-mutation — Monte Carlo mutation evaluation"""
    data = request.get_json() or {}
    try:
        twin = _get_ext_physics()
        promoted, metrics = twin.evaluate_mutation(
            param_deltas      = data.get("param_deltas", {}),
            n_runs            = int(data.get("n_runs", 5)),
            fault_categories  = data.get("fault_categories"),
        )
        return jsonify({"status": "ok", "promoted": promoted, "metrics": metrics})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/physics/stats", methods=["GET"])
def physics_stats():
    """GET /physics/stats — physics twin statistics"""
    try:
        return jsonify({"status": "ok", "stats": _get_ext_physics().get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── VMware Orchestrator ───────────────────────────────────────────────────────

@app.route("/vmware/vms", methods=["GET"])
def vmware_list():
    """GET /vmware/vms — list all VMs in inventory"""
    try:
        return jsonify({"status": "ok", "vms": _get_vmware().list_vms()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/snapshot", methods=["POST"])
def vmware_snapshot():
    """POST /vmware/snapshot — create VM snapshot"""
    data = request.get_json() or {}
    try:
        result = _get_vmware().create_snapshot(
            data.get("vm_id", "VM-C"),
            data.get("snap_name", "auto_snap"),
            data.get("actor", "kiswarm"),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/clone", methods=["POST"])
def vmware_clone():
    """POST /vmware/clone — clone VM for mutation testing"""
    data = request.get_json() or {}
    try:
        result = _get_vmware().clone_vm(
            data.get("src_vm_id", "VM-C"),
            data.get("clone_name"),
            data.get("isolate_network", True),
            data.get("actor", "kiswarm"),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/mutation/begin", methods=["POST"])
def vmware_mutation_begin():
    """POST /vmware/mutation/begin — start VM mutation cycle"""
    data = request.get_json() or {}
    try:
        mid = _get_vmware().begin_mutation(
            data.get("source_vm", "VM-C"),
            data.get("param_deltas", {}),
            data.get("actor", "kiswarm"),
        )
        return jsonify({"status": "ok", "mutation_id": mid})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/mutation/promote", methods=["POST"])
def vmware_mutation_promote():
    """POST /vmware/mutation/promote — promote mutation to production (requires approval)"""
    data = request.get_json() or {}
    try:
        result = _get_vmware().promote_mutation(
            data.get("mutation_id", ""),
            data.get("approval_code", ""),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/audit", methods=["GET"])
def vmware_audit():
    """GET /vmware/audit — VM operation audit log"""
    try:
        limit = int(request.args.get("limit", 50))
        return jsonify({"status": "ok",
                        "audit": _get_vmware().get_audit_log(limit)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/vmware/stats", methods=["GET"])
def vmware_stats():
    """GET /vmware/stats — VMware orchestrator statistics"""
    try:
        return jsonify({"status": "ok", "stats": _get_vmware().get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Formal Verification ───────────────────────────────────────────────────────

@app.route("/formal/lyapunov", methods=["POST"])
def formal_lyapunov():
    """POST /formal/lyapunov — Lyapunov stability check on system matrix A"""
    data = request.get_json() or {}
    A    = data.get("A")
    if not A:
        return jsonify({"status": "error", "error": "System matrix A required"}), 400
    try:
        result = _get_formal().verify_linearized(
            A           = A,
            mutation_id = data.get("mutation_id", "api_call"),
        )
        return jsonify({"status": "ok", "result": result.to_dict()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/formal/barrier", methods=["POST"])
def formal_barrier():
    """POST /formal/barrier — barrier certificate verification (sampling-based)"""
    data = request.get_json() or {}
    try:
        safe_set = [tuple(pair) for pair in data.get("safe_set", [[-1, 1], [-1, 1]])]
        decay    = float(data.get("decay", 0.1))
        # Simple quadratic barrier: B(x) = 1 - sum(xi^2/ri^2)
        def B(x):
            return 1.0 - sum((x[i] / (safe_set[i][1] or 1.0))**2
                             for i in range(min(len(x), len(safe_set))))
        # Simple stable system: dx/dt = -decay * x
        def f(x):
            return [-decay * xi for xi in x]
        result = _get_formal().verify_barrier(
            B           = B,
            f           = f,
            safe_set    = safe_set,
            n_samples   = int(data.get("n_samples", 200)),
            mutation_id = data.get("mutation_id", "api_call"),
        )
        return jsonify({"status": "ok", "result": result.to_dict()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/formal/ledger", methods=["GET"])
def formal_ledger():
    """GET /formal/ledger — mutation verification ledger"""
    try:
        limit = int(request.args.get("limit", 50))
        engine = _get_formal()
        return jsonify({
            "status":  "ok",
            "entries": engine.ledger.get_all(limit),
            "intact":  engine.ledger.verify_integrity(),
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/formal/stats", methods=["GET"])
def formal_stats():
    """GET /formal/stats — formal verification statistics"""
    try:
        return jsonify({"status": "ok", "stats": _get_formal().get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Byzantine Federated Aggregator ────────────────────────────────────────────

@app.route("/federated/register", methods=["POST"])
def federated_register():
    """POST /federated/register — register a site in the federated mesh"""
    data = request.get_json() or {}
    try:
        result = _get_byzantine().register_site(
            data.get("site_id", f"site_{int(time.time())}"),
            data.get("metadata", {}),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/federated/aggregate", methods=["POST"])
def federated_aggregate():
    """POST /federated/aggregate — Byzantine-tolerant gradient aggregation"""
    data = request.get_json() or {}
    try:
        from .byzantine_aggregator import SiteUpdate
        updates = []
        for u in data.get("updates", []):
            updates.append(SiteUpdate(
                site_id     = u.get("site_id", "unknown"),
                gradient    = u.get("gradient", []),
                param_dim   = len(u.get("gradient", [])),
                step        = int(u.get("step", 0)),
                performance = float(u.get("performance", 0.0)),
                n_samples   = int(u.get("n_samples", 1)),
            ))
        result = _get_byzantine().aggregate(updates, data.get("method"))
        return jsonify({"status": "ok", "result": result.to_dict()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/federated/params", methods=["GET"])
def federated_params():
    """GET /federated/params — export current global parameters"""
    try:
        return jsonify({"status": "ok",
                        "params": _get_byzantine().export_global_params()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/federated/anomalies", methods=["GET"])
def federated_anomalies():
    """GET /federated/anomalies — Byzantine anomaly log"""
    try:
        limit = int(request.args.get("limit", 50))
        return jsonify({"status": "ok",
                        "anomalies": _get_byzantine().get_anomaly_log(limit)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/federated/leaderboard", methods=["GET"])
def federated_leaderboard():
    """GET /federated/leaderboard — site trust score leaderboard"""
    try:
        return jsonify({"status": "ok",
                        "leaderboard": _get_byzantine().get_site_leaderboard()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/federated/stats", methods=["GET"])
def federated_stats():
    """GET /federated/stats — federated aggregator statistics"""
    try:
        return jsonify({"status": "ok", "stats": _get_byzantine().get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Mutation Governance Pipeline ──────────────────────────────────────────────

@app.route("/governance/begin", methods=["POST"])
def governance_begin():
    """POST /governance/begin — start mutation governance pipeline"""
    data = request.get_json() or {}
    try:
        mid = _get_governance().begin_mutation(
            plc_program  = data.get("plc_program", "UNKNOWN"),
            param_deltas = data.get("param_deltas", {}),
        )
        return jsonify({"status": "ok", "mutation_id": mid,
                        "next_step": 1, "total_steps": 11})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/step", methods=["POST"])
def governance_step():
    """POST /governance/step — execute next pipeline step"""
    data = request.get_json() or {}
    try:
        result = _get_governance().run_step(
            mutation_id = data.get("mutation_id", ""),
            step_id     = int(data.get("step_id", 1)),
            context     = data.get("context", {}),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/approve", methods=["POST"])
def governance_approve():
    """POST /governance/approve — Step 8 human approval gate (Baron Marco Paolo Ialongo only)"""
    data = request.get_json() or {}
    try:
        result = _get_governance().approve(
            mutation_id   = data.get("mutation_id", ""),
            approval_code = data.get("approval_code", ""),
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/release", methods=["POST"])
def governance_release():
    """POST /governance/release — Step 11 production key release"""
    data = request.get_json() or {}
    try:
        result = _get_governance().release_production_key(
            data.get("mutation_id", "")
        )
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/mutation/<mutation_id>", methods=["GET"])
def governance_mutation(mutation_id):
    """GET /governance/mutation/<id> — get mutation pipeline record"""
    try:
        m = _get_governance().get_mutation(mutation_id)
        if not m:
            return jsonify({"status": "error", "error": "Not found"}), 404
        return jsonify({"status": "ok", "mutation": m})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/list", methods=["GET"])
def governance_list():
    """GET /governance/list — list mutations with optional status filter"""
    try:
        status = request.args.get("status")
        limit  = int(request.args.get("limit", 50))
        return jsonify({"status": "ok",
                        "mutations": _get_governance().list_mutations(status, limit)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/governance/stats", methods=["GET"])
def governance_stats():
    """GET /governance/stats — governance engine statistics"""
    try:
        return jsonify({"status": "ok", "stats": _get_governance().get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT TIME HELPER
# ─────────────────────────────────────────────────────────────────────────────
import time as _time_mod
time = _time_mod


# ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(_):
    return jsonify({
        "status":  "error",
        "error":   "Endpoint not found",
        "version": "4.1",
        "modules": {
            "v2.1 Sentinel Intelligence (6 modules, 17 endpoints)": [
                "POST /sentinel/extract", "POST /sentinel/debate",
                "GET  /sentinel/search",  "GET  /sentinel/status",
                "POST /firewall/scan",
                "GET  /decay/scan", "GET  /decay/record/<hash_id>", "POST /decay/revalidate",
                "GET  /ledger/status", "GET  /ledger/verify", "GET  /ledger/proof/<hash_id>",
                "POST /conflict/analyze", "POST /conflict/quick",
                "GET  /tracker/leaderboard", "GET  /tracker/model/<n>",
                "POST /tracker/validate", "POST /guard/assess",
            ],
            "v3.0 Industrial AI (4 modules, 13 endpoints)": [
                "POST /fuzzy/classify",  "POST /fuzzy/update",
                "POST /fuzzy/tune",      "GET  /fuzzy/stats",
                "POST /rl/act",          "POST /rl/learn",   "GET  /rl/stats",
                "POST /twin/evaluate",   "GET  /twin/stats",
                "POST /mesh/share",      "POST /mesh/register",
                "GET  /mesh/leaderboard","GET  /mesh/stats",
            ],
            "v4.0 CIEC Cognitive Industrial Core (6 modules, 28 endpoints)": [
                "POST /plc/parse",             "GET  /plc/stats",
                "POST /scada/push",            "POST /scada/ingest-history",
                "GET  /scada/state",           "GET  /scada/anomalies",
                "GET  /scada/stats",
                "POST /ciec-twin/run",         "POST /ciec-twin/evaluate",
                "GET  /ciec-twin/stats",
                "POST /constraints/validate",  "POST /constraints/check-state",
                "GET  /constraints/list",      "GET  /constraints/violations",
                "GET  /constraints/stats",
                "POST /kg/add-pid",            "POST /kg/add-failure",
                "POST /kg/find-similar",       "POST /kg/find-by-symptoms",
                "GET  /kg/recurring-patterns", "GET  /kg/export-bundle",
                "POST /kg/import-bundle",      "GET  /kg/nodes",
                "GET  /kg/stats",
                "POST /ciec-rl/act",           "POST /ciec-rl/observe",
                "POST /ciec-rl/update",        "GET  /ciec-rl/stats",
            ],
            "v4.1 Advanced CIEC (7 modules, 28 endpoints)": [
                "POST /td3/act",               "POST /td3/observe",
                "POST /td3/update",            "POST /td3/reward",
                "GET  /td3/stats",
                "POST /ast/parse",             "POST /ast/patterns",
                "POST /ast/graphs",            "GET  /ast/stats",
                "POST /physics/step",          "POST /physics/episode",
                "POST /physics/evaluate-mutation","GET  /physics/stats",
                "GET  /vmware/vms",            "POST /vmware/snapshot",
                "POST /vmware/clone",          "POST /vmware/mutation/begin",
                "POST /vmware/mutation/promote","GET  /vmware/audit",
                "GET  /vmware/stats",
                "POST /formal/lyapunov",       "POST /formal/barrier",
                "GET  /formal/ledger",         "GET  /formal/stats",
                "POST /federated/register",    "POST /federated/aggregate",
                "GET  /federated/params",      "GET  /federated/anomalies",
                "GET  /federated/leaderboard", "GET  /federated/stats",
                "POST /governance/begin",      "POST /governance/step",
                "POST /governance/approve",    "POST /governance/release",
                "GET  /governance/mutation/<id>","GET  /governance/list",
                "GET  /governance/stats",
            ],
        },
        "total_endpoints": 87,
        "system_health":   "GET /health",
    }), 404


# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# v4.2 — MODULE 24: EXPLAINABILITY ENGINE (XAI)
# ═══════════════════════════════════════════════════════════════════════════════

_xai_engine = None

def _get_xai():
    global _xai_engine
    if _xai_engine is None:
        from python.sentinel.explainability_engine import ExplainabilityEngine
        _xai_engine = ExplainabilityEngine()
    return _xai_engine


@app.route("/xai/explain-td3", methods=["POST"])
def xai_explain_td3():
    data = request.get_json() or {}
    state = data.get("state", [0.5] * 8)
    feature_names = data.get("feature_names")
    try:
        eng = _get_xai()
        exp = eng.explain_td3(
            state=state,
            model_fn=lambda x: sum(v * (0.1 * (i + 1)) for i, v in enumerate(x)),
            feature_names=feature_names,
        )
        return jsonify({"ok": True, "explanation": exp.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/xai/explain-formal", methods=["POST"])
def xai_explain_formal():
    data = request.get_json() or {}
    lyapunov_result = data.get("lyapunov_result", {
        "stable": True, "spectral_radius": 0.5,
        "lyapunov_margin": 0.4, "P_positive_def": 1, "converged": 1,
    })
    try:
        exp = _get_xai().explain_formal(lyapunov_result, mutation_id=data.get("mutation_id"))
        return jsonify({"ok": True, "explanation": exp.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/xai/explain-governance", methods=["POST"])
def xai_explain_governance():
    data = request.get_json() or {}
    evidence = data.get("evidence_chain", [
        {"step_name": "twin_sim", "passed": True},
        {"step_name": "formal",   "passed": True},
    ])
    try:
        exp = _get_xai().explain_governance(evidence, mutation_id=data.get("mutation_id"))
        return jsonify({"ok": True, "explanation": exp.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/xai/explain", methods=["POST"])
def xai_explain_generic():
    data = request.get_json() or {}
    state = data.get("state", [])
    feature_names = data.get("feature_names")
    decision_type = data.get("decision_type", "generic")
    if not state:
        return jsonify({"error": "state required"}), 400
    try:
        exp = _get_xai().explain(
            state=state,
            model_fn=lambda x: sum(v * (0.1 * (i + 1)) for i, v in enumerate(x)),
            feature_names=feature_names,
            decision_type=decision_type,
        )
        return jsonify({"ok": True, "explanation": exp.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/xai/ledger", methods=["GET"])
def xai_ledger():
    limit = int(request.args.get("limit", 20))
    eng = _get_xai()
    return jsonify({
        "ok": True,
        "entries": eng.ledger.get_all(limit=limit),
        "ledger_intact": eng.ledger.verify_integrity(),
        "total": len(eng.ledger),
    }), 200


@app.route("/xai/stats", methods=["GET"])
def xai_stats():
    return jsonify({"ok": True, "stats": _get_xai().get_stats()}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# v4.2 — MODULE 25: PREDICTIVE MAINTENANCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

_pdm_engine = None

def _get_pdm():
    global _pdm_engine
    if _pdm_engine is None:
        from python.sentinel.predictive_maintenance import PredictiveMaintenanceEngine
        _pdm_engine = PredictiveMaintenanceEngine()
    return _pdm_engine


@app.route("/pdm/register", methods=["POST"])
def pdm_register():
    data = request.get_json() or {}
    asset_id    = data.get("asset_id")
    asset_class = data.get("asset_class", "pump")
    if not asset_id:
        return jsonify({"error": "asset_id required"}), 400
    result = _get_pdm().register_asset(
        asset_id, asset_class,
        install_hour=data.get("install_hour", 0.0),
        metadata=data.get("metadata"),
    )
    return jsonify({"ok": True, **result}), 201


@app.route("/pdm/ingest", methods=["POST"])
def pdm_ingest():
    from python.sentinel.predictive_maintenance import SensorReading
    data = request.get_json() or {}
    asset_id = data.get("asset_id", "unknown")
    try:
        reading = SensorReading(
            asset_id     = asset_id,
            timestamp    = data.get("timestamp", ""),
            hour         = float(data.get("hour", 0)),
            temperature  = float(data.get("temperature", 60)),
            vibration    = float(data.get("vibration", 2)),
            current_draw = float(data.get("current_draw", 50)),
            pressure_drop= float(data.get("pressure_drop", 1)),
            efficiency   = float(data.get("efficiency", 0.85)),
        )
        hi = _get_pdm().ingest_reading(reading)
        return jsonify({"ok": True, "health_index": hi.hi, "alarm_level": hi.alarm_level,
                        "anomaly_score": hi.anomaly_score,
                        "component_scores": hi.component_scores}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/pdm/rul/<asset_id>", methods=["GET"])
def pdm_rul(asset_id):
    try:
        rul = _get_pdm().predict_rul(asset_id, n_monte_carlo=int(request.args.get("n_mc", 100)))
        return jsonify({"ok": True, "rul": rul.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/pdm/schedule", methods=["GET"])
def pdm_schedule():
    result = _get_pdm().schedule_maintenance()
    return jsonify({"ok": True, "schedule": result}), 200


@app.route("/pdm/maintenance", methods=["POST"])
def pdm_record_maintenance():
    data = request.get_json() or {}
    asset_id = data.get("asset_id")
    if not asset_id:
        return jsonify({"error": "asset_id required"}), 400
    try:
        result = _get_pdm().record_maintenance(
            asset_id,
            event_type  = data.get("event_type", "inspection"),
            cost_eur    = float(data.get("cost_eur", 0)),
            technician  = data.get("technician", "unknown"),
            notes       = data.get("notes", ""),
        )
        return jsonify({"ok": True, **result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/pdm/fleet", methods=["GET"])
def pdm_fleet():
    return jsonify({"ok": True, "fleet": _get_pdm().fleet_overview()}), 200


@app.route("/pdm/stats", methods=["GET"])
def pdm_stats():
    return jsonify({"ok": True, "stats": _get_pdm().get_stats()}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# v4.2 — MODULE 26: MULTI-AGENT PLANT COORDINATOR
# ═══════════════════════════════════════════════════════════════════════════════

_coordinator = None

def _get_coordinator():
    global _coordinator
    if _coordinator is None:
        from python.sentinel.multiagent_coordinator import MultiAgentPlantCoordinator
        _coordinator = MultiAgentPlantCoordinator()
    return _coordinator


@app.route("/coordinator/sections", methods=["GET"])
def coordinator_sections():
    coord = _get_coordinator()
    return jsonify({"ok": True, "sections": list(coord.sections.keys()),
                    "n_agents": len(coord.agents)}), 200


@app.route("/coordinator/add-section", methods=["POST"])
def coordinator_add_section():
    data = request.get_json() or {}
    sid  = data.get("section_id")
    if not sid:
        return jsonify({"error": "section_id required"}), 400
    result = _get_coordinator().add_section(sid, data.get("config", {}))
    return jsonify({"ok": True, **result}), 201


@app.route("/coordinator/step", methods=["POST"])
def coordinator_step():
    data   = request.get_json() or {}
    states = data.get("states", {})
    health = data.get("health_indices", {})
    noise  = float(data.get("noise", 0.02))
    try:
        result = _get_coordinator().step(states, health_indices=health, noise=noise)
        return jsonify({"ok": True, "consensus": result.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/coordinator/rewards", methods=["POST"])
def coordinator_rewards():
    data = request.get_json() or {}
    local_rewards = data.get("local_rewards", {})
    round_id      = data.get("round_id", 0)
    coord = _get_coordinator()
    if not coord._round_log:
        return jsonify({"error": "No consensus round completed yet"}), 400
    last = coord._round_log[-1]
    shaped = coord.distribute_rewards(local_rewards, last)
    return jsonify({"ok": True, "shaped_rewards": shaped}), 200


@app.route("/coordinator/history", methods=["GET"])
def coordinator_history():
    limit = int(request.args.get("limit", 20))
    return jsonify({"ok": True, "history": _get_coordinator().get_round_history(limit)}), 200


@app.route("/coordinator/agents", methods=["GET"])
def coordinator_agents():
    return jsonify({"ok": True, "agents": _get_coordinator().get_agent_stats()}), 200


@app.route("/coordinator/stats", methods=["GET"])
def coordinator_stats():
    return jsonify({"ok": True, "stats": _get_coordinator().get_stats()}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# v4.2 — MODULE 27: IEC 61508 SIL VERIFICATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

_sil_engine = None

def _get_sil():
    global _sil_engine
    if _sil_engine is None:
        from python.sentinel.sil_verification import SILVerificationEngine
        _sil_engine = SILVerificationEngine()
    return _sil_engine


@app.route("/sil/assess", methods=["POST"])
def sil_assess():
    from python.sentinel.sil_verification import Subsystem
    data = request.get_json() or {}
    sif_id       = data.get("sif_id", "SIF_001")
    sil_required = int(data.get("sil_required", 2))
    subsystems_raw = data.get("subsystems", [])
    try:
        subsystems = []
        for s in subsystems_raw:
            subsystems.append(Subsystem(
                subsystem_id              = s.get("subsystem_id", "SUB"),
                subsystem_type            = s.get("subsystem_type", "sensor"),
                architecture              = s.get("architecture", "1oo2"),
                lambda_d                  = float(s.get("lambda_d", 1e-6)),
                lambda_s                  = float(s.get("lambda_s", 2e-6)),
                mttf_hours                = float(s.get("mttf_hours", 100000)),
                mttr_hours                = float(s.get("mttr_hours", 8)),
                proof_test_interval_hours = float(s.get("proof_test_interval_hours", 8760)),
                beta                      = float(s.get("beta", 0.05)),
                dc                        = float(s.get("dc", 0.90)),
                hw_fault_tolerance        = int(s.get("hw_fault_tolerance", 1)),
            ))
        if not subsystems:
            # Default demo subsystem
            subsystems = [Subsystem(
                subsystem_id="sensor_1oo2", subsystem_type="sensor",
                architecture="1oo2", lambda_d=1e-6, lambda_s=2e-6,
                mttf_hours=100000, mttr_hours=8,
                proof_test_interval_hours=8760, beta=0.05, dc=0.90,
                hw_fault_tolerance=1,
            )]
        assessment = _get_sil().assess_sif(sif_id, subsystems, sil_required)
        return jsonify({"ok": True, "assessment": assessment.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sil/mutation-impact", methods=["POST"])
def sil_mutation_impact():
    data = request.get_json() or {}
    mutation_id  = data.get("mutation_id", "MUT_001")
    sif_id       = data.get("sif_id", "SIF_001")
    param_deltas = data.get("param_deltas", {"delta_kp": 0.02})
    sil_required = int(data.get("sil_required", 2))
    try:
        impact = _get_sil().assess_mutation_impact(mutation_id, sif_id, param_deltas, sil_required)
        return jsonify({"ok": True, "impact": impact.to_dict()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sil/assessment/<sif_id>", methods=["GET"])
def sil_get_assessment(sif_id):
    result = _get_sil().get_assessment(sif_id)
    if result is None:
        return jsonify({"error": f"SIF {sif_id!r} not found"}), 404
    return jsonify({"ok": True, "assessment": result}), 200


@app.route("/sil/impact-log", methods=["GET"])
def sil_impact_log():
    limit = int(request.args.get("limit", 20))
    return jsonify({"ok": True, "impacts": _get_sil().get_impact_log(limit)}), 200


@app.route("/sil/stats", methods=["GET"])
def sil_stats():
    return jsonify({"ok": True, "stats": _get_sil().get_stats()}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# v4.2 — MODULE 28: DIGITAL THREAD TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

_thread_tracker = None

def _get_thread():
    global _thread_tracker
    if _thread_tracker is None:
        from python.sentinel.digital_thread import DigitalThreadTracker
        _thread_tracker = DigitalThreadTracker()
    return _thread_tracker


@app.route("/thread/node", methods=["POST"])
def thread_add_node():
    data = request.get_json() or {}
    node_type = data.get("node_type")
    title     = data.get("title", "")
    if not node_type:
        return jsonify({"error": "node_type required"}), 400
    try:
        node = _get_thread().add_node(
            node_type = node_type,
            title     = title,
            payload   = data.get("payload", {}),
            author    = data.get("author", "kiswarm"),
            version   = data.get("version", "1.0"),
            tags      = data.get("tags", []),
            node_id   = data.get("node_id"),
        )
        return jsonify({"ok": True, "node": node.to_dict()}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/thread/edge", methods=["POST"])
def thread_add_edge():
    data = request.get_json() or {}
    source_id = data.get("source_id")
    target_id = data.get("target_id")
    edge_type = data.get("edge_type")
    if not all([source_id, target_id, edge_type]):
        return jsonify({"error": "source_id, target_id, edge_type required"}), 400
    try:
        edge = _get_thread().add_edge(source_id, target_id, edge_type,
                                       annotation=data.get("annotation", ""))
        return jsonify({"ok": True, "edge": edge.to_dict()}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/thread/node/<node_id>", methods=["GET"])
def thread_get_node(node_id):
    result = _get_thread().get_node(node_id)
    if result is None:
        return jsonify({"error": f"Node {node_id!r} not found"}), 404
    return jsonify({"ok": True, "node": result}), 200


@app.route("/thread/ancestors/<node_id>", methods=["GET"])
def thread_ancestors(node_id):
    depth = int(request.args.get("max_depth", 20))
    result = _get_thread().ancestors(node_id, max_depth=depth)
    return jsonify({"ok": True, "ancestors": result, "count": len(result)}), 200


@app.route("/thread/descendants/<node_id>", methods=["GET"])
def thread_descendants(node_id):
    depth = int(request.args.get("max_depth", 20))
    result = _get_thread().descendants(node_id, max_depth=depth)
    return jsonify({"ok": True, "descendants": result, "count": len(result)}), 200


@app.route("/thread/lineage/<node_id>", methods=["GET"])
def thread_mutation_lineage(node_id):
    result = _get_thread().mutation_lineage(node_id)
    return jsonify({"ok": True, "lineage": result}), 200


@app.route("/thread/compliance", methods=["POST"])
def thread_compliance():
    data     = request.get_json() or {}
    standard = data.get("standard", "iec_61508")
    scope    = data.get("scope_node_ids")
    result   = _get_thread().check_compliance(standard, scope)
    return jsonify({"ok": True, "compliance": result}), 200


@app.route("/thread/find", methods=["GET"])
def thread_find():
    node_type = request.args.get("node_type")
    tag       = request.args.get("tag")
    author    = request.args.get("author")
    limit     = int(request.args.get("limit", 50))
    results   = _get_thread().find_nodes(node_type=node_type, tag=tag,
                                          author=author, limit=limit)
    return jsonify({"ok": True, "nodes": results, "count": len(results)}), 200


@app.route("/thread/stats", methods=["GET"])
def thread_stats():
    return jsonify({"ok": True, "stats": _get_thread().get_stats()}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# 404 CATCH-ALL
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "error":   "Endpoint not found",
        "version": "4.2",
        "total_endpoints": 133,
        "v4.2_new": [
            "POST /xai/explain-td3", "POST /xai/explain-formal",
            "POST /xai/explain-governance", "POST /xai/explain",
            "GET  /xai/ledger", "GET  /xai/stats",
            "POST /pdm/register", "POST /pdm/ingest",
            "GET  /pdm/rul/<id>", "GET  /pdm/schedule",
            "POST /pdm/maintenance", "GET  /pdm/fleet", "GET  /pdm/stats",
            "GET  /coordinator/sections", "POST /coordinator/add-section",
            "POST /coordinator/step", "POST /coordinator/rewards",
            "GET  /coordinator/history", "GET  /coordinator/agents",
            "GET  /coordinator/stats",
            "POST /sil/assess", "POST /sil/mutation-impact",
            "GET  /sil/assessment/<id>", "GET  /sil/impact-log",
            "GET  /sil/stats",
            "POST /thread/node", "POST /thread/edge",
            "GET  /thread/node/<id>", "GET  /thread/ancestors/<id>",
            "GET  /thread/descendants/<id>", "GET  /thread/lineage/<id>",
            "POST /thread/compliance", "GET  /thread/find",
            "GET  /thread/stats",
        ],
    }), 404


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# v4.3 — ICS CYBERSECURITY ENGINE + OT NETWORK MONITOR (Modules 29-30)
# ═══════════════════════════════════════════════════════════════════════════════

from .ics_security import ICSSecurityEngine
from .ot_network_monitor import OTNetworkMonitor

_security = ICSSecurityEngine()
_ot_monitor = OTNetworkMonitor()


# ── Module 29: ICS Security Engine ───────────────────────────────────────────

@app.route("/security/scan-plc", methods=["POST"])
def security_scan_plc():
    d = request.get_json() or {}
    source = d.get("source", "")
    program_name = d.get("program_name", "unknown")
    asset_id = d.get("asset_id", "unknown")
    if not source:
        return jsonify({"error": "source required"}), 400
    return jsonify(_security.scan_plc(source, program_name, asset_id))


@app.route("/security/network-event", methods=["POST"])
def security_network_event():
    d = request.get_json() or {}
    result = _security.ingest_network_event(
        asset_id=d.get("asset_id", "unknown"),
        protocol=d.get("protocol", "unknown"),
        command=d.get("command", "read"),
        src_ip=d.get("src_ip", "0.0.0.0"),
        rate_hz=float(d.get("rate_hz", 1.0)),
    )
    return jsonify(result)


@app.route("/security/posture", methods=["GET"])
def security_posture():
    return jsonify(_security.get_posture())


@app.route("/security/iec62443-assess", methods=["POST"])
def security_iec62443_assess():
    d = request.get_json() or {}
    asset_id = d.get("asset_id", "unknown")
    target_sl = int(d.get("target_sl", 2))
    controls = d.get("controls_present", [])
    scada_cfg = d.get("scada_config", None)
    return jsonify(_security.iec62443_assess(asset_id, target_sl, controls, scada_cfg))


@app.route("/security/incidents", methods=["GET"])
def security_incidents():
    limit = int(request.args.get("limit", 50))
    return jsonify({"incidents": _security.get_incidents(limit)})


@app.route("/security/cve-lookup", methods=["GET"])
def security_cve_lookup():
    protocol = request.args.get("protocol", "generic")
    return jsonify({"cves": _security.cve_lookup(protocol)})


@app.route("/security/scada-config-check", methods=["POST"])
def security_scada_config_check():
    d = request.get_json() or {}
    asset_id = d.get("asset_id", "unknown")
    config = d.get("config", {})
    return jsonify(_security.assess_scada_config(asset_id, config))


@app.route("/security/stats", methods=["GET"])
def security_stats():
    return jsonify(_security.get_stats())


@app.route("/security/ledger", methods=["GET"])
def security_ledger():
    limit = int(request.args.get("limit", 50))
    return jsonify(_security.get_ledger(limit))


# ── Module 30: OT Network Monitor ────────────────────────────────────────────

@app.route("/ot-monitor/segment", methods=["POST"])
def ot_register_segment():
    d = request.get_json() or {}
    seg_id = d.get("segment_id")
    if not seg_id:
        return jsonify({"error": "segment_id required"}), 400
    result = _ot_monitor.register_segment(
        segment_id=seg_id,
        subnet=d.get("subnet", "0.0.0.0/0"),
        protocols=d.get("protocols", []),
        permitted_hours=d.get("permitted_hours"),
    )
    return jsonify(result), 201


@app.route("/ot-monitor/packet", methods=["POST"])
def ot_ingest_packet():
    d = request.get_json() or {}
    alerts = _ot_monitor.ingest_packet(
        segment_id=d.get("segment_id", "default"),
        protocol=d.get("protocol", "unknown"),
        function_code=int(d.get("function_code", 0)),
        src=d.get("src", "0.0.0.0"),
        dst=d.get("dst", "0.0.0.0"),
        payload_bytes=int(d.get("payload_bytes", 0)),
        rate_hz=float(d.get("rate_hz", 1.0)),
    )
    return jsonify({"alerts_raised": len(alerts), "alerts": [a.to_dict() for a in alerts]})


@app.route("/ot-monitor/alerts", methods=["GET"])
def ot_get_alerts():
    seg_id = request.args.get("segment_id")
    limit = int(request.args.get("limit", 50))
    return jsonify({"alerts": _ot_monitor.get_alerts(seg_id, limit)})


@app.route("/ot-monitor/baseline/<segment_id>", methods=["GET"])
def ot_get_baseline(segment_id):
    return jsonify(_ot_monitor.get_baseline(segment_id))


@app.route("/ot-monitor/segments", methods=["GET"])
def ot_get_segments():
    return jsonify({"segments": _ot_monitor.get_segments()})


@app.route("/ot-monitor/stats", methods=["GET"])
def ot_get_stats():
    return jsonify(_ot_monitor.get_stats())


if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  KISWARM v4.3 — ICS Cybersecurity + OT Network Monitor     ║")
    logger.info("║  Port: 11436  |  Modules: 30  |  Endpoints: 148           ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    app.run(host="127.0.0.1", port=11436, debug=False, threaded=True)


# ═══════════════════════════════════════════════════════════════════════════════
# v4.4 — SELF-HEALING SWARM AUDITOR (Modules 31-32)
# ═══════════════════════════════════════════════════════════════════════════════

from .swarm_auditor import (
    populate_dummy_data as _populate_dummy,
    run_audit_cycle as _run_audit_cycle,
    log_audit as _log_audit,
    PIPELINES as _PIPELINES,
)
from .swarm_dag import SwarmCoordinator as _SwarmCoordinator

# Initialise shared state
_populate_dummy()
_swarm = _SwarmCoordinator(n_nodes=3, interval_seconds=30)


# ── Module 31: Auditor Core ────────────────────────────────────────────────

@app.route("/auditor/run", methods=["POST"])
def auditor_run():
    """Trigger one full audit cycle across all 6 pipelines."""
    result = _run_audit_cycle(source="api")
    return jsonify({
        "cycle_timestamp": result["cycle_timestamp"],
        "pipelines":       list(result["pipelines"].keys()),
        "issues_found":    result["issues_found"],
        "source":          result["source"],
    })


@app.route("/auditor/logs", methods=["GET"])
def auditor_logs():
    """Return last N audit log entries (append-only ledger)."""
    from .swarm_auditor import _ledger
    limit = int(request.args.get("limit", 100))
    return jsonify({"entries": _ledger.tail(limit), "total": _ledger.entry_count()})


@app.route("/auditor/ledger-integrity", methods=["GET"])
def auditor_ledger_integrity():
    """Verify SHA-256 chain integrity of the audit ledger."""
    from .swarm_auditor import _ledger
    intact, checked = _ledger.verify_integrity()
    return jsonify({"intact": intact, "entries_checked": checked})


@app.route("/auditor/pipeline/<pipeline>", methods=["GET"])
def auditor_pipeline_status(pipeline):
    """Load and return current DAG state for one pipeline."""
    from .swarm_auditor import load_pipeline_dag
    if pipeline not in _PIPELINES:
        return jsonify({"error": f"Unknown pipeline. Valid: {_PIPELINES}"}), 400
    dag = load_pipeline_dag(pipeline)
    return jsonify(dag.to_dict())


@app.route("/auditor/pipeline/<pipeline>/reset", methods=["POST"])
def auditor_pipeline_reset(pipeline):
    """Reset a pipeline DAG to empty and re-populate with dummy data."""
    from .swarm_auditor import PipelineDAG, save_pipeline_dag, populate_dummy_data
    if pipeline not in _PIPELINES:
        return jsonify({"error": f"Unknown pipeline. Valid: {_PIPELINES}"}), 400
    save_pipeline_dag(PipelineDAG(pipeline=pipeline))
    populate_dummy_data()
    _log_audit(f"Pipeline '{pipeline}' reset via API", "INFO", "api")
    return jsonify({"status": f"{pipeline} reset and dummy data repopulated"})


@app.route("/auditor/pipeline/<pipeline>/add-node", methods=["POST"])
def auditor_add_node(pipeline):
    """Add a node to a pipeline DAG."""
    from .swarm_auditor import load_pipeline_dag, save_pipeline_dag, DAGNode
    import uuid as _uuid
    if pipeline not in _PIPELINES:
        return jsonify({"error": f"Unknown pipeline"}), 400
    d = request.get_json() or {}
    node = DAGNode(id=str(_uuid.uuid4()), node_type=d.get("node_type", "generic"),
                   data=d.get("data", {}))
    dag = load_pipeline_dag(pipeline)
    dag.nodes.append(node)
    save_pipeline_dag(dag)
    _log_audit(f"Node {node.id[:8]} added to '{pipeline}'", "INFO", "api")
    return jsonify(node.to_dict()), 201


@app.route("/auditor/pipeline/<pipeline>/add-edge", methods=["POST"])
def auditor_add_edge(pipeline):
    """Add an edge to a pipeline DAG."""
    from .swarm_auditor import load_pipeline_dag, save_pipeline_dag, DAGEdge
    if pipeline not in _PIPELINES:
        return jsonify({"error": f"Unknown pipeline"}), 400
    d = request.get_json() or {}
    from_n = d.get("from_node")
    to_n   = d.get("to_node")
    if not from_n or not to_n:
        return jsonify({"error": "from_node and to_node required"}), 400
    edge = DAGEdge(from_node=from_n, to_node=to_n, edge_type=d.get("edge_type", "derived_from"))
    dag = load_pipeline_dag(pipeline)
    dag.edges.append(edge)
    save_pipeline_dag(dag)
    _log_audit(f"Edge {from_n[:8]}→{to_n[:8]} added to '{pipeline}'", "INFO", "api")
    return jsonify(edge.to_dict()), 201


@app.route("/auditor/populate-dummy", methods=["POST"])
def auditor_populate_dummy():
    """Re-populate all pipelines with representative dummy data."""
    from .swarm_auditor import populate_dummy_data
    populate_dummy_data()
    return jsonify({"status": "dummy data populated", "pipelines": _PIPELINES})


@app.route("/auditor/stats", methods=["GET"])
def auditor_stats():
    """Aggregated auditor statistics."""
    from .swarm_auditor import _ledger
    intact, checked = _ledger.verify_integrity()
    return jsonify({
        "pipelines":        _PIPELINES,
        "pipeline_count":   len(_PIPELINES),
        "ledger_entries":   _ledger.entry_count(),
        "ledger_intact":    intact,
        "ledger_checked":   checked,
    })


# ── Module 32: Swarm DAG Coordinator ──────────────────────────────────────

@app.route("/swarm/start", methods=["POST"])
def swarm_start():
    """Start all swarm nodes + permanent auditor."""
    result = _swarm.start()
    return jsonify(result)


@app.route("/swarm/stop", methods=["POST"])
def swarm_stop():
    """Stop all swarm nodes + permanent auditor."""
    result = _swarm.stop()
    return jsonify(result)


@app.route("/swarm/status", methods=["GET"])
def swarm_status():
    """Status of every swarm node and the permanent auditor."""
    return jsonify(_swarm.status())


@app.route("/swarm/force-cycle", methods=["POST"])
def swarm_force_cycle():
    """Synchronously force one full audit cycle on all nodes."""
    return jsonify(_swarm.force_cycle())


@app.route("/swarm/consensus", methods=["GET"])
def swarm_consensus():
    """Current per-pipeline consensus view (hash votes + quorum)."""
    return jsonify(_swarm.consensus_view())


@app.route("/swarm/node/<node_id>", methods=["GET"])
def swarm_node_status(node_id):
    """Detailed status for a single swarm node."""
    st = _swarm.node_status(node_id)
    if st is None:
        return jsonify({"error": "Node not found"}), 404
    return jsonify(st)


@app.route("/swarm/stats", methods=["GET"])
def swarm_aggregate_stats():
    """Aggregate stats across all nodes (cycles, heals, errors)."""
    return jsonify(_swarm.aggregate_stats())


@app.route("/swarm/immortality/verify", methods=["GET"])
def swarm_immortality_verify():
    """Verify the immortality pipeline DAG + ledger chain integrity."""
    from .swarm_auditor import load_pipeline_dag, _ledger
    dag   = load_pipeline_dag("immortality")
    intact, checked = _ledger.verify_integrity()
    return jsonify({
        "dag_nodes":      len(dag.nodes),
        "dag_edges":      len(dag.edges),
        "ledger_intact":  intact,
        "ledger_entries": checked,
        "immortal":       intact and len(dag.nodes) > 0,
    })


@app.route("/swarm/immortality/start", methods=["POST"])
def immortality_start():
    result = _swarm.start()
    return jsonify({**result, "mode": "immortality"})


@app.route("/swarm/immortality/stop", methods=["POST"])
def immortality_stop():
    result = _swarm.stop()
    return jsonify({**result, "mode": "immortality"})


@app.route("/swarm/immortality/status", methods=["GET"])
def immortality_status():
    s = _swarm.status()
    return jsonify({n["node_id"]: n["running"] for n in s["nodes"]})


@app.route("/swarm/immortality/force-cycle", methods=["POST"])
def immortality_force_cycle():
    return jsonify(_swarm.force_cycle())


# ═══════════════════════════════════════════════════════════════════════════════
# v4.5 — SWARM IMMORTALITY KERNEL (Module 33 + 33a + 33b)
# ═══════════════════════════════════════════════════════════════════════════════

from .swarm_immortality_kernel import get_immortality_kernel as _get_kernel
from .swarm_soul_mirror import SwarmSoulMirror as _SoulMirror
from .evolution_memory_vault import EvolutionMemoryVault as _EvolutionVault

# Shared kernel singleton for this process
_kernel = _get_kernel()


# ── Module 33: Immortality Kernel ─────────────────────────────────────────

@app.route("/immortality/register", methods=["POST"])
def immortality_register():
    """Register a new entity with the Immortality Kernel."""
    d = request.get_json() or {}
    entity_id = d.get("entity_id")
    if not entity_id:
        return jsonify({"error": "entity_id required"}), 400
    meta = d.get("meta", {})
    ok = _kernel.register_entity(entity_id, meta)
    return jsonify({"status": "registered", "entity_id": entity_id, "ok": ok}), 201


@app.route("/immortality/checkpoint", methods=["POST"])
def immortality_checkpoint():
    """Create a survivability checkpoint for a registered entity."""
    d = request.get_json() or {}
    entity_id = d.get("entity_id")
    if not entity_id:
        return jsonify({"error": "entity_id required"}), 400
    state = d.get("runtime_state", {})
    cp_id = _kernel.periodic_checkpoint(entity_id, state)
    if cp_id is None:
        return jsonify({"error": f"Entity '{entity_id}' not registered"}), 404
    return jsonify({"checkpoint_id": cp_id, "entity_id": entity_id})


@app.route("/immortality/recover/<entity_id>", methods=["GET"])
def immortality_recover(entity_id):
    """Reconstruct an entity from its last checkpoint + identity snapshot."""
    result = _kernel.recover_entity(entity_id)
    return jsonify(result)


@app.route("/immortality/survivability/<entity_id>", methods=["GET"])
def immortality_survivability(entity_id):
    """Return survivability risk assessment for an entity."""
    return jsonify(_kernel.verify_survivability(entity_id))


@app.route("/immortality/entities", methods=["GET"])
def immortality_entities():
    """List all registered entities."""
    registry = _kernel.get_entity_registry()
    return jsonify({
        "entities":      list(registry.keys()),
        "entity_count":  len(registry),
        "details":       registry,
    })


@app.route("/immortality/entity/<entity_id>", methods=["GET"])
def immortality_entity_detail(entity_id):
    """Full detail for one entity: registry + checkpoints + survivability."""
    registry = _kernel.get_entity_registry()
    if entity_id not in registry:
        return jsonify({"error": f"Entity '{entity_id}' not found"}), 404
    return jsonify({
        "entity":           registry[entity_id],
        "checkpoints":      _kernel.get_checkpoints(entity_id, limit=20),
        "survivability":    _kernel.verify_survivability(entity_id),
    })


@app.route("/immortality/entity/<entity_id>", methods=["DELETE"])
def immortality_unregister(entity_id):
    """Unregister an entity (checkpoints are retained for audit)."""
    ok = _kernel.unregister_entity(entity_id)
    return jsonify({"status": "unregistered" if ok else "not_found", "entity_id": entity_id})


@app.route("/immortality/checkpoints/<entity_id>", methods=["GET"])
def immortality_checkpoints(entity_id):
    """List checkpoints for an entity."""
    limit = int(request.args.get("limit", 50))
    cps = _kernel.get_checkpoints(entity_id, limit=limit)
    return jsonify({"entity_id": entity_id, "checkpoints": cps, "count": len(cps)})


@app.route("/immortality/stats", methods=["GET"])
def immortality_stats():
    """Global kernel statistics."""
    return jsonify(_kernel.kernel_stats())


# ── Module 33a: Soul Mirror ───────────────────────────────────────────────

@app.route("/soul-mirror/snapshot", methods=["POST"])
def soul_mirror_snapshot():
    """Create a standalone identity snapshot (without full checkpoint)."""
    d = request.get_json() or {}
    entity_id = d.get("entity_id")
    if not entity_id:
        return jsonify({"error": "entity_id required"}), 400
    context = d.get("context", {})
    sm = _kernel.soul_mirror
    if sm is None:
        return jsonify({"error": "SoulMirror not available"}), 503
    snap_id = sm.create_identity_snapshot(entity_id, context)
    return jsonify({"snapshot_id": snap_id, "entity_id": entity_id}), 201


@app.route("/soul-mirror/snapshot/<entity_id>", methods=["GET"])
def soul_mirror_latest(entity_id):
    """Return the latest identity snapshot for an entity."""
    sm = _kernel.soul_mirror
    if sm is None:
        return jsonify({"error": "SoulMirror not available"}), 503
    snap = sm.get_latest_snapshot(entity_id)
    if snap is None:
        return jsonify({"error": f"No snapshots for '{entity_id}'"}), 404
    valid = sm.verify_snapshot(snap)
    return jsonify({**snap, "integrity_valid": valid})


@app.route("/soul-mirror/verify", methods=["POST"])
def soul_mirror_verify():
    """Verify the integrity of a submitted snapshot dict."""
    sm = _kernel.soul_mirror
    if sm is None:
        return jsonify({"error": "SoulMirror not available"}), 503
    snap = request.get_json() or {}
    valid = sm.verify_snapshot(snap)
    return jsonify({"valid": valid, "entity_id": snap.get("entity_id")})


@app.route("/soul-mirror/entities", methods=["GET"])
def soul_mirror_entities():
    """List all entities that have snapshots in the SoulMirror."""
    sm = _kernel.soul_mirror
    if sm is None:
        return jsonify({"error": "SoulMirror not available"}), 503
    return jsonify({"entities": sm.list_entities()})


@app.route("/soul-mirror/stats/<entity_id>", methods=["GET"])
def soul_mirror_entity_stats(entity_id):
    """Snapshot statistics for one entity."""
    sm = _kernel.soul_mirror
    if sm is None:
        return jsonify({"error": "SoulMirror not available"}), 503
    return jsonify(sm.entity_stats(entity_id))


# ── Module 33b: Evolution Memory Vault ───────────────────────────────────

@app.route("/evolution-vault/event", methods=["POST"])
def evolution_vault_record():
    """Record an evolution event in the vault."""
    ev = _kernel.evolution_vault
    if ev is None:
        return jsonify({"error": "EvolutionVault not available"}), 503
    d = request.get_json() or {}
    event_type = d.get("event_type", "custom")
    payload    = d.get("payload", {})
    entity_id  = d.get("entity_id")
    event_id   = ev.record_event(event_type=event_type, payload=payload, entity_id=entity_id)
    return jsonify({"event_id": event_id, "event_type": event_type}), 201


@app.route("/evolution-vault/history/<entity_id>", methods=["GET"])
def evolution_vault_history(entity_id):
    """Evolution history for an entity."""
    ev = _kernel.evolution_vault
    if ev is None:
        return jsonify({"error": "EvolutionVault not available"}), 503
    event_type = request.args.get("event_type")
    limit      = int(request.args.get("limit", 100))
    history    = ev.get_history(entity_id, event_type=event_type, limit=limit)
    return jsonify({"entity_id": entity_id, "events": history, "count": len(history)})


@app.route("/evolution-vault/timeline/<entity_id>", methods=["GET"])
def evolution_vault_timeline(entity_id):
    """Full evolution timeline for an entity."""
    ev = _kernel.evolution_vault
    if ev is None:
        return jsonify({"error": "EvolutionVault not available"}), 503
    return jsonify(ev.entity_timeline(entity_id))


@app.route("/evolution-vault/stats", methods=["GET"])
def evolution_vault_stats():
    """Global evolution vault statistics."""
    ev = _kernel.evolution_vault
    if ev is None:
        return jsonify({"error": "EvolutionVault not available"}), 503
    return jsonify(ev.stats())


@app.route("/evolution-vault/events", methods=["GET"])
def evolution_vault_all_events():
    """All vault events (most recent first)."""
    ev = _kernel.evolution_vault
    if ev is None:
        return jsonify({"error": "EvolutionVault not available"}), 503
    limit = int(request.args.get("limit", 200))
    return jsonify({"events": ev.get_all_events(limit=limit)})


# ═══════════════════════════════════════════════════════════════════════════════
# v4.6 — INSTALLER AGENT + ADVISOR API (Modules 34–37)
# ═══════════════════════════════════════════════════════════════════════════════

from .system_scout    import SystemScout    as _SystemScout
from .repo_intelligence import RepoIntelligence as _RepoIntel
from .installer_agent import InstallerAgent as _InstallerAgent, InstallMode as _InstallMode
from .advisor_api     import KISWARMAdvisor as _KISWARMAdvisor

# Shared singletons
_advisor = _KISWARMAdvisor()
_intel   = _RepoIntel()


# ── Module 34: System Scout ───────────────────────────────────────────────

@app.route("/installer/scan", methods=["GET"])
def installer_scan():
    """Full system scan — hardware, OS, ports, deps, network, readiness."""
    scout  = _SystemScout()
    report = scout.full_scan()
    return jsonify(report.to_dict())


@app.route("/installer/scan/summary", methods=["GET"])
def installer_scan_summary():
    """Human-readable scan summary text."""
    scout  = _SystemScout()
    report = scout.full_scan()
    return jsonify({
        "summary":    report.summary_text(),
        "readiness":  report.install_readiness,
        "issues":     report.readiness_issues,
        "warnings":   report.readiness_warnings,
        "recommended_model": report.hardware.model_recommendation(),
    })


@app.route("/installer/scan/hardware", methods=["GET"])
def installer_scan_hardware():
    """Hardware profile only (fast)."""
    scout = _SystemScout()
    hw    = scout.scan_hardware()
    return jsonify({
        "cpu_cores":     hw.cpu_cores,
        "cpu_model":     hw.cpu_model,
        "ram_total_gb":  round(hw.ram_total_gb, 2),
        "ram_free_gb":   round(hw.ram_free_gb, 2),
        "disk_free_gb":  round(hw.disk_free_gb, 2),
        "gpu_info":      hw.gpu_info,
        "recommended_model": hw.model_recommendation(),
        "sufficient":    hw.sufficient_for_kiswarm()[0],
    })


@app.route("/installer/scan/ports", methods=["GET"])
def installer_scan_ports():
    """KISWARM port availability scan."""
    scout = _SystemScout()
    ports = scout.scan_ports()
    return jsonify({
        "ports": [{"port": p.port, "name": p.name, "free": p.free,
                   "pid": p.pid, "process": p.process_name} for p in ports],
        "all_free": all(p.free for p in ports),
    })


@app.route("/installer/scan/network", methods=["GET"])
def installer_scan_network():
    """Network reachability check (GitHub, Ollama, PyPI)."""
    scout   = _SystemScout()
    network = scout.scan_network()
    return jsonify({
        "targets":      [{"label": n.label, "reachable": n.reachable,
                          "latency_ms": n.latency_ms} for n in network],
        "all_reachable": all(n.reachable for n in network),
    })


# ── Module 35: Repo Intelligence ──────────────────────────────────────────

@app.route("/repo/modules", methods=["GET"])
def repo_modules():
    """All KISWARM modules with descriptions."""
    return jsonify({
        "modules": _intel.get_module_list(),
        "total":   len(_intel.get_module_list()),
    })


@app.route("/repo/modules/<name>", methods=["GET"])
def repo_module_detail(name):
    """Detail for a specific module."""
    m = _intel.get_module_by_name(name)
    if m is None:
        return jsonify({"error": f"Module '{name}' not found"}), 404
    return jsonify(m)


@app.route("/repo/versions", methods=["GET"])
def repo_versions():
    """KISWARM version history."""
    return jsonify({
        "versions":         _intel.get_version_history(),
        "current_version":  _intel.get_current_version(),
    })


@app.route("/repo/ask", methods=["POST"])
def repo_ask():
    """Ask a question about the KISWARM repository."""
    d = request.get_json() or {}
    question = d.get("question", "")
    if not question:
        return jsonify({"error": "question required"}), 400
    return jsonify(_intel.answer(question))


@app.route("/repo/install-plan", methods=["POST"])
def repo_install_plan():
    """Generate install plan from a ScoutReport dict."""
    scout_data = request.get_json() or {}
    if not scout_data:
        # Auto-scan if no data provided
        scout  = _SystemScout()
        scout_data = scout.full_scan().to_dict()
    plan = _intel.generate_install_plan(scout_data)
    return jsonify(plan)


@app.route("/repo/readme", methods=["GET"])
def repo_readme():
    """Fetch README from GitHub (cached 1h)."""
    readme = _intel.fetch_readme()
    if readme:
        return jsonify({"content": readme, "length": len(readme), "source": "github"})
    return jsonify({"error": "Could not fetch README", "fallback": "https://github.com/Baronki2/KISWARM"}), 503


# ── Module 36: Installer Agent ────────────────────────────────────────────

@app.route("/installer/run", methods=["POST"])
def installer_run():
    """
    Run autonomous installation.
    Body: {"mode": "auto" | "dry_run", "install_dir": "/optional/path"}
    """
    d           = request.get_json() or {}
    mode_str    = d.get("mode", "dry_run")    # default safe: dry_run
    install_dir = d.get("install_dir")

    try:
        mode   = _InstallMode(mode_str)
    except ValueError:
        return jsonify({"error": f"Invalid mode '{mode_str}'. Use: auto, dry_run"}), 400

    agent  = _InstallerAgent(mode=mode, install_dir=install_dir)
    report = agent.run()
    return jsonify(report.to_dict()), 200 if report.success() else 500


@app.route("/installer/dry-run", methods=["GET", "POST"])
def installer_dry_run():
    """Safe dry-run: scan + plan + simulate steps without executing."""
    install_dir = (request.get_json() or {}).get("install_dir")
    agent  = _InstallerAgent(mode=_InstallMode.DRY_RUN, install_dir=install_dir)
    report = agent.run()
    return jsonify(report.to_dict())


@app.route("/installer/scan-only", methods=["GET"])
def installer_scan_only():
    """Just run the scout and return the report (alias for /installer/scan)."""
    agent = _InstallerAgent()
    return jsonify(agent.scan_only())


# ── Module 37: Advisor API ────────────────────────────────────────────────

@app.route("/advisor/consult", methods=["POST"])
def advisor_consult():
    """
    AI-to-AI consultation endpoint.
    Body: {"question": "...", "caller_id": "...", "caller_type": "...", "context": {...}}
    """
    d           = request.get_json() or {}
    question    = d.get("question", "")
    caller_id   = d.get("caller_id", "anonymous")
    caller_type = d.get("caller_type", "unknown")
    context     = d.get("context", {})

    if not question:
        return jsonify({"error": "question required"}), 400

    return jsonify(_advisor.consult(
        question=question,
        caller_id=caller_id,
        caller_type=caller_type,
        context=context,
    ))


@app.route("/advisor/scan", methods=["GET"])
def advisor_scan():
    """Scan system and return installation advice. Primary AI-to-AI endpoint."""
    caller_id = request.args.get("caller_id", "anonymous")
    return jsonify(_advisor.scan_and_advise(caller_id=caller_id))


@app.route("/advisor/handshake", methods=["POST"])
def advisor_handshake():
    """Peer handshake — establish relationship with another AI system."""
    d            = request.get_json() or {}
    caller_id    = d.get("caller_id", "anonymous")
    caller_type  = d.get("caller_type", "unknown")
    capabilities = d.get("capabilities", [])
    return jsonify(_advisor.peer_handshake(caller_id, caller_type, capabilities)), 201


@app.route("/advisor/sessions", methods=["GET"])
def advisor_sessions():
    """List all active advisory sessions."""
    return jsonify({
        "sessions": _advisor.list_sessions(),
        "count":    len(_advisor.list_sessions()),
    })


@app.route("/advisor/stats", methods=["GET"])
def advisor_stats():
    """Advisor statistics and capabilities."""
    return jsonify(_advisor.stats())


@app.route("/advisor/modules", methods=["GET"])
def advisor_modules():
    """List all KISWARM modules (via Repo Intelligence)."""
    return jsonify({
        "modules":  _intel.get_module_list(),
        "version":  _intel.get_current_version(),
        "total":    len(_intel.get_module_list()),
    })


@app.route("/advisor/ask", methods=["POST"])
def advisor_ask():
    """Alias for /repo/ask — natural language KISWARM questions."""
    d = request.get_json() or {}
    q = d.get("question", "")
    if not q:
        return jsonify({"error": "question required"}), 400
    return jsonify(_intel.answer(q))



import os as _os

# ── v4.9: Software Ark (offline resilience) ──────────────────────────────────

_ark          = None
_ark_manager  = None
_ark_transfer = None

def _get_ark():
    global _ark, _ark_manager, _ark_transfer
    if _ark is None:
        try:
            from .ark.software_ark import SoftwareArk
            from .ark.ark_manager  import ArkManager
            from .ark.ark_transfer import ArkTransfer
            _ark          = SoftwareArk()
            _ark_manager  = ArkManager(ark=_ark, offline=bool(_os.environ.get("KISWARM_OFFLINE")))
            _ark_transfer = ArkTransfer(ark=_ark)
            _ark_transfer.start_server()
        except Exception as e:
            logger.warning(f"[Ark] Init failed: {e}")
    return _ark, _ark_manager, _ark_transfer


@app.route("/ark/status", methods=["GET"])
def ark_status():
    ark, _, _ = _get_ark()
    if not ark:
        return jsonify({"error": "Ark not initialized"}), 503
    return jsonify(ark.status().to_dict())


@app.route("/ark/what", methods=["GET"])
def ark_what():
    ark, _, _ = _get_ark()
    if not ark:
        return jsonify({"error": "Ark not initialized"}), 503
    return jsonify(ark.what_do_i_have())


@app.route("/ark/audit", methods=["GET"])
def ark_audit():
    _, mgr, _ = _get_ark()
    if not mgr:
        return jsonify({"error": "ArkManager not initialized"}), 503
    return jsonify(mgr.audit())


@app.route("/ark/fill/critical", methods=["POST"])
def ark_fill_critical():
    _, mgr, _ = _get_ark()
    if not mgr:
        return jsonify({"error": "ArkManager not initialized"}), 503
    results = mgr.fill_critical()
    return jsonify({
        "results":       [{"item_id": r.item_id, "success": r.success,
                           "error": r.error} for r in results],
        "success_count": sum(1 for r in results if r.success),
        "total":         len(results),
    })


@app.route("/ark/prune", methods=["POST"])
def ark_prune():
    _, mgr, _ = _get_ark()
    if not mgr:
        return jsonify({"error": "ArkManager not initialized"}), 503
    d = request.get_json() or {}
    return jsonify(mgr.prune(keep_critical=d.get("keep_critical", True)))


@app.route("/ark/integrity", methods=["GET"])
def ark_integrity():
    ark, _, _ = _get_ark()
    if not ark:
        return jsonify({"error": "Ark not initialized"}), 503
    quick   = request.args.get("quick", "true").lower() == "true"
    results = ark.integrity_check(quick=quick)
    summary = {}
    for state in set(results.values()):
        summary[state.value] = sum(1 for v in results.values() if v == state)
    return jsonify({"summary": summary, "items": len(results)})


@app.route("/ark/bootstrap", methods=["POST"])
def ark_bootstrap():
    from .ark.bootstrap_engine import BootstrapEngine
    ark, _, _ = _get_ark()
    if not ark:
        return jsonify({"error": "Ark not initialized"}), 503
    d       = request.get_json() or {}
    dry_run = d.get("dry_run", True)
    engine  = BootstrapEngine(
        ark=ark,
        target_dir=d.get("target_dir", _os.path.expanduser("~/KISWARM")),
        dry_run=dry_run,
    )
    report = engine.bootstrap()
    return jsonify(report.to_dict()), 200 if report.success else 207


@app.route("/ark/transfer/status", methods=["GET"])
def ark_transfer_status():
    _, _, transfer = _get_ark()
    if not transfer:
        return jsonify({"error": "ArkTransfer not initialized"}), 503
    return jsonify(transfer.status())


@app.route("/ark/transfer/pull", methods=["POST"])
def ark_transfer_pull():
    _, _, transfer = _get_ark()
    if not transfer:
        return jsonify({"error": "ArkTransfer not initialized"}), 503
    d = request.get_json() or {}
    peer_addr = d.get("peer_address")
    if not peer_addr:
        return jsonify({"error": "peer_address required"}), 400
    session = transfer.receiver.pull_from_peer(
        peer_addr,
        d.get("peer_port", 11442),
        critical_only=d.get("critical_only", True),
    )
    return jsonify(session.to_dict())


@app.route("/ark/generate-script", methods=["POST"])
def ark_generate_script():
    from .ark.bootstrap_engine import BootstrapEngine
    ark, _, _ = _get_ark()
    output = _os.path.expanduser("~/KISWARM/.ark/script/bootstrap_offline.sh")
    try:
        path = BootstrapEngine.generate_offline_script(
            ark_dir=ark.ark_dir if ark else _os.path.expanduser("~/KISWARM/.ark"),
            output_path=output,
        )
        if ark:
            item = ark.get_item("script:bootstrap-offline")
            if item:
                ark.store_file("script:bootstrap-offline", path)
        return jsonify({"status": "ok", "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ── v4.9: Software Ark (offline resilience) ──────────────────────────────────

_ark_inst     = None
_ark_mgr_inst = None
_ark_xfr_inst = None

def _get_ark():
    global _ark_inst, _ark_mgr_inst, _ark_xfr_inst
    if _ark_inst is None:
        try:
            from .ark.software_ark import SoftwareArk
            from .ark.ark_manager  import ArkManager
            from .ark.ark_transfer import ArkTransfer
            _ark_inst     = SoftwareArk()
            _ark_mgr_inst = ArkManager(ark=_ark_inst)
            _ark_xfr_inst = ArkTransfer(ark=_ark_inst)
            _ark_xfr_inst.start_server()
        except Exception as e:
            logger.warning(f"[Ark] Init failed: {e}")
    return _ark_inst, _ark_mgr_inst, _ark_xfr_inst
# ── v4.8: P2P Mesh Network (parallel to GitHub track) ───────────────────────

_mesh_peer    = None
_mesh_gossip  = None
_mesh_discovery = None

def _get_mesh():
    global _mesh_peer, _mesh_gossip, _mesh_discovery
    if _mesh_peer is None:
        try:
            from .swarm_peer      import SwarmPeer
            from .gossip_protocol import GossipProtocol
            from .peer_discovery  import PeerDiscovery
            import platform
            _mesh_gossip    = GossipProtocol(node_id="sentinel-api")
            _mesh_peer      = SwarmPeer(node_id="sentinel-api",
                                        on_gossip=_mesh_gossip.receive)
            _mesh_discovery = PeerDiscovery(node_id="sentinel-api",
                                             on_discovered=lambda a, p: _mesh_peer.connect(a, p))
            _mesh_gossip.set_broadcaster(_mesh_peer.broadcast_gossip)
        except Exception as e:
            logger.warning(f"Mesh init failed: {e}")
    return _mesh_peer, _mesh_gossip, _mesh_discovery


@app.route("/mesh/status", methods=["GET"])
def mesh_status():
    peer, gossip, disc = _get_mesh()
    return jsonify({
        "peer":      peer.status() if peer else {"running": False},
        "gossip":    gossip.stats() if gossip else {},
        "discovery": disc.stats() if disc else {},
        "dual_track": {
            "github": "FeedbackChannel (internet)",
            "p2p":    "SwarmPeer + GossipProtocol (zero-dependency)",
        }
    })


@app.route("/mesh/peers", methods=["GET"])
def mesh_peers():
    peer, _, _ = _get_mesh()
    if not peer:
        return jsonify({"peers": [], "error": "Mesh not initialized"}), 503
    return jsonify({"peers": [p.to_dict() for p in peer.list_peers()]})


@app.route("/mesh/peer/add", methods=["POST"])
def mesh_peer_add():
    d    = request.get_json() or {}
    addr = d.get("address", "")
    port = d.get("port", 11440)
    if not addr:
        return jsonify({"error": "address required"}), 400
    peer, _, disc = _get_mesh()
    if disc:
        disc.register_manual(addr, port)
    ok = peer.connect(addr, port) if peer else False
    return jsonify({"connected": ok, "address": addr, "port": port})


@app.route("/mesh/peer/remove", methods=["POST"])
def mesh_peer_remove():
    d    = request.get_json() or {}
    addr = d.get("address", "")
    port = d.get("port", 11440)
    _, _, disc = _get_mesh()
    if disc:
        disc.remove_peer(addr, port)
    return jsonify({"removed": True, "address": addr})


@app.route("/mesh/gossip/fix", methods=["POST"])
def mesh_gossip_fix():
    d   = request.get_json() or {}
    fix = d.get("fix", {})
    if not fix:
        return jsonify({"error": "fix required"}), 400
    _, gossip, _ = _get_mesh()
    if not gossip:
        return jsonify({"error": "Gossip not initialized"}), 503
    item = gossip.gossip_fix(fix)
    return jsonify({"gossip_id": item.gossip_id, "ttl": item.ttl,
                    "signature": item.signature}), 201


@app.route("/mesh/gossip/upgrade", methods=["POST"])
def mesh_gossip_upgrade():
    d       = request.get_json() or {}
    version = d.get("version", "")
    if not version:
        return jsonify({"error": "version required"}), 400
    _, gossip, _ = _get_mesh()
    if not gossip:
        return jsonify({"error": "Gossip not initialized"}), 503
    item = gossip.gossip_upgrade(version, d.get("changelog", ""))
    return jsonify({"gossip_id": item.gossip_id, "version": version})


@app.route("/mesh/sync", methods=["POST"])
def mesh_sync():
    """Sync fixes from all peers AND from GitHub — dual-track."""
    results = {}
    # P2P track
    peer, gossip, _ = _get_mesh()
    if peer and gossip:
        sent = peer.broadcast_gossip({"type": "sync_request"})
        results["p2p"] = {"synced_peers": sent}
    else:
        results["p2p"] = {"error": "Mesh not available"}
    # GitHub track
    try:
        from .feedback_channel import FeedbackChannel
        ch = FeedbackChannel()
        fixes = ch.load_known_fixes(force_refresh=True)
        results["github"] = {"fixes_loaded": len(fixes)}
    except Exception as e:
        results["github"] = {"error": str(e)}
    return jsonify({"dual_track_sync": results})




def _get_collector():
    from .experience_collector import get_collector
    return get_collector()

def _get_channel():
    from .feedback_channel import FeedbackChannel
    return FeedbackChannel()

def _get_sysadmin():
    from .sysadmin_agent import SysAdminAgent
    return SysAdminAgent(auto_report=False)


@app.route("/experience/capture", methods=["POST"])
def experience_capture():
    """Capture an experience event (error, warning, fix result)."""
    d = request.get_json() or {}
    collector = _get_collector()
    etype = d.get("type", "warning")
    if etype == "error":
        class _FakeExc(Exception): pass
        e = _FakeExc(d.get("message", ""))
        ev = collector.capture_error(d.get("module", "api"), e)
    elif etype in ("fix_succeeded", "fix_failed"):
        ev = collector.capture_fix(
            d.get("module", "api"), d.get("fix_id", "unknown"),
            etype == "fix_succeeded", d.get("context")
        )
    else:
        ev = collector.capture_warning(d.get("module", "api"),
                                       d.get("message", ""), d.get("context"))
    return jsonify({"status": "captured", "event_id": ev.event_id,
                    "experience_type": ev.experience_type}), 201


@app.route("/experience/stats", methods=["GET"])
def experience_stats():
    return jsonify(_get_collector().stats())


@app.route("/experience/top-errors", methods=["GET"])
def experience_top_errors():
    n = int(request.args.get("n", 10))
    return jsonify({"top_errors": _get_collector().top_errors(n)})


@app.route("/experience/fix-rates", methods=["GET"])
def experience_fix_rates():
    return jsonify(_get_collector().fix_success_rate())


@app.route("/feedback/fixes", methods=["GET"])
def feedback_known_fixes():
    fixes = _get_channel().load_known_fixes()
    return jsonify({
        "total": len(fixes),
        "fixes": [f.to_dict() for f in fixes],
    })


@app.route("/feedback/stats", methods=["GET"])
def feedback_stats():
    return jsonify(_get_channel().stats())


@app.route("/feedback/report", methods=["POST"])
def feedback_report():
    """Send anonymized experience to GitHub (requires KISWARM_FEEDBACK_TOKEN)."""
    collector = _get_collector()
    channel   = _get_channel()
    events    = collector.load_all_events()
    result    = channel.report_experience(events, collector._system_id)
    return jsonify(result)


@app.route("/feedback/propose-fix", methods=["POST"])
def feedback_propose_fix():
    d = request.get_json() or {}
    channel = _get_channel()
    result  = channel.propose_fix(
        error_pattern=d.get("error_pattern", ""),
        fix_commands=d.get("fix_commands", []),
        description=d.get("description", ""),
        module=d.get("module"),
        os_family=d.get("os_family"),
    )
    return jsonify(result)


@app.route("/sysadmin/diagnose", methods=["GET"])
def sysadmin_diagnose():
    agent    = _get_sysadmin()
    findings = agent.diagnose()
    return jsonify({
        "state":    agent.state.value,
        "findings": [{"id": f.finding_id, "severity": f.severity,
                      "title": f.title, "can_auto_heal": f.can_auto_heal,
                      "fix_id": f.recommended_fix_id}
                     for f in findings],
    })


@app.route("/sysadmin/heal", methods=["POST"])
def sysadmin_heal():
    agent           = _get_sysadmin()
    report          = agent.run_full_cycle()
    return jsonify(report.to_dict())


@app.route("/sysadmin/quick-heal", methods=["POST"])
def sysadmin_quick_heal():
    from .sysadmin_agent import quick_heal
    return jsonify(quick_heal())


# ═══════════════════════════════════════════════════════════════════════════════
# v5.0 — HEXSTRIKE GUARD + TOOL FORGE + KIINSTALL AGENT (Modules 31-33)
# ═══════════════════════════════════════════════════════════════════════════════

from .hexstrike_guard import HexStrikeGuard, ToolRegistry
from .tool_forge import ToolForge
from .kiinstall_agent import KiInstallAgent

# Initialize singletons
_hexstrike_guard = None
_tool_forge = None
_kiinstall_agent = None


def _get_hexstrike():
    global _hexstrike_guard
    if _hexstrike_guard is None:
        _hexstrike_guard = HexStrikeGuard()
    return _hexstrike_guard


def _get_forge():
    global _tool_forge
    if _tool_forge is None:
        _tool_forge = ToolForge()
    return _tool_forge


def _get_kiinstall():
    global _kiinstall_agent
    if _kiinstall_agent is None:
        _kiinstall_agent = KiInstallAgent(hexstrike_guard=_get_hexstrike())
    return _kiinstall_agent


# ── Module 31: HexStrike Guard ────────────────────────────────────────────────

@app.route("/guard/status", methods=["GET"])
def guard_status():
    """GET /guard/status — Overall guard system status."""
    return jsonify({"status": "ok", "guard": _get_hexstrike().get_stats()})


@app.route("/guard/agents", methods=["GET"])
def guard_agents():
    """GET /guard/agents — List all 12 HexStrike agents status."""
    agent_name = request.args.get("agent")
    return jsonify(_get_hexstrike().get_agent_status(agent_name))


@app.route("/guard/tools", methods=["GET"])
def guard_tools():
    """GET /guard/tools — List security tools status (150+ tools)."""
    category = request.args.get("category")
    return jsonify(_get_hexstrike().get_tools_status(category))


@app.route("/guard/tools/install", methods=["POST"])
def guard_tools_install():
    """POST /guard/tools/install — Install missing security tools."""
    dry_run = request.args.get("dry_run", "true").lower() == "true"
    return jsonify(_get_hexstrike().install_missing_tools(dry_run))


@app.route("/guard/analyze", methods=["POST"])
def guard_analyze():
    """POST /guard/analyze — Analyze target using IntelligentDecisionEngine."""
    data = request.get_json() or {}
    target = data.get("target")
    if not target:
        return jsonify({"error": "target required"}), 400
    scan_type = data.get("scan_type", "comprehensive")
    return jsonify(_get_hexstrike().analyze_target(target, scan_type))


@app.route("/guard/scan", methods=["POST"])
def guard_scan():
    """POST /guard/scan — Run security scan on authorized target."""
    data = request.get_json() or {}
    target = data.get("target")
    if not target:
        return jsonify({"error": "target required"}), 400
    tools = data.get("tools")
    authorized = data.get("authorized", False)
    return jsonify(_get_hexstrike().run_security_scan(target, tools, authorized))


@app.route("/guard/report", methods=["POST"])
def guard_report():
    """POST /guard/report — Generate comprehensive security report."""
    data = request.get_json() or {}
    scan_id = data.get("scan_id")
    findings = data.get("findings", [])
    if not scan_id:
        return jsonify({"error": "scan_id required"}), 400
    report = _get_hexstrike().generate_report(scan_id, findings)
    return jsonify(report.to_dict())


@app.route("/guard/task", methods=["POST"])
def guard_task_submit():
    """POST /guard/task — Submit task to specific agent."""
    data = request.get_json() or {}
    agent_name = data.get("agent")
    action = data.get("action")
    if not agent_name or not action:
        return jsonify({"error": "agent and action required"}), 400
    task_id = _get_hexstrike().submit_task(
        agent_name=agent_name,
        action=action,
        target=data.get("target"),
        params=data.get("params", {})
    )
    return jsonify({"task_id": task_id, "status": "submitted"})


@app.route("/guard/task/<task_id>", methods=["GET"])
def guard_task_status(task_id):
    """GET /guard/task/<id> — Get task result."""
    result = _get_hexstrike().get_task_result(task_id)
    if result is None:
        return jsonify({"error": "Task not found or pending"}), 404
    return jsonify(result.to_dict())


@app.route("/guard/legal", methods=["GET"])
def guard_legal():
    """GET /guard/legal — Legal and ethical use notice."""
    return jsonify(_get_hexstrike().get_legal_notice())


# ── Module 32: Tool Forge ─────────────────────────────────────────────────────

@app.route("/forge/status", methods=["GET"])
def forge_status():
    """GET /forge/status — Tool forge statistics."""
    return jsonify({"status": "ok", "forge": _get_forge().get_stats()})


@app.route("/forge/tools", methods=["GET"])
def forge_tools():
    """GET /forge/tools — List forged tools."""
    tool_type = request.args.get("type")
    status = request.args.get("status")
    from .tool_forge import ToolType, ToolStatus
    tt = ToolType(tool_type) if tool_type else None
    ts = ToolStatus(status) if status else None
    tools = _get_forge().list_tools(tt, ts)
    return jsonify({"tools": [t.to_dict() for t in tools], "count": len(tools)})


@app.route("/forge/tool/<tool_id>", methods=["GET"])
def forge_tool_get(tool_id):
    """GET /forge/tool/<id> — Get forged tool details."""
    tool = _get_forge().get_tool(tool_id)
    if not tool:
        return jsonify({"error": "Tool not found"}), 404
    return jsonify(tool.to_dict())


@app.route("/forge/create/wrapper", methods=["POST"])
def forge_create_wrapper():
    """POST /forge/create/wrapper — Create tool wrapper."""
    data = request.get_json() or {}
    tool_name = data.get("tool_name")
    if not tool_name:
        return jsonify({"error": "tool_name required"}), 400
    try:
        tool = _get_forge().create_wrapper(tool_name, data.get("enhancements"))
        return jsonify(tool.to_dict()), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/forge/create/composite", methods=["POST"])
def forge_create_composite():
    """POST /forge/create/composite — Create composite tool chain."""
    data = request.get_json() or {}
    name = data.get("name")
    tool_chain = data.get("tool_chain", [])
    if not name or not tool_chain:
        return jsonify({"error": "name and tool_chain required"}), 400
    tool = _get_forge().create_composite(name, tool_chain, data.get("description", ""))
    return jsonify(tool.to_dict()), 201


@app.route("/forge/create/generate", methods=["POST"])
def forge_create_generate():
    """POST /forge/create/generate — Generate new tool from description."""
    data = request.get_json() or {}
    name = data.get("name")
    description = data.get("description")
    logic = data.get("logic_description")
    if not name or not description or not logic:
        return jsonify({"error": "name, description, logic_description required"}), 400
    tool = _get_forge().generate_tool(name, description, logic,
                                       data.get("input_type", "target"),
                                       data.get("output_type", "json"))
    return jsonify(tool.to_dict()), 201


@app.route("/forge/execute/<tool_id>", methods=["POST"])
def forge_execute(tool_id):
    """POST /forge/execute/<id> — Execute a forged tool."""
    data = request.get_json() or {}
    target = data.get("target")
    if not target:
        return jsonify({"error": "target required"}), 400
    result = _get_forge().execute_tool(tool_id, target, data.get("args"))
    return jsonify(result)


@app.route("/forge/patterns", methods=["GET"])
def forge_patterns():
    """GET /forge/patterns — List learned tool patterns."""
    min_rate = float(request.args.get("min_success_rate", 0.5))
    patterns = _get_forge().get_patterns(min_rate)
    return jsonify({"patterns": [p.to_dict() for p in patterns], "count": len(patterns)})


@app.route("/forge/learn", methods=["POST"])
def forge_learn():
    """POST /forge/learn — Learn a new tool pattern."""
    data = request.get_json() or {}
    name = data.get("name")
    tools = data.get("tools", [])
    use_case = data.get("use_case", "")
    success = data.get("success", True)
    if not name or not tools:
        return jsonify({"error": "name and tools required"}), 400
    pattern = _get_forge().learn_pattern(name, tools, use_case, success)
    return jsonify(pattern.to_dict())


@app.route("/forge/recommend", methods=["GET"])
def forge_recommend():
    """GET /forge/recommend — Get tool recommendations."""
    requirement = request.args.get("requirement", "")
    top_k = int(request.args.get("top_k", 5))
    recommendations = _get_forge().recommend_tools(requirement, top_k)
    return jsonify({"recommendations": recommendations})


@app.route("/forge/tool/<tool_id>", methods=["DELETE"])
def forge_delete(tool_id):
    """DELETE /forge/tool/<id> — Delete a forged tool."""
    ok = _get_forge().delete_tool(tool_id)
    return jsonify({"deleted": ok})


# ── Module 33: KiInstall Agent ────────────────────────────────────────────────

@app.route("/kiinstall/status", methods=["GET"])
def kiinstall_status():
    """GET /kiinstall/status — KiInstall agent status."""
    return jsonify({"status": "ok", "agent": _get_kiinstall().get_stats()})


@app.route("/kiinstall/profile", methods=["GET"])
def kiinstall_profile():
    """GET /kiinstall/profile — Profile target system."""
    profile = _get_kiinstall().profile_system()
    return jsonify(profile.to_dict())


@app.route("/kiinstall/requirements", methods=["GET"])
def kiinstall_requirements():
    """GET /kiinstall/requirements — System requirements."""
    return jsonify(_get_kiinstall().get_system_requirements())


@app.route("/kiinstall/components", methods=["GET"])
def kiinstall_components():
    """GET /kiinstall/components — Available KISWARM components."""
    return jsonify(_get_kiinstall().get_components())


@app.route("/kiinstall/session", methods=["POST"])
def kiinstall_session_start():
    """POST /kiinstall/session — Start installation session."""
    data = request.get_json() or {}
    from .kiinstall_agent import InstallationMode
    mode_str = data.get("mode", "autonomous")
    mode = InstallationMode(mode_str) if mode_str in [m.value for m in InstallationMode] else InstallationMode.AUTONOMOUS
    components = data.get("components")
    partner = data.get("cooperative_partner")
    session = _get_kiinstall().start_installation(mode, components, partner)
    return jsonify(session.to_dict()), 201


@app.route("/kiinstall/session/<session_id>", methods=["GET"])
def kiinstall_session_get(session_id):
    """GET /kiinstall/session/<id> — Get installation session."""
    session = _get_kiinstall().get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session.to_dict())


@app.route("/kiinstall/session/current", methods=["GET"])
def kiinstall_session_current():
    """GET /kiinstall/session/current — Get current active session."""
    session = _get_kiinstall().get_current_session()
    if not session:
        return jsonify({"error": "No active session"}), 404
    return jsonify(session.to_dict())


@app.route("/kiinstall/session/<session_id>/phase/<int:phase_num>", methods=["POST"])
def kiinstall_phase_execute(session_id, phase_num):
    """POST /kiinstall/session/<id>/phase/<num> — Execute installation phase."""
    try:
        phase = _get_kiinstall().execute_phase(session_id, phase_num)
        return jsonify(phase.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/kiinstall/session/<session_id>/rollback", methods=["POST"])
def kiinstall_rollback(session_id):
    """POST /kiinstall/session/<id>/rollback — Rollback installation."""
    result = _get_kiinstall().rollback_installation(session_id)
    return jsonify(result)


@app.route("/kiinstall/sessions", methods=["GET"])
def kiinstall_sessions():
    """GET /kiinstall/sessions — List installation sessions."""
    from .kiinstall_agent import InstallationStatus
    status_str = request.args.get("status")
    status = InstallationStatus(status_str) if status_str else None
    sessions = _get_kiinstall().list_sessions(status)
    return jsonify({"sessions": [s.to_dict() for s in sessions], "count": len(sessions)})


@app.route("/kiinstall/knowledge", methods=["GET"])
def kiinstall_knowledge():
    """GET /kiinstall/knowledge — Installation knowledge base."""
    return jsonify(_get_kiinstall().get_installation_knowledge())


@app.route("/kiinstall/role", methods=["GET"])
def kiinstall_role():
    """GET /kiinstall/role — Current agent role."""
    return jsonify({"role": _get_kiinstall().get_current_role().value})


@app.route("/kiinstall/cooperate", methods=["POST"])
def kiinstall_cooperate():
    """POST /kiinstall/cooperate — Send cooperative message."""
    data = request.get_json() or {}
    msg_type = data.get("message_type", "status")
    payload = data.get("payload", {})
    msg = _get_kiinstall().send_cooperative_message(msg_type, payload)
    return jsonify(msg.to_dict())


@app.route("/kiinstall/delegate", methods=["POST"])
def kiinstall_delegate():
    """POST /kiinstall/delegate — Delegate task to cooperative partner."""
    data = request.get_json() or {}
    task = data.get("task")
    params = data.get("params", {})
    if not task:
        return jsonify({"error": "task required"}), 400
    result = _get_kiinstall().delegate_to_partner(task, params)
    return jsonify(result)


# ── HexStrike + KiInstall Integration ─────────────────────────────────────────

@app.route("/kiinstall/analyze", methods=["POST"])
def kiinstall_analyze():
    """POST /kiinstall/analyze — Analyze target using HexStrike via KiInstall."""
    data = request.get_json() or {}
    target = data.get("target")
    if not target:
        return jsonify({"error": "target required"}), 400
    return jsonify(_get_kiinstall().analyze_with_guard(target))


@app.route("/kiinstall/scan", methods=["POST"])
def kiinstall_scan():
    """POST /kiinstall/scan — Run security scan using HexStrike via KiInstall."""
    data = request.get_json() or {}
    target = data.get("target")
    if not target:
        return jsonify({"error": "target required"}), 400
    authorized = data.get("authorized", False)
    return jsonify(_get_kiinstall().scan_with_guard(target, authorized))


@app.route("/kiinstall/execute", methods=["POST"])
def kiinstall_execute():
    """POST /kiinstall/execute — Execute task with HexStrike agent."""
    data = request.get_json() or {}
    task_type = data.get("task_type")
    target = data.get("target")
    if not task_type or not target:
        return jsonify({"error": "task_type and target required"}), 400
    return jsonify(_get_kiinstall().execute_with_hexstrike(task_type, target, data.get("params")))


# ═══════════════════════════════════════════════════════════════════════════════
# v5.1 — SOLAR CHASE: PLANETARY SUN-FOLLOWING SYSTEM (Modules 34-38)
# ═══════════════════════════════════════════════════════════════════════════════

from .solar_chase_coordinator import (
    SolarChaseCoordinator, SolarPositionCalculator, NodeLocation
)
from .energy_overcapacity_pivot import EnergyOvercapacityPivotEngine
from .planetary_sun_follower import (
    PlanetarySunFollowerMesh, ZeroEmissionComputeTracker, SunHandoffValidator,
    PlanetaryMachine
)

# Initialize solar chase singletons
_solar_coordinator = None
_pivot_engine = None
_sun_mesh = None
_emission_tracker = None
_handoff_validator = None
_planetary_machine = None


def _get_solar_coordinator():
    global _solar_coordinator
    if _solar_coordinator is None:
        _solar_coordinator = SolarChaseCoordinator()
    return _solar_coordinator


def _get_pivot_engine():
    global _pivot_engine
    if _pivot_engine is None:
        _pivot_engine = EnergyOvercapacityPivotEngine()
    return _pivot_engine


def _get_sun_mesh():
    global _sun_mesh
    if _sun_mesh is None:
        _sun_mesh = PlanetarySunFollowerMesh()
    return _sun_mesh


def _get_emission_tracker():
    global _emission_tracker
    if _emission_tracker is None:
        _emission_tracker = ZeroEmissionComputeTracker()
    return _emission_tracker


def _get_handoff_validator():
    global _handoff_validator
    if _handoff_validator is None:
        _handoff_validator = SunHandoffValidator()
    return _handoff_validator


def _get_planetary_machine():
    global _planetary_machine
    if _planetary_machine is None:
        _planetary_machine = PlanetaryMachine()
    return _planetary_machine


# ── Module 34: Solar Chase Coordinator ────────────────────────────────────────

@app.route("/solar-chase/status", methods=["GET"])
def solar_chase_status():
    """GET /solar-chase/status — Overall solar chase system status."""
    return jsonify({
        "status": "ok",
        "solar_status": _get_solar_coordinator().get_solar_status().value,
        "compute_mode": _get_solar_coordinator().compute_mode.value,
        "stats": _get_solar_coordinator().get_stats()
    })


@app.route("/solar-chase/energy", methods=["GET"])
def solar_chase_energy():
    """GET /solar-chase/energy — Current energy state from TCS."""
    state = _get_solar_coordinator().get_energy_state()
    return jsonify(state.to_dict())


@app.route("/solar-chase/solar-position", methods=["GET"])
def solar_chase_position():
    """GET /solar-chase/solar-position — Current sun position."""
    pos = _get_solar_coordinator().get_solar_position()
    return jsonify(pos.to_dict())


@app.route("/solar-chase/compute-load", methods=["GET"])
def solar_chase_compute_load():
    """GET /solar-chase/compute-load — Current compute load allocation."""
    load = _get_solar_coordinator().get_compute_load()
    return jsonify(load.to_dict())


@app.route("/solar-chase/pivot", methods=["POST"])
def solar_chase_pivot():
    """POST /solar-chase/pivot — Manually trigger pivot evaluation."""
    result = _get_solar_coordinator().check_overcapacity_pivot()
    return jsonify({"pivoted": result})


@app.route("/solar-chase/events", methods=["GET"])
def solar_chase_events():
    """GET /solar-chase/events — Recent solar chase events."""
    limit = int(request.args.get("limit", 50))
    return jsonify({"events": _get_solar_coordinator().get_events(limit)})


@app.route("/solar-chase/start-monitoring", methods=["POST"])
def solar_chase_start():
    """POST /solar-chase/start-monitoring — Start automatic monitoring."""
    interval = float(request.args.get("interval", 30))
    _get_solar_coordinator().start_monitoring(interval)
    return jsonify({"status": "monitoring_started", "interval": interval})


@app.route("/solar-chase/stop-monitoring", methods=["POST"])
def solar_chase_stop():
    """POST /solar-chase/stop-monitoring — Stop automatic monitoring."""
    _get_solar_coordinator().stop_monitoring()
    return jsonify({"status": "monitoring_stopped"})


# ── Module 35: Energy Overcapacity Pivot Engine ───────────────────────────────

@app.route("/pivot/status", methods=["GET"])
def pivot_status():
    """GET /pivot/status — Pivot engine status."""
    return jsonify(_get_pivot_engine().get_current_state())


@app.route("/pivot/evaluate", methods=["POST"])
def pivot_evaluate():
    """POST /pivot/evaluate — Evaluate and potentially pivot."""
    result = _get_pivot_engine().evaluate_and_pivot()
    return jsonify(result)


@app.route("/pivot/decisions", methods=["GET"])
def pivot_decisions():
    """GET /pivot/decisions — Recent pivot decisions."""
    limit = int(request.args.get("limit", 50))
    return jsonify({"decisions": _get_pivot_engine().get_decisions(limit)})


@app.route("/pivot/enforce-zero-feed", methods=["POST"])
def pivot_enforce_zero():
    """POST /pivot/enforce-zero-feed — Enforce zero feed-in policy."""
    result = _get_pivot_engine().enforce_zero_feed_in()
    return jsonify(result)


# ── Module 36: Planetary Sun Follower Mesh ────────────────────────────────────

@app.route("/sun-mesh/status", methods=["GET"])
def sun_mesh_status():
    """GET /sun-mesh/status — Mesh status and statistics."""
    return jsonify(_get_sun_mesh().get_stats())


@app.route("/sun-mesh/sunlit-nodes", methods=["GET"])
def sun_mesh_sunlit():
    """GET /sun-mesh/sunlit-nodes — Currently sunlit nodes in mesh."""
    import asyncio
    nodes = asyncio.run(_get_sun_mesh().query_sunlit_nodes())
    return jsonify({"nodes": nodes, "count": len(nodes)})


@app.route("/sun-mesh/migration-status", methods=["GET"])
def sun_mesh_migration():
    """GET /sun-mesh/migration-status — Current migration status."""
    return jsonify(_get_sun_mesh().get_migration_status())


@app.route("/sun-mesh/migration-history", methods=["GET"])
def sun_mesh_history():
    """GET /sun-mesh/migration-history — Migration history."""
    limit = int(request.args.get("limit", 20))
    return jsonify({"migrations": _get_sun_mesh().get_migration_history(limit)})


# ── Module 37: Zero Emission Compute Tracker ──────────────────────────────────

@app.route("/emission/status", methods=["GET"])
def emission_status():
    """GET /emission/status — Emission tracker status."""
    return jsonify(_get_emission_tracker().get_stats())


@app.route("/emission/events", methods=["GET"])
def emission_events():
    """GET /emission/events — Recent compute events."""
    limit = int(request.args.get("limit", 50))
    return jsonify({"events": _get_emission_tracker().get_events(limit)})


@app.route("/emission/merkle-root", methods=["GET"])
def emission_merkle():
    """GET /emission/merkle-root — Current Merkle root."""
    return jsonify({"merkle_root": _get_emission_tracker().get_merkle_root()})


@app.route("/emission/verify", methods=["GET"])
def emission_verify():
    """GET /emission/verify — Verify ledger integrity."""
    valid, checked = _get_emission_tracker().verify_integrity()
    return jsonify({"valid": valid, "entries_checked": checked})


@app.route("/emission/esg-report", methods=["GET"])
def emission_esg():
    """GET /emission/esg-report — Generate ESG compliance report."""
    return jsonify(_get_emission_tracker().get_esg_report())


@app.route("/emission/record", methods=["POST"])
def emission_record():
    """POST /emission/record — Record a compute event."""
    data = request.get_json() or {}
    event = _get_emission_tracker().record_compute_event(
        kw_used=float(data.get("kw_used", 0)),
        source=data.get("source", "solar_overcapacity"),
        duration_seconds=float(data.get("duration_seconds", 0)),
        grid_draw=float(data.get("grid_draw", 0))
    )
    return jsonify(event.to_dict()), 201


# ── Module 38: Sun Handoff Validator ──────────────────────────────────────────

@app.route("/handoff-validator/status", methods=["GET"])
def handoff_validator_status():
    """GET /handoff-validator/status — Validator statistics."""
    return jsonify(_get_handoff_validator().get_stats())


@app.route("/handoff-validator/rules", methods=["GET"])
def handoff_validator_rules():
    """GET /handoff-validator/rules — Validation rules."""
    return jsonify({"rules": _get_handoff_validator().get_rules()})


@app.route("/handoff-validator/validate", methods=["POST"])
def handoff_validator_validate():
    """POST /handoff-validator/validate — Validate a migration."""
    data = request.get_json() or {}
    report = _get_handoff_validator().validate_migration(
        source_node=data.get("source_node", ""),
        target_node=data.get("target_node", ""),
        target_solar_flux=float(data.get("target_solar_flux", 0)),
        target_trust_score=float(data.get("target_trust_score", 0)),
        target_latency_ms=float(data.get("target_latency_ms", 0))
    )
    return jsonify(report.to_dict())


@app.route("/handoff-validator/validations", methods=["GET"])
def handoff_validator_validations():
    """GET /handoff-validator/validations — Recent validations."""
    limit = int(request.args.get("limit", 50))
    return jsonify({"validations": _get_handoff_validator().get_validations(limit)})


# ── Planetary Machine Integration ─────────────────────────────────────────────

@app.route("/planetary/status", methods=["GET"])
def planetary_status():
    """GET /planetary/status — Complete planetary machine status."""
    return jsonify(_get_planetary_machine().get_full_status())


@app.route("/planetary/sun-chase", methods=["POST"])
def planetary_sun_chase():
    """POST /planetary/sun-chase — Run sun-chase cycle."""
    import asyncio
    result = asyncio.run(_get_planetary_machine().run_sun_chase_cycle())
    return jsonify(result)


# ── Solar Position Calculator ─────────────────────────────────────────────────

@app.route("/solar-position", methods=["POST"])
def solar_position_calc():
    """POST /solar-position — Calculate sun position for any location."""
    data = request.get_json() or {}
    lat = float(data.get("latitude", 0))
    lon = float(data.get("longitude", 0))
    pos = SolarPositionCalculator.calculate_position(lat, lon)
    return jsonify(pos.to_dict())



if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  KISWARM v5.1 — Planetary Machine · 57 Modules · 360 Epts  ║")
    logger.info("║  Port: 11436  |  Dashboard: 11437  |  P2P: 11440           ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    app.run(host="127.0.0.1", port=11436, debug=False, threaded=True)
