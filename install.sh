#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  KISWARM v4.6 — One-Click Installer                                     ║
# ║  curl -fsSL https://raw.githubusercontent.com/Baronki2/KISWARM/main/install.sh | bash  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Fully autonomous — scans your system, adapts, installs, verifies.
# No configuration needed. Works on Ubuntu, Debian, Fedora, Arch, macOS.
#
# Usage:
#   One-line:  curl -fsSL https://raw.githubusercontent.com/Baronki2/KISWARM/main/install.sh | bash
#   From repo: ./install.sh
#   Dry run:   KISWARM_DRY_RUN=1 ./install.sh
#   Custom dir: KISWARM_DIR=/opt/kiswarm ./install.sh

set -euo pipefail

# ── Configuration (overridable via environment) ───────────────────────────
KISWARM_REPO="${KISWARM_REPO:-https://github.com/Baronki2/KISWARM.git}"
KISWARM_DIR="${KISWARM_DIR:-$HOME/KISWARM}"
KISWARM_VENV="${KISWARM_VENV:-$HOME/mem0_env}"
KISWARM_LOG="${KISWARM_LOG:-$HOME/kiswarm_install.log}"
KISWARM_DRY_RUN="${KISWARM_DRY_RUN:-0}"
KISWARM_BRANCH="${KISWARM_BRANCH:-main}"

# ── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; WHITE='\033[1;37m'; NC='\033[0m'

# ── Logging ───────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}[KISWARM]${NC} $1" | tee -a "$KISWARM_LOG"; }
ok()      { echo -e "${GREEN}  ✓${NC} $1"       | tee -a "$KISWARM_LOG"; }
warn()    { echo -e "${YELLOW}  ⚠${NC} $1"      | tee -a "$KISWARM_LOG"; }
err()     { echo -e "${RED}  ✗${NC} $1"         | tee -a "$KISWARM_LOG"; }
step()    { echo -e "\n${WHITE}━━ $1${NC}"       | tee -a "$KISWARM_LOG"; }
dryrun()  { [[ "$KISWARM_DRY_RUN" == "1" ]] && echo -e "${YELLOW}[DRY]${NC} $1" && return 0 || return 1; }

mkdir -p "$(dirname "$KISWARM_LOG")"
touch "$KISWARM_LOG"

# ── Banner ────────────────────────────────────────────────────────────────
clear
echo -e "${CYAN}"
cat << 'BANNER'
  ██╗  ██╗██╗███████╗██╗    ██╗ █████╗ ██████╗ ███╗   ███╗
  ██║ ██╔╝██║██╔════╝██║    ██║██╔══██╗██╔══██╗████╗ ████║
  █████╔╝ ██║███████╗██║ █╗ ██║███████║██████╔╝██╔████╔██║
  ██╔═██╗ ██║╚════██║██║███╗██║██╔══██║██╔══██╗██║╚██╔╝██║
  ██║  ██╗██║███████║╚███╔███╔╝██║  ██║██║  ██║██║ ╚═╝ ██║
  ╚═╝  ╚═╝╚═╝╚══════╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝
  v4.6 — Autonomous One-Click Installer
  Architect: Baron Marco Paolo Ialongo
BANNER
echo -e "${NC}"
echo ""

[[ "$KISWARM_DRY_RUN" == "1" ]] && warn "DRY RUN MODE — no changes will be made"
log "Install log: $KISWARM_LOG"
log "Target directory: $KISWARM_DIR"
echo ""

# ── PHASE 1: SYSTEM SCAN ─────────────────────────────────────────────────
step "Phase 1: System Intelligence Scan"

OS="unknown"
DISTRO="unknown"
DISTRO_VER="unknown"
PKG_MGR="unknown"
INIT_SYS="unknown"
ARCH=$(uname -m)
IS_CONTAINER=0

# Detect OS
if [[ "$(uname)" == "Linux" ]]; then
    OS="Linux"
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release 2>/dev/null || true
        DISTRO="${ID:-unknown}"
        DISTRO_VER="${VERSION_ID:-unknown}"
    fi
    # Container detection
    [[ -f /.dockerenv ]] && IS_CONTAINER=1
    if [[ -f /proc/1/cgroup ]]; then
        grep -qE "docker|lxc|kubepods" /proc/1/cgroup 2>/dev/null && IS_CONTAINER=1 || true
    fi
