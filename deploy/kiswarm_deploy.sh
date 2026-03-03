#!/bin/bash
################################################################################
# KISWARM v1.1 - COMPLETE AUTOMATED DEPLOYMENT & GOVERNANCE SYSTEM
# Repo: https://github.com/Baronki2/KISWARM
# Version: 1.1 Production-Hardened
# Fixes: Portable paths ($HOME), trap error handling, Qdrant vector configs,
#        missing service script, backup rotation, model verification
################################################################################
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
KISWARM_HOME="${KISWARM_HOME:-$HOME}"
KISWARM_DIR="${KISWARM_HOME}/KISWARM"
VENV_PATH="${KISWARM_HOME}/mem0_env"
LOG_FILE="${KISWARM_HOME}/kiswarm_deployment_$(date +%Y%m%d_%H%M%S).log"
touch "$LOG_FILE"

trap 'echo -e "${RED}FAILED at line $LINENO (exit $?)${NC}" | tee -a "$LOG_FILE"; exit 1' ERR
log_success() { echo -e "${GREEN}✓${NC} $1" | tee -a "$LOG_FILE"; }
log_error()   { echo -e "${RED}✗${NC} $1"   | tee -a "$LOG_FILE"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1" | tee -a "$LOG_FILE"; }
log_info()    { echo -e "${CYAN}►${NC} $1"   | tee -a "$LOG_FILE"; }

phase_prerequisites() {
    log_info "PHASE 1: VALIDATING PREREQUISITES"
    local missing=0
    for cmd in curl python3 pip git; do
        command -v "$cmd" &>/dev/null && log_success "Found: $cmd" \
            || { log_warning "Missing: $cmd"; missing=$((missing+1)); }
    done
    if [ $missing -gt 0 ]; then
        sudo apt update -qq 2>/dev/null || true
        sudo apt install -y curl python3 python3-pip python3-venv git 2>&1 | tail -3 >> "$LOG_FILE"
    fi
    if ! command -v ollama &>/dev/null; then
        log_warning "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -5 >> "$LOG_FILE"
        log_success "Ollama installed"
    else
        log_success "Ollama: $(ollama --version 2>/dev/null || echo 'installed')"
    fi
}

phase_directories() {
    log_info "PHASE 2: CREATING DIRECTORY STRUCTURE"
    for dir in "$KISWARM_DIR" \
               "$KISWARM_DIR/onecontext_system" \
               "$KISWARM_DIR/qdrant_data" \
               "$KISWARM_DIR/central_tools_pool" \
               "$KISWARM_DIR/mcp_servers" \
               "$KISWARM_DIR/skills" \
               "$KISWARM_DIR/docs" \
               "$KISWARM_HOME/backups" \
               "$KISWARM_HOME/logs"; do
        mkdir -p "$dir" && log_success "Created: $dir"
    done
    chmod -R 755 "$KISWARM_DIR"
}

phase_venv() {
    log_info "PHASE 3: PYTHON VIRTUAL ENVIRONMENT"
    [ ! -d "$VENV_PATH" ] && python3 -m venv "$VENV_PATH" && log_success "venv created"
    # shellcheck disable=SC1091
    source "${VENV_PATH}/bin/activate"
    pip install --upgrade pip setuptools wheel -q 2>&1 | tail -1 >> "$LOG_FILE"
    # Use pinned requirements.txt if available (preferred)
    REPO_DIR="$(dirname "$(readlink -f "$0")")/.."
    if [ -f "${REPO_DIR}/requirements.txt" ]; then
        log_info "Installing from requirements.txt (pinned versions)..."
        pip install -r "${REPO_DIR}/requirements.txt" -q 2>&1 | tail -5 >> "$LOG_FILE"             || { log_error "requirements.txt install failed — check log"; exit 1; }
    else
        log_warning "requirements.txt not found, installing individually..."
        for pkg in ollama mem0 qdrant-client rich flask requests numpy watchdog psutil flask-cors; do
            log_info "Installing: $pkg"
            pip install "$pkg" -q 2>&1 | tail -1 >> "$LOG_FILE"                 || log_warning "Could not install $pkg — continuing"
        done
    fi
    log_success "Virtual environment ready"
}

phase_scripts() {
    log_info "PHASE 4: DEPLOYING SYSTEM SCRIPTS"

    # Service orchestrator (the missing link)
    cat > "${KISWARM_DIR}/start_all_services.sh" << 'EOF'
#!/bin/bash
# KISWARM Service Orchestrator - called by systemd
source "${HOME}/mem0_env/bin/activate" 2>/dev/null || true
mkdir -p "$HOME/logs"
if ! pgrep -x "ollama" > /dev/null 2>&1; then
    nohup ollama serve > "$HOME/logs/ollama.log" 2>&1 &
    sleep 3
fi
if [ -f "$HOME/KISWARM/tool_proxy.py" ]; then
    source "${HOME}/mem0_env/bin/activate"
    nohup python3 "$HOME/KISWARM/tool_proxy.py" \
        > "$HOME/logs/tool_proxy.log" 2>&1 &
fi
echo "[$(date)] All KISWARM services started" >> "$HOME/logs/kiswarm_boot.log"
EOF
    chmod +x "${KISWARM_DIR}/start_all_services.sh"
    log_success "Created start_all_services.sh"

    # Backup cleanup (prevents disk bloat)
    cat > "${KISWARM_HOME}/cleanup_old_backups.sh" << 'EOF'
#!/bin/bash
# KISWARM Maintenance Engine — runs daily at 1 AM via cron
find "${HOME}/backups" -name "*.tar.gz" -mtime +30 -delete 2>/dev/null || true
find "${HOME}/logs"    -name "*.log"    -mtime +60 -delete 2>/dev/null || true
echo "[$(date)] Backup rotation complete" >> "${HOME}/logs/maintenance.log"
EOF
    chmod +x "${KISWARM_HOME}/cleanup_old_backups.sh"
    log_success "Created cleanup_old_backups.sh"

    # Shell aliases
    grep -q "sys-nav" ~/.bashrc 2>/dev/null || {
        echo "alias sys-nav='bash ${KISWARM_HOME}/system_navigation.sh'"        >> ~/.bashrc
        echo "alias kiswarm-status='python3 ${KISWARM_HOME}/kiswarm_status.py'" >> ~/.bashrc
        echo "alias kiswarm-health='bash ${KISWARM_HOME}/health_check.sh'
        echo "alias sentinel-extract='bash ${KISWARM_HOME}/KISWARM/../scripts/sentinel_trigger.sh extract'" >> ~/.bashrc
        echo "alias sentinel-search='bash ${KISWARM_HOME}/KISWARM/../scripts/sentinel_trigger.sh search'" >> ~/.bashrc
        echo "alias sentinel-status='bash ${KISWARM_HOME}/KISWARM/../scripts/sentinel_trigger.sh status'" >> ~/.bashrc"      >> ~/.bashrc
    }
    log_success "Aliases added to ~/.bashrc"
}

phase_memory_systems() {
    log_info "PHASE 6: INITIALIZING QDRANT WITH PROPER COLLECTIONS"
    # shellcheck disable=SC1091
    source "${VENV_PATH}/bin/activate"
    python3 - << 'PYEOF'
import os
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
path = os.path.expanduser("~/KISWARM/qdrant_data")
os.makedirs(path, exist_ok=True)
client = QdrantClient(path=path)
for name in ["memories", "tools", "awareness", "context"]:
    try:
        client.get_collection(name)
        print(f"  ✓ Collection '{name}' already exists")
    except Exception:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE)
        )
        print(f"  ✓ Created collection '{name}'")
