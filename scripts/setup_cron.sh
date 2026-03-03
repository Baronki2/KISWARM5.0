#!/bin/bash
# KISWARM v1.1 - One-Click Automation Setup
# Run AFTER deployment to enable full autonomous operation

echo "Setting up KISWARM automation (cron + systemd)..."

# 1. Daily backup rotation at 1 AM
(crontab -l 2>/dev/null | grep -v cleanup_old_backups; \
 echo "0 1 * * * bash \$HOME/cleanup_old_backups.sh >> \$HOME/logs/maintenance.log 2>&1") | crontab -
echo "✓ Cron: Daily backup rotation at 01:00"

# 2. Health check every 30 minutes
(crontab -l 2>/dev/null | grep -v health_check; \
 echo "*/30 * * * * bash \$HOME/health_check.sh >> \$HOME/logs/health_cron.log 2>&1") | crontab -
echo "✓ Cron: Health check every 30 minutes"

# 3. Daily model verification at 6 AM
(crontab -l 2>/dev/null | grep -v "api/tags"; \
 echo "0 6 * * * curl -s http://localhost:11434/api/tags > /dev/null && echo OK >> \$HOME/logs/model_check.log || echo FAIL >> \$HOME/logs/model_check.log") | crontab -
echo "✓ Cron: Model verification daily at 06:00"

echo ""
echo "Active cron jobs:"
crontab -l
echo ""
echo "✅ Automation setup complete!"
echo "   To enable systemd auto-start:"
echo "   sudo cp config/kiswarm.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now kiswarm"