elif [[ "$(uname)" == "Darwin" ]]; then
    OS="macOS"
    DISTRO="macos"
    DISTRO_VER=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
fi

# Package manager
for pm_pair in "apt:apt" "dnf:dnf" "yum:yum" "pacman:pacman" "zypper:zypper" "brew:brew"; do
    pm="${pm_pair%%:*}"; cmd="${pm_pair##*:}"
    command -v "$cmd" &>/dev/null && { PKG_MGR="$pm"; break; } || true
done

# Init system
command -v systemctl &>/dev/null && INIT_SYS="systemd" || true
command -v rc-status  &>/dev/null && INIT_SYS="openrc"  || true

# RAM
RAM_GB=0
if [[ -f /proc/meminfo ]]; then
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$(( RAM_KB / 1024 / 1024 ))
elif command -v sysctl &>/dev/null; then
    RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    RAM_GB=$(( RAM_BYTES / 1024 / 1024 / 1024 ))
fi

# Disk
DISK_FREE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {gsub("G","",$4); print $4}' || echo "?")

# CPU
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "?")

ok "OS:         $OS ($DISTRO $DISTRO_VER) [$ARCH]"
ok "CPU:        $CPU_CORES cores"
ok "RAM:        ${RAM_GB}GB"
ok "Disk Free:  ${DISK_FREE_GB}GB"
ok "Pkg Mgr:    $PKG_MGR"
ok "Init:       $INIT_SYS"
[[ "$IS_CONTAINER" == "1" ]] && warn "Container detected — systemd will be skipped"

# Model recommendation
MODEL="qwen2.5:3b"
[[ "$RAM_GB" -ge 32 ]] && MODEL="qwen2.5:14b"
[[ "$RAM_GB" -ge 16 ]] && [[ "$RAM_GB" -lt 32 ]] && MODEL="qwen2.5:7b"
[[ "$RAM_GB" -lt  8 ]] && MODEL="qwen2.5:0.5b"
ok "Recommended model: $MODEL"

# Readiness check
BLOCKED=0
[[ "$RAM_GB" -lt 4 ]]       && err "RAM ${RAM_GB}GB < 4GB minimum" && BLOCKED=1 || true
[[ "${DISK_FREE_GB}" != "?" ]] && [[ "$DISK_FREE_GB" -lt 10 ]] && err "Free disk < 10GB" && BLOCKED=1 || true
command -v python3 &>/dev/null || { err "python3 not found"; BLOCKED=1; }

if [[ "$BLOCKED" == "1" ]] && [[ "$KISWARM_DRY_RUN" != "1" ]]; then
    err "System-Check fehlgeschlagen — Installation blockiert"
    err "Bitte Probleme lösen und erneut versuchen"
    exit 1
fi

echo ""

# ── PHASE 2: DEPENDENCIES ─────────────────────────────────────────────────
step "Phase 2: Abhängigkeiten installieren"

install_pkgs() {
    local pkgs="$1"
    dryrun "Would install: $pkgs" && return
    case "$PKG_MGR" in
        apt)    sudo apt-get update -qq && sudo apt-get install -y $pkgs ;;
        dnf)    sudo dnf install -y $pkgs ;;
        yum)    sudo yum install -y $pkgs ;;
        pacman) sudo pacman -S --noconfirm $pkgs ;;
        brew)   brew install $pkgs ;;
        *)      warn "Unbekannter Package-Manager: $pkgs manuell installieren" ;;
    esac
}

MISSING_PKGS=""
for pkg in "git" "python3" "curl"; do
    command -v "$pkg" &>/dev/null || MISSING_PKGS="$MISSING_PKGS $pkg"
done

if [[ -n "$MISSING_PKGS" ]]; then
    log "Installiere:$MISSING_PKGS"
    case "$PKG_MGR" in
        apt)    install_pkgs "python3 python3-pip python3-venv git curl" ;;
        dnf|yum) install_pkgs "python3 python3-pip python3-venv git curl" ;;
        pacman) install_pkgs "python python-pip git curl" ;;
        *)      warn "Bitte manuell installieren: python3 git curl" ;;
    esac
