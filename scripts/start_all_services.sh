#!/bin/bash
################################################################################
# KISWARM v1.1 - MASTER SERVICE ORCHESTRATOR
# Called by: systemd (kiswarm.service) or manually
# Handles: Ollama startup, Tool Proxy, dependency verification
################################################################################
set -euo pipefail

KISWARM_HOME="${KISWARM_HOME:-$HOME}"
KISWARM_DIR="${KISWARM_HOME}/KISWARM"
VENV_PATH="${KISWARM_HOME}/mem0_env"
LOG_DIR="${KISWARM_HOME}/logs"
BOOT_LOG="${LOG_DIR}/kiswarm_boot.log"

mkdir -p "$LOG_DIR"

log_event()   { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$BOOT_LOG"; }
log_success() { echo "✓ $1" | tee -a "$BOOT_LOG"; }
log_error()   { echo "✗ $1" | tee -a "$BOOT_LOG"; }
log_warn()    { echo "⚠ $1" | tee -a "$BOOT_LOG"; }

verify_environment() {
    log_event "PHASE 1: Verifying Environment"
    [ -d "$KISWARM_DIR" ] || { log_error "KISWARM directory missing: $KISWARM_DIR"; return 1; }
    [ -d "$VENV_PATH" ]   || { log_error "Virtual env missing: $VENV_PATH"; return 1; }
    mkdir -p "${KISWARM_DIR}/qdrant_data"
    log_success "Environment verified"
}

start_ollama() {
    log_event "PHASE 2: Starting Ollama"
    if pgrep -x "ollama" > /dev/null 2>&1; then
        if curl -s http://localhost:11434/api/tags &>/dev/null; then
            log_success "Ollama already running"
            return 0
        fi
        pkill -9 ollama 2>/dev/null || true
        sleep 2
    fi
    nohup ollama serve > "${LOG_DIR}/ollama.log" 2>&1 &
    local attempts=0
    while [ $attempts -lt 30 ]; do
        curl -s http://localhost:11434/api/tags &>/dev/null && {
            log_success "Ollama responsive on :11434"
            return 0
        }
        attempts=$((attempts+1)); sleep 1
    done
    log_error "Ollama failed to respond after 30s"
    return 1
}

start_tool_proxy() {
    log_event "PHASE 3: Starting Tool Proxy"
    [ -f "${KISWARM_DIR}/tool_proxy.py" ] || { log_warn "tool_proxy.py not found — skipping"; return 0; }
    pgrep -f "tool_proxy.py" &>/dev/null && { log_warn "Proxy already running"; return 0; }
    # shellcheck disable=SC1091
    source "${VENV_PATH}/bin/activate"
    nohup python3 "${KISWARM_DIR}/tool_proxy.py" > "${LOG_DIR}/tool_proxy.log" 2>&1 &
    sleep 2
    curl -s http://localhost:11435/health &>/dev/null \
        && log_success "Tool proxy responsive on :11435" \
        || log_warn "Proxy still starting (check logs/tool_proxy.log)"
}

trap 'log_error "Startup failed at line $LINENO"' ERR

main() {
    log_event "═══════════════════════════════════════════════════"
    log_event "KISWARM v1.1 SERVICE ORCHESTRATOR STARTED"
    log_event "User: $(whoami) | Home: $KISWARM_HOME"
    log_event "═══════════════════════════════════════════════════"
    verify_environment || exit 1
    start_ollama       || exit 1
    start_tool_proxy   || true
    log_event "KISWARM is operational ✓"
}

main "$@"
