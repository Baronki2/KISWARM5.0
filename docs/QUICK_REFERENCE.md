# ⚡ KISWARM v1.1 — Quick Reference

**Repo:** https://github.com/Baronki2/KISWARM

---

## 🚀 Deploy in 4 Commands

```bash
git clone https://github.com/Baronki2/KISWARM.git
cd KISWARM
chmod +x deploy/kiswarm_deploy.sh
./deploy/kiswarm_deploy.sh
source ~/.bashrc && kiswarm-health
```

---

## 🎓 Master Commands

| Command | Function |
|---------|----------|
| `sys-nav` | Central control hub |
| `kiswarm-status` | Live monitoring dashboard |
| `kiswarm-health` | 40+ diagnostic checks |
| `ollama list` | Show available models |
| `ollama pull llama2` | Download a model |
| `ollama run llama2 "prompt"` | Run model |

---

## 📁 Key Directories

| Path | Purpose |
|------|---------|
| `~/KISWARM/` | Main system |
| `~/KISWARM/qdrant_data/` | Vector memory DB |
| `~/mem0_env/` | Python virtualenv |
| `~/logs/` | All system logs |
| `~/backups/` | Auto snapshots |
| `~/governance_config.json` | Configuration |

---

## 🔌 Key Ports

| Port | Service |
|------|---------|
| 11434 | Ollama LLM Server |
| 11435 | Tool Injection Proxy |

---

## ⚙️ Enable Full Automation

```bash
# Cron jobs (backup rotation + health checks)
bash scripts/setup_cron.sh

# Systemd auto-start on boot
sed -i "s|REPLACE_WITH_USERNAME|$(whoami)|g; \
        s|REPLACE_WITH_HOME|$HOME|g" config/kiswarm.service
sudo cp config/kiswarm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kiswarm
```

---

## 🚨 Troubleshooting

| Problem | Fix |
|---------|-----|
| Ollama offline | `ollama serve` |
| Missing packages | `source ~/mem0_env/bin/activate && pip install --upgrade ollama mem0 qdrant-client` |
| Disk full | `bash ~/cleanup_old_backups.sh` |
| Check everything | `kiswarm-health` |
| View logs | `tail -f ~/logs/*.log` |

---

## 🌐 Pull Models

```bash
ollama pull llama2          # General purpose
ollama pull qwen2.5:7b      # Fast & capable
ollama pull deepseek-r1:8b  # Reasoning
ollama pull phi3:mini       # Lightweight (2.6GB)
```
