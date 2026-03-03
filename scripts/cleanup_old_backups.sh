#!/bin/bash
################################################################################
# KISWARM v1.1 - MAINTENANCE ENGINE & BACKUP ROTATION
# Schedule: 0 1 * * * bash ~/cleanup_old_backups.sh  (daily at 1 AM)
# Prevents disk bloat with 30-day backup + 60-day log retention policy
################################################################################
set -euo pipefail

KISWARM_HOME="${KISWARM_HOME:-$HOME}"
BACKUP_DIR="${KISWARM_HOME}/backups"
LOG_DIR="${KISWARM_HOME}/logs"
MAINTENANCE_LOG="${LOG_DIR}/maintenance.log"
BACKUP_RETENTION_DAYS=30
LOG_RETENTION_DAYS=60

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$MAINTENANCE_LOG"; }

log "═══════════════════════════════════════════"
log "KISWARM MAINTENANCE ENGINE STARTED"
log "═══════════════════════════════════════════"

# Phase 1: Clean old backups
if [ -d "$BACKUP_DIR" ]; then
    before=$(find "$BACKUP_DIR" -name "*.tar.gz" 2>/dev/null | wc -l)
    find "$BACKUP_DIR" -type f -name "*.tar.gz" -mtime "+${BACKUP_RETENTION_DAYS}" -delete 2>/dev/null || true
    after=$(find "$BACKUP_DIR" -name "*.tar.gz" 2>/dev/null | wc -l)
    log "✓ Backups: removed $((before-after)), kept $after (${BACKUP_RETENTION_DAYS}-day policy)"
else
    log "⚠ Backup directory not found: $BACKUP_DIR"
fi

# Phase 2: Clean old logs
if [ -d "$LOG_DIR" ]; then
    before=$(find "$LOG_DIR" -name "*.log" 2>/dev/null | wc -l)
    find "$LOG_DIR" -type f -name "*.log" -mtime "+${LOG_RETENTION_DAYS}" -delete 2>/dev/null || true
    after=$(find "$LOG_DIR" -name "*.log" 2>/dev/null | wc -l)
    log "✓ Logs: removed $((before-after)), kept $after (${LOG_RETENTION_DAYS}-day policy)"
fi

# Phase 3: Disk space check
DISK_USED=$(df "$KISWARM_HOME" 2>/dev/null | tail -1 | awk '{print int($5)}' || echo "0")
if [ "$DISK_USED" -gt 90 ]; then
    log "✗ CRITICAL: Disk ${DISK_USED}% used — immediate action required!"
elif [ "$DISK_USED" -gt 80 ]; then
    log "⚠ WARNING: Disk ${DISK_USED}% used — monitor closely"
else
    log "✓ Disk space OK: ${DISK_USED}% used"
fi

log "MAINTENANCE COMPLETE — Next: tomorrow 01:00"
