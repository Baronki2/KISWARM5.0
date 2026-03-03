#!/bin/bash
################################################################################
# KISWARM v1.1 - CENTRAL SYSTEM NAVIGATION HUB
# Usage: sys-nav  OR  bash system_navigation.sh
################################################################################

KISWARM_HOME="${KISWARM_HOME:-$HOME}"

while true; do
    clear
    echo -e "\e[1;36m╔═══════════════════════════════════════════════════════╗\e[0m"
    echo -e "\e[1;36m║        🚀 KISWARM CENTRAL GOVERNANCE HUB              ║\e[0m"
    echo -e "\e[1;36m║        https://github.com/Baronki2/KISWARM             ║\e[0m"
    echo -e "\e[1;36m╚═══════════════════════════════════════════════════════╝\e[0m"
    echo ""
    echo -e "  1) \e[1;32mDASHBOARD\e[0m    — Real-time System Monitor"
    echo -e "  2) \e[1;32mHEALTH\e[0m       — Run Deep Diagnostics (40+ checks)"
    echo -e "  3) \e[1;32mMODELS\e[0m        — List / Pull Ollama Models"
    echo -e "  4) \e[1;32mSERVICES\e[0m     — Start / Stop Core Services"
    echo -e "  5) \e[1;32mBACKUP\e[0m        — Manual System Snapshot"
    echo -e "  6) \e[1;32mMAINTENANCE\e[0m  — Run Backup Rotation Now"
    echo -e "  7) \e[1;32mLOGS\e[0m          — Tail System Logs"
    echo -e "  8) \e[1;32mGOVERNANCE\e[0m   — View/Edit Config"
    echo -e "  q) \e[1;31mEXIT\e[0m"
    echo ""
    read -rp "  Select [1-8, q]: " opt

    case $opt in
        1)
            if [ -f "${KISWARM_HOME}/kiswarm_status.py" ]; then
                python3 "${KISWARM_HOME}/kiswarm_status.py"
            else
                echo "Status monitor not found. Run deployment first."
                read -rp "Press enter to continue" _
            fi
            ;;
        2)
            bash "${KISWARM_HOME}/health_check.sh" 2>/dev/null \
                || bash "${KISWARM_HOME}/scripts/health_check.sh" 2>/dev/null \
                || echo "Health check not found"
            read -rp "Press enter to continue" _
            ;;
        3)
            echo ""
            echo "Available models:"
            ollama list 2>/dev/null || echo "Ollama not running"
            echo ""
            read -rp "Pull a model (name or Enter to skip): " model_name
            [ -n "$model_name" ] && ollama pull "$model_name"
            read -rp "Press enter to continue" _
            ;;
        4)
            echo ""
            echo "  1) Start All Services"
            echo "  2) Stop Ollama"
            echo "  3) Start Ollama Only"
            read -rp "  Choice: " sopt
            case $sopt in
                1) bash "${KISWARM_HOME}/KISWARM/start_all_services.sh" && echo "✓ Services started" ;;
                2) pkill -f "ollama serve" 2>/dev/null && echo "✓ Ollama stopped" || echo "Not running" ;;
                3) nohup ollama serve > "${KISWARM_HOME}/logs/ollama.log" 2>&1 & echo "✓ Ollama starting..." ;;
            esac
            sleep 2
            ;;
        5)
            BACKUP_FILE="${KISWARM_HOME}/backups/manual_$(date +%Y%m%d_%H%M%S).tar.gz"
            mkdir -p "${KISWARM_HOME}/backups"
            tar -czf "$BACKUP_FILE" "${KISWARM_HOME}/KISWARM/" 2>/dev/null \
                && echo "✓ Backup saved: $BACKUP_FILE" \
                || echo "✗ Backup failed"
            read -rp "Press enter to continue" _
            ;;
        6)
            bash "${KISWARM_HOME}/cleanup_old_backups.sh" && echo "✓ Maintenance complete"
            read -rp "Press enter to continue" _
            ;;
        7)
            echo "Showing last 30 lines of logs (Ctrl+C to stop)..."
            tail -f "${KISWARM_HOME}/logs/"*.log 2>/dev/null || echo "No logs found"
            ;;
        8)
            if command -v nano &>/dev/null; then
                nano "${KISWARM_HOME}/governance_config.json"
            else
                cat "${KISWARM_HOME}/governance_config.json"
                read -rp "Press enter to continue" _
            fi
            ;;
        q|Q) echo "Goodbye!"; exit 0 ;;
        *) echo "Invalid option"; sleep 1 ;;
    esac
done