fi

# python3-venv
python3 -m venv --help &>/dev/null || {
    log "Installiere python3-venv..."
    case "$PKG_MGR" in
        apt) install_pkgs "python3-venv python3-dev build-essential" ;;
        dnf|yum) install_pkgs "python3-devel gcc" ;;
    esac
}

ok "Abhängigkeiten bereit"
echo ""

# ── PHASE 3: OLLAMA ──────────────────────────────────────────────────────
step "Phase 3: Ollama installieren & starten"

if ! command -v ollama &>/dev/null; then
    log "Installiere Ollama..."
    dryrun "Would run: curl -fsSL https://ollama.com/install.sh | sh" || \
        curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installiert"
else
    ok "Ollama bereits installiert: $(ollama --version 2>/dev/null || echo 'version unknown')"
fi

# Start Ollama
if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
    log "Starte Ollama..."
    mkdir -p "$HOME/logs"
    dryrun "Would start ollama serve" || {
        nohup ollama serve > "$HOME/logs/ollama.log" 2>&1 &
        sleep 4
    }
fi

if curl -s http://localhost:11434/api/tags &>/dev/null; then
    ok "Ollama läuft auf Port 11434"
else
    warn "Ollama nicht erreichbar — weiter..."
fi
echo ""

# ── PHASE 4: CLONE REPOSITORY ────────────────────────────────────────────
step "Phase 4: KISWARM Repository klonen"

if [[ -d "$KISWARM_DIR/.git" ]]; then
    ok "Repository bereits vorhanden — update..."
    dryrun "Would run: git pull" || {
        cd "$KISWARM_DIR" && git pull --ff-only 2>/dev/null || warn "git pull fehlgeschlagen — nutze vorhandene Version"
    }
else
    log "Klone $KISWARM_REPO → $KISWARM_DIR"
    dryrun "Would run: git clone $KISWARM_REPO $KISWARM_DIR" || \
        git clone --depth=1 --branch "$KISWARM_BRANCH" "$KISWARM_REPO" "$KISWARM_DIR"
    ok "Repository geklont"
fi
echo ""

# ── PHASE 5: PYTHON ENVIRONMENT ──────────────────────────────────────────
step "Phase 5: Python Virtual Environment"

dryrun "Would create venv at $KISWARM_VENV" || {
    if [[ ! -d "$KISWARM_VENV" ]]; then
        python3 -m venv "$KISWARM_VENV"
        ok "Virtual Environment erstellt: $KISWARM_VENV"
    else
        ok "Virtual Environment bereits vorhanden"
    fi

    # Install packages
    log "Installiere Python-Pakete..."
    source "$KISWARM_VENV/bin/activate"
    pip install --upgrade pip --quiet

    # Core packages
    PACKAGES="ollama mem0 qdrant-client flask flask-cors rich psutil requests numpy"
    pip install $PACKAGES --quiet 2>&1 | grep -E "Successfully|already|ERROR" | head -20 || true

    # Optional packages (don't fail if unavailable)
    pip install chromadb tiktoken 2>/dev/null | grep -E "Successfully|already" || true

    ok "Python-Pakete installiert"
}
echo ""

# ── PHASE 6: MODEL DOWNLOAD ──────────────────────────────────────────────
step "Phase 6: KI-Modell herunterladen ($MODEL)"