print("Qdrant fully initialized")
PYEOF
    log_success "Memory systems initialized"
}

phase_ollama_setup() {
    log_info "PHASE 7: OLLAMA + MODEL VERIFICATION"
    if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
        log_warning "Starting Ollama daemon..."
        nohup ollama serve > "${KISWARM_HOME}/logs/ollama.log" 2>&1 &
        sleep 4
    fi
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        log_success "Ollama running on :11434"
        MODEL_COUNT=$(curl -s http://localhost:11434/api/tags \
            | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo "0")
        log_success "Models available: $MODEL_COUNT"
    else
        log_error "Ollama failed to respond"
        return 1
    fi
}

phase_governance() {
    log_info "PHASE 10: GOVERNANCE MODE ACTIVATION"

    cat > "${KISWARM_HOME}/governance_config.json" << 'GEOF'
{
  "system_name": "KISWARM",
  "version": "1.1",
  "governance_mode": "active",
  "autonomous_operation": true,
  "auto_restart_services": true,
  "memory_consolidation_interval": 100,
  "awareness_injection_enabled": true,
  "tool_injection_enabled": true,
  "max_context_tokens": 3500,
  "audit_logging": true,
  "auto_backup_interval_hours": 6,
  "health_check_interval_minutes": 30,
  "swarm_enabled": true,
  "distributed_learning": true,
  "backup_retention_days": 30,
  "log_retention_days": 60
}
GEOF
    log_success "Governance config created"

    # Systemd service (portable)
    if command -v systemctl &>/dev/null; then
        cat > /tmp/kiswarm.service << SVCEOF
[Unit]
Description=KISWARM Swarm Intelligence System
After=network.target

[Service]
Type=forking
User=$(whoami)
ExecStart=${KISWARM_DIR}/start_all_services.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
        sudo cp /tmp/kiswarm.service /etc/systemd/system/kiswarm.service 2>/dev/null \
            && sudo systemctl daemon-reload 2>/dev/null \
            && log_success "Systemd service configured" \
            || log_warning "Systemd config skipped (add manually if needed)"
    fi

    # Initial backup
    mkdir -p "${KISWARM_HOME}/backups"
    tar -czf "${KISWARM_HOME}/backups/initial_$(date +%Y%m%d).tar.gz" \
        "${KISWARM_DIR}/qdrant_data" 2>/dev/null \
        && log_success "Initial backup created" || true
}

main() {
    clear
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║  🌟 KISWARM v1.1 — PRODUCTION DEPLOYMENT  🌟        ║"
    echo "║  https://github.com/Baronki2/KISWARM                ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    log_info "User: $(whoami) | Home: $KISWARM_HOME"
    log_info "Log: $LOG_FILE"
    echo ""

    phase_prerequisites
    phase_directories
    phase_venv
    phase_scripts
    phase_memory_systems
    phase_ollama_setup
    phase_governance

    echo ""
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║  ✅ KISWARM v1.1 DEPLOYMENT COMPLETE!               ║"
    echo "╠══════════════════════════════════════════════════════╣"
    echo "║  1. source ~/.bashrc    — activate aliases           ║"
    echo "║  2. kiswarm-health      — verify all systems         ║"
    echo "║  3. ollama pull llama2  — get your first model       ║"
    echo "║  4. sys-nav             — open the control hub       ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    log_success "Full log: $LOG_FILE"
}

main "$@"
