#!/bin/bash
################################################################################
# KISWARM v1.1 - COMPREHENSIVE HEALTH CHECK SYSTEM
# Usage: kiswarm-health  OR  bash health_check.sh
# Runs 40+ checks across: dirs, python, ollama, memory, tools, disk, resources
################################################################################

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
KISWARM_HOME="${KISWARM_HOME:-$HOME}"
KISWARM_DIR="${KISWARM_HOME}/KISWARM"
VENV_PATH="${KISWARM_HOME}/mem0_env"
PASSED=0; FAILED=0; WARNINGS=0

check_pass() { echo -e "${GREEN}✓${NC} $1"; PASSED=$((PASSED+1)); }
check_fail() { echo -e "${RED}✗${NC} $1"; FAILED=$((FAILED+1)); }
check_warn() { echo -e "${YELLOW}⚠${NC} $1"; WARNINGS=$((WARNINGS+1)); }
header()     { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

clear
echo -e "${CYAN}╔════════════════════════════════════════════════════╗"
echo    "║     🏥 KISWARM v1.1 HEALTH CHECK SYSTEM           ║"
echo -e "╚════════════════════════════════════════════════════╝${NC}"

header "1. SYSTEM DIRECTORIES"
[ -d "$KISWARM_HOME" ]                       && check_pass "KISWARM Home: $KISWARM_HOME"          || check_fail "KISWARM Home"
[ -d "$KISWARM_DIR" ]                        && check_pass "KISWARM Dir: $KISWARM_DIR"            || check_fail "KISWARM Dir"
[ -d "$KISWARM_DIR/qdrant_data" ]            && check_pass "Qdrant Data"                          || check_fail "Qdrant Data"
[ -d "$KISWARM_DIR/central_tools_pool" ]     && check_pass "Central Tools Pool"                   || check_warn "Central Tools Pool"
[ -d "$KISWARM_HOME/logs" ]                  && check_pass "Logs Directory"                       || check_warn "Logs Directory"
[ -d "$KISWARM_HOME/backups" ]               && check_pass "Backups Directory"                    || check_warn "Backups Directory"
[ -f "$KISWARM_HOME/governance_config.json" ] && check_pass "Governance Config"                   || check_warn "Governance Config"

header "2. PYTHON ENVIRONMENT"
[ -d "$VENV_PATH" ] && check_pass "Virtual Env: $VENV_PATH" || check_fail "Virtual Env"
[ -f "${VENV_PATH}/bin/python" ] && {
    PY_VER=$("${VENV_PATH}/bin/python" --version 2>&1)
    check_pass "Python: $PY_VER"
}
for pkg in ollama mem0 qdrant_client rich flask psutil; do
    # shellcheck disable=SC1091
    source "${VENV_PATH}/bin/activate" 2>/dev/null
    python3 -c "import $pkg" 2>/dev/null && check_pass "Package: $pkg" || check_fail "Package: $pkg"
done

header "3. OLLAMA SERVER"
command -v ollama &>/dev/null && check_pass "Ollama installed" || check_fail "Ollama not found"
if curl -s http://localhost:11434/api/tags &>/dev/null; then
    check_pass "Ollama Server :11434 — Running"
    MODEL_COUNT=$(curl -s http://localhost:11434/api/tags \
        | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo "?")
    check_pass "Models available: $MODEL_COUNT"
else
    check_fail "Ollama Server :11434 — Offline"
    check_warn "Fix: ollama serve"
fi

header "4. MEMORY SYSTEMS (QDRANT)"
if [ -d "${KISWARM_DIR}/qdrant_data" ]; then
    check_pass "Qdrant storage directory exists"
    QDRANT_SIZE=$(du -sh "${KISWARM_DIR}/qdrant_data" 2>/dev/null | cut -f1)
    check_pass "Qdrant storage size: ${QDRANT_SIZE:-0B}"
    # shellcheck disable=SC1091
    source "${VENV_PATH}/bin/activate" 2>/dev/null
    COLS=$(python3 - << 'PYEOF' 2>/dev/null
try:
    from qdrant_client import QdrantClient
    import os
    c = QdrantClient(path=os.path.expanduser("~/KISWARM/qdrant_data"))
    print(len(c.get_collections().collections))
except:
    print(-1)
PYEOF
)
    [ "${COLS:-0}" -ge 0 ] 2>/dev/null && check_pass "Qdrant connected ($COLS collections)" || check_warn "Qdrant connection issue"
else
    check_fail "Qdrant data directory missing"
fi

header "5. TOOL INJECTION PROXY"
curl -s http://localhost:11435/health &>/dev/null \
    && check_pass "Tool Proxy :11435 — Running" \
    || { check_warn "Tool Proxy — Offline"; check_warn "Fix: python3 \$HOME/KISWARM/tool_proxy.py &"; }

header "6. SERVICES & PROCESSES"
pgrep -x "ollama" > /dev/null && check_pass "Ollama process running" || check_fail "Ollama process not found"
pgrep -f "tool_proxy.py" > /dev/null && check_pass "Tool proxy process" || check_warn "Tool proxy not running"

header "7. DISK SPACE"
DISK_USED=$(df "$KISWARM_HOME" 2>/dev/null | tail -1 | awk '{print int($5)}' || echo 0)
DISK_FREE=$(df -h "$KISWARM_HOME" 2>/dev/null | tail -1 | awk '{print $4}')
echo "   Home: ${DISK_FREE} free (${DISK_USED}% used)"
[ "$DISK_USED" -lt 80 ] && check_pass "Disk space adequate" \
    || { [ "$DISK_USED" -lt 90 ] && check_warn "Disk ${DISK_USED}% — monitor closely" \
    || check_fail "CRITICAL: Disk ${DISK_USED}%!"; }

header "8. SYSTEM RESOURCES"
MEM_PCT=$(free 2>/dev/null | awk 'NR==2 {printf "%.0f", ($3/$2)*100}' || echo 0)
MEM_INFO=$(free -h 2>/dev/null | awk 'NR==2 {print $3"/"$2}' || echo "N/A")
CPU_LOAD=$(uptime 2>/dev/null | awk -F'load average:' '{print $2}' | cut -d, -f1 | xargs || echo "N/A")
echo "   RAM: $MEM_INFO (${MEM_PCT}%)"
echo "   CPU Load: $CPU_LOAD"
[ "${MEM_PCT:-0}" -lt 85 ] && check_pass "Memory available" || check_warn "High memory: ${MEM_PCT}%"

header "9. CONFIGURATION"
if [ -f "${KISWARM_HOME}/governance_config.json" ]; then
    GOV_MODE=$(python3 -c "import json;print(json.load(open('${KISWARM_HOME}/governance_config.json')).get('governance_mode','?'))" 2>/dev/null || echo "?")
    [ "$GOV_MODE" = "active" ] && check_pass "Governance mode: ACTIVE" || check_warn "Governance mode: $GOV_MODE"
fi
grep -q "sys-nav" ~/.bashrc 2>/dev/null && check_pass "Shell aliases configured" || check_warn "Aliases not found in ~/.bashrc"

header "HEALTH CHECK SUMMARY"
TOTAL=$((PASSED+FAILED+WARNINGS))
[ $TOTAL -eq 0 ] && TOTAL=1
SCORE=$(( (PASSED * 100) / TOTAL ))
echo ""
echo -e "${GREEN}Passed:   $PASSED${NC}"
echo -e "${RED}Failed:   $FAILED${NC}"
echo -e "${YELLOW}Warnings: $WARNINGS${NC}"
echo ""
echo "Health Score: ${SCORE}%"
echo ""
if [ $FAILED -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ ALL SYSTEMS OPERATIONAL${NC}"
elif [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}⚠ SYSTEM OPERATIONAL WITH WARNINGS${NC}"
else
    echo -e "${RED}✗ CRITICAL ISSUES DETECTED — review above${NC}"
fi
echo -e "\nHealth check completed: $(date)"