if curl -s http://localhost:11434/api/tags &>/dev/null; then
    MODELS_JSON=$(curl -s http://localhost:11434/api/tags)
    if echo "$MODELS_JSON" | grep -q "$MODEL" 2>/dev/null; then
        ok "Modell $MODEL bereits vorhanden"
    else
        log "Lade $MODEL herunter (das kann 5-15 Minuten dauern)..."
        dryrun "Would run: ollama pull $MODEL" || ollama pull "$MODEL"
        ok "Modell $MODEL geladen"
    fi
else
    warn "Ollama nicht erreichbar — Modell-Download übersprungen"
    warn "Später ausführen: ollama pull $MODEL"
fi
echo ""

# ── PHASE 7: DIRECTORY STRUCTURE ─────────────────────────────────────────
step "Phase 7: Verzeichnisstruktur"

dryrun "Would create directories" || {
    mkdir -p "$HOME/logs" "$HOME/backups"
    mkdir -p "$KISWARM_DIR/sentinel_data"/{soul_mirror,evolution_vault,immortality,repo_cache,advisor_sessions}
    chmod -R 755 "$KISWARM_DIR" 2>/dev/null || true
    ok "Verzeichnisse erstellt"
}

# ── PHASE 8: GOVERNANCE CONFIG ───────────────────────────────────────────
dryrun "Would create governance_config.json" || {
if [[ ! -f "$HOME/governance_config.json" ]]; then
cat > "$HOME/governance_config.json" << GOVEOF
{
  "system_name":                "KISWARM",
  "version":                    "4.6",
  "governance_mode":            "active",
  "autonomous_operation":       true,
  "auto_restart_services":      true,
  "memory_consolidation_interval": 100,
  "awareness_injection_enabled": true,
  "tool_injection_enabled":     true,
  "max_context_tokens":         3500,
  "audit_logging":              true,
  "auto_backup_interval_hours": 6,
  "health_check_interval_minutes": 30,
  "swarm_enabled":              true,
  "distributed_learning":       true,
  "backup_retention_days":      30,
  "installer_agent_enabled":    true,
  "advisor_api_enabled":        true,
  "recommended_model":          "$MODEL"
}
GOVEOF
    ok "governance_config.json erstellt"
fi
}
echo ""

# ── PHASE 9: SHELL ALIASES ───────────────────────────────────────────────
step "Phase 9: Shell-Aliases konfigurieren"

ALIAS_BLOCK="
# KISWARM Aliases (auto-generated by installer v4.6)
alias sys-nav='bash $KISWARM_DIR/system_navigation.sh 2>/dev/null || echo \"sys-nav: Datei nicht gefunden\"'
alias kiswarm-health='bash $HOME/health_check.sh 2>/dev/null || python3 $KISWARM_DIR/python/sentinel/sentinel_api.py --health 2>/dev/null || echo \"kiswarm-health: nicht verfügbar\"'
alias kiswarm-status='python3 $HOME/kiswarm_status.py 2>/dev/null || curl -s http://localhost:11436/health | python3 -m json.tool'
alias kiswarm-start='source $KISWARM_VENV/bin/activate && cd $KISWARM_DIR && nohup python python/sentinel/sentinel_api.py > $HOME/logs/kiswarm.log 2>&1 &'
alias kiswarm-advisor='curl -s http://localhost:11436/advisor/stats | python3 -m json.tool'
alias kiswarm-scan='curl -s http://localhost:11436/installer/scan | python3 -m json.tool'
"

dryrun "Would add aliases to ~/.bashrc" || {
    if ! grep -q "KISWARM Aliases" "$HOME/.bashrc" 2>/dev/null; then
        echo "$ALIAS_BLOCK" >> "$HOME/.bashrc"
        ok "Aliases zu ~/.bashrc hinzugefügt"
    else
        ok "Aliases bereits in ~/.bashrc"
    fi
}
echo ""

# ── PHASE 10: SYSTEMD (wenn verfügbar) ───────────────────────────────────
step "Phase 10: Service-Konfiguration"

if [[ "$INIT_SYS" == "systemd" ]] && [[ "$IS_CONTAINER" == "0" ]]; then
    dryrun "Would create systemd service" || {
sudo tee /etc/systemd/system/kiswarm.service > /dev/null << SVCEOF
[Unit]
Description=KISWARM Swarm Intelligence System v4.6
After=network.target

[Service]
Type=forking
User=$USER
WorkingDirectory=$KISWARM_DIR
ExecStart=$KISWARM_DIR/python/sentinel/sentinel_api.py
Environment=KISWARM_HOME=$HOME
Environment=PATH=$KISWARM_VENV/bin:/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=10
StandardOutput=append:$HOME/logs/kiswarm.log
StandardError=append:$HOME/logs/kiswarm_err.log

[Install]
WantedBy=multi-user.target
SVCEOF
        sudo systemctl daemon-reload
        ok "Systemd-Service erstellt"
    }

    # Build custom Ollama model if Ollama available
    if command -v ollama &>/dev/null && [[ -f "$KISWARM_DIR/ollama/Modelfile" ]]; then
        log "Erstelle kiswarm-installer Ollama-Modell..."
        dryrun "Would run: ollama create kiswarm-installer" || {
            cd "$KISWARM_DIR"
            ollama create kiswarm-installer -f ollama/Modelfile 2>/dev/null && \
                ok "kiswarm-installer Modell erstellt" || \
                warn "kiswarm-installer Modell konnte nicht erstellt werden (base model nicht vorhanden)"
        }
    fi
else
    warn "Kein systemd / Container — direkter Prozessstart wird verwendet"
fi
echo ""

# ── PHASE 11: START SERVICES ─────────────────────────────────────────────
step "Phase 11: Services starten"

dryrun "Would start KISWARM Sentinel API" || {
    if ! curl -s http://localhost:11436/health &>/dev/null; then
        log "Starte KISWARM Sentinel API..."
        source "$KISWARM_VENV/bin/activate"
        cd "$KISWARM_DIR"
        nohup python python/sentinel/sentinel_api.py > "$HOME/logs/kiswarm.log" 2>&1 &
        sleep 3
    fi
}
echo ""

# ── PHASE 12: VERIFICATION ───────────────────────────────────────────────
step "Phase 12: Installation verifizieren"

CHECKS_PASSED=0
CHECKS_TOTAL=0
check_service() {
    local name="$1" url="$2"
    CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
    if curl -s "$url" &>/dev/null; then
        ok "$name ✓"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))
    else
        warn "$name ✗ (möglicherweise noch nicht gestartet)"
    fi
}

