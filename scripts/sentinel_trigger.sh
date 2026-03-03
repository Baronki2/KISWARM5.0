#!/bin/bash
################################################################################
# KISWARM v2.1 — SENTINEL BRIDGE CLI TRIGGER
# Integrates with the Central Knowledge Manager (CKM)
#
# Usage:
#   sentinel-extract "quantum computing basics"
#   sentinel-extract "blockchain consensus algorithms" --force
#   sentinel-search "machine learning"
#   sentinel-status
#
# Or from CKM confidence check:
#   if [ "$CONFIDENCE_SCORE" -lt 85 ]; then
#       bash sentinel_trigger.sh "$USER_INPUT"
#   fi
################################################################################

set -euo pipefail

KISWARM_HOME="${KISWARM_HOME:-$HOME}"
KISWARM_DIR="${KISWARM_HOME}/KISWARM"
VENV_PATH="${KISWARM_HOME}/mem0_env"
SENTINEL_API="http://localhost:11436"
LOG_DIR="${KISWARM_HOME}/logs"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()    { echo -e "${CYAN}[SENTINEL]${NC} $1"; }
success(){ echo -e "${GREEN}✓${NC} $1"; }
warn()   { echo -e "${YELLOW}⚠${NC} $1"; }
error()  { echo -e "${RED}✗${NC} $1"; }

# ── Check if Sentinel API is running ─────────────────────────────────────────
sentinel_running() {
    curl -s "${SENTINEL_API}/health" > /dev/null 2>&1
}

# ── Start Sentinel API if not running ────────────────────────────────────────
start_sentinel() {
    if sentinel_running; then
        return 0
    fi
    log "Sentinel Bridge not running. Starting..."
    source "${VENV_PATH}/bin/activate" 2>/dev/null || true
    nohup python3 "${KISWARM_DIR}/../python/sentinel/sentinel_api.py" \
        > "${LOG_DIR}/sentinel_api.log" 2>&1 &
    # Wait up to 10 seconds
    for i in $(seq 1 10); do
        sleep 1
        if sentinel_running; then
            success "Sentinel Bridge started (Port 11436)"
            return 0
        fi
    done
    error "Sentinel Bridge failed to start"
    return 1
}

# ── Main command dispatch ─────────────────────────────────────────────────────
COMMAND="${1:-help}"
shift || true

case "$COMMAND" in

    extract)
        QUERY="$*"
        FORCE_FLAG=""
        if echo "$QUERY" | grep -q "\-\-force"; then
            FORCE_FLAG='"force": true,'
            QUERY="${QUERY/--force/}"
        fi
        QUERY="$(echo "$QUERY" | xargs)"  # trim

        if [ -z "$QUERY" ]; then
            error "Usage: sentinel-extract <query> [--force]"
            exit 1
        fi

        start_sentinel || exit 1

        log "Extracting intelligence for: '$QUERY'"
        RESPONSE=$(curl -s -X POST "${SENTINEL_API}/sentinel/extract" \
            -H "Content-Type: application/json" \
            -d "{${FORCE_FLAG} \"query\": \"${QUERY}\"}")

        STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
        CONFIDENCE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(f\"{json.load(sys.stdin).get('confidence',0):.0%}\")" 2>/dev/null)
        SOURCES=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sources',0))" 2>/dev/null)
        INJECTED=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('injected',False))" 2>/dev/null)

        echo ""
        echo "═══════════════════════════════════════════════"
        echo "  SENTINEL EXTRACTION REPORT"
        echo "═══════════════════════════════════════════════"
        echo "  Query:      $QUERY"
        echo "  Status:     $STATUS"
        echo "  Confidence: $CONFIDENCE"
        echo "  Sources:    $SOURCES"
        echo "  Injected:   $INJECTED"
        echo "═══════════════════════════════════════════════"
        ;;

    search)
        QUERY="$*"
        if [ -z "$QUERY" ]; then
            error "Usage: sentinel-search <query>"
            exit 1
        fi

        start_sentinel || exit 1

        log "Searching swarm memory for: '$QUERY'"
        RESPONSE=$(curl -s "${SENTINEL_API}/sentinel/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${QUERY}'))")")
        echo "$RESPONSE" | python3 -m json.tool
        ;;

    status)
        if sentinel_running; then
            success "Sentinel Bridge is running"
            curl -s "${SENTINEL_API}/sentinel/status" | python3 -m json.tool
        else
            warn "Sentinel Bridge is not running"
            echo "  Start with: bash scripts/sentinel_trigger.sh start"
        fi
        ;;

    start)
        start_sentinel
        ;;

    stop)
        pkill -f "sentinel_api.py" && success "Sentinel Bridge stopped" || warn "Not running"
        ;;

    # ── CKM Integration Mode ─────────────────────────────────────────────────
    ckm-check)
        # Called by CKM: sentinel_trigger.sh ckm-check <confidence_score> <query>
        CONFIDENCE_SCORE="${1:-50}"
        QUERY="${@:2}"
        THRESHOLD=85

        if [ "$CONFIDENCE_SCORE" -lt "$THRESHOLD" ]; then
            log "Knowledge Gap Detected (confidence: ${CONFIDENCE_SCORE}% < ${THRESHOLD}%)"
            log "Deploying Sentinel Bridge for: '$QUERY'"
            start_sentinel || exit 1
            curl -s -X POST "${SENTINEL_API}/sentinel/extract" \
                -H "Content-Type: application/json" \
                -d "{\"query\": \"${QUERY}\"}" > /dev/null
            success "Swarm knowledge updated. Re-process query with enriched context."
        else
            success "Swarm confident (${CONFIDENCE_SCORE}%) — no extraction needed"
        fi
        ;;

    help|*)
        echo ""
        echo "  KISWARM v2.1 — Sentinel Bridge CLI"
        echo "  ════════════════════════════════════"
        echo "  sentinel-extract <query>          Extract intelligence for a query"
        echo "  sentinel-extract <query> --force  Extract regardless of confidence"
        echo "  sentinel-search  <query>          Search existing swarm memory"
        echo "  sentinel-status                   Show Sentinel system status"
        echo "  sentinel-start                    Start Sentinel API server"
        echo "  sentinel-stop                     Stop Sentinel API server"
        echo ""
        echo "  CKM Integration:"
        echo "  sentinel ckm-check <score> <query>  Trigger if score < 85%"
        echo ""
        ;;
esac