check_service "Ollama (Port 11434)"         "http://localhost:11434/api/tags"
check_service "KISWARM API (Port 11436)"    "http://localhost:11436/health"

CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
[[ -d "$KISWARM_DIR/.git" ]] && { ok "Repository vorhanden ✓"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); } || warn "Repository fehlt"

CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
[[ -f "$KISWARM_VENV/bin/python" ]] && { ok "Python venv ✓"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); } || warn "Python venv fehlt"

echo ""

# ── FINAL SUMMARY ────────────────────────────────────────────────────────
echo -e "${WHITE}"
cat << 'SUMMARY'
╔═══════════════════════════════════════════════════════════════════════╗
║           KISWARM v4.6 — Installation Abgeschlossen                  ║
╚═══════════════════════════════════════════════════════════════════════╝
SUMMARY
echo -e "${NC}"

echo -e "${GREEN}Checks:  $CHECKS_PASSED/$CHECKS_TOTAL passed${NC}"
echo ""
echo -e "${CYAN}Wichtige Befehle:${NC}"
echo "  source ~/.bashrc           → Aliases aktivieren"
echo "  kiswarm-health             → 40+ System-Checks"
echo "  kiswarm-status             → Live-Dashboard"
echo "  kiswarm-scan               → System-Scan via API"
echo "  kiswarm-advisor            → Advisor-Status"
echo ""
echo -e "${CYAN}Direkte API-Zugriffe:${NC}"
echo "  curl http://localhost:11436/health"
echo "  curl http://localhost:11436/installer/scan"
echo "  curl http://localhost:11436/advisor/stats"
echo ""
echo -e "${CYAN}Ollama:${NC}"
echo "  ollama list                → Verfügbare Modelle"
echo "  ollama run kiswarm-installer  → KISWARM-Berater starten"
echo "  ollama run $MODEL          → Allgemeines Modell"
echo ""
echo -e "${CYAN}Dokumentation:${NC}"
echo "  https://github.com/Baronki2/KISWARM"
echo ""
echo "  Install-Log: $KISWARM_LOG"
echo ""

[[ "$KISWARM_DRY_RUN" == "1" ]] && warn "Dies war ein DRY RUN — keine Änderungen wurden vorgenommen"

exit 0
