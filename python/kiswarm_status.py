#!/usr/bin/env python3
"""
KISWARM v1.1 - Production-Grade Status Monitor
Real-time dashboard using Rich UI library
Usage: kiswarm-status  OR  python3 kiswarm_status.py
"""

import os
import sys
import json
import datetime
import subprocess
from pathlib import Path

try:
    import psutil
    import requests
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    print("Installing required packages...")
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "rich", "psutil", "requests"], check=True)
    import psutil
    import requests
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

console = Console()

KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR  = os.path.join(KISWARM_HOME, "KISWARM")


class KISWARMMonitor:
    """Production-grade real-time system monitor"""

    def __init__(self):
        self.start_time = datetime.datetime.now()

    def ollama_status(self):
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=2)
            models = r.json().get("models", [])
            names = [m.get("name", "?") for m in models[:4]]
            return {
                "status": "✓ Running",
                "color":  "green",
                "models": len(models),
                "names":  ", ".join(names) + ("..." if len(models) > 4 else ""),
            }
        except Exception:
            return {"status": "✗ Offline", "color": "red", "models": 0, "names": "none"}

    def memory_status(self):
        path = os.path.join(KISWARM_DIR, "qdrant_data")
        if not os.path.exists(path):
            return {"status": "✗ Not initialized", "color": "red", "size": "0B", "collections": 0}
        # Bounded traversal — stop counting at 500 files to avoid hanging on huge DBs
        size_bytes = 0
        try:
            for i, f in enumerate(Path(path).rglob("*")):
                if i > 500:
                    break
                if f.is_file():
                    size_bytes += f.stat().st_size
        except OSError:
            size_bytes = 0
        size_mb = size_bytes / 1_048_576
        try:
            from qdrant_client import QdrantClient  # noqa: PLC0415
            c = QdrantClient(path=path)
            cols = len(c.get_collections().collections)
            return {"status": "✓ Connected", "color": "green",
                    "size": f"{size_mb:.1f}MB", "collections": cols}
        except Exception:
            return {"status": "⚠ Accessible", "color": "yellow",
                    "size": f"{size_mb:.1f}MB", "collections": "?"}

    def proxy_status(self):
        try:
            requests.get("http://localhost:11435/health", timeout=2)
            return {"status": "✓ Running", "color": "green"}
        except Exception:
            return {"status": "✗ Offline", "color": "red"}

    def resources(self):
        """Return CPU, memory, and disk usage. All errors return safe defaults."""
        cpu   = psutil.cpu_percent(interval=None)  # non-blocking cached value
        mem   = psutil.virtual_memory()

        # Disk — try KISWARM_HOME first, fall back to /, then a safe mock
        disk_pct, disk_free = 0.0, "?"
        for path in (KISWARM_HOME, "/"):
            try:
                d = psutil.disk_usage(path)
                disk_pct  = d.percent
                disk_free = f"{d.free // 1_073_741_824}GB"
                break
            except (FileNotFoundError, PermissionError, OSError):
                continue

        def color(p):
            return "green" if p < 60 else ("yellow" if p < 80 else "red")

        return {
            "cpu":  {"pct": cpu, "color": color(cpu)},
            "mem":  {"pct": mem.percent, "color": color(mem.percent),
                     "used": f"{mem.used // 1_073_741_824}GB",
                     "total": f"{mem.total // 1_073_741_824}GB"},
            "disk": {"pct": disk_pct, "color": color(disk_pct), "free": disk_free},
        }

    def governance_status(self):
        cfg_path = os.path.join(KISWARM_HOME, "governance_config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            return {"exists": True, "mode": cfg.get("governance_mode", "?"),
                    "autonomous": cfg.get("autonomous_operation", False),
                    "version": cfg.get("version", "?")}
        except Exception:
            return {"exists": False}

    def render(self):
        console.clear()
        console.print(Panel(
            "[bold cyan]🌟 KISWARM v1.1 GOVERNANCE MONITOR — github.com/Baronki2/KISWARM[/bold cyan]",
            border_style="cyan", padding=(0, 2)
        ))

        # System components table
        t = Table(title="[bold blue]System Components[/bold blue]", expand=True)
        t.add_column("Component", style="cyan", min_width=20)
        t.add_column("Status", min_width=15)
        t.add_column("Details", style="yellow")

        o = self.ollama_status()
        t.add_row("Ollama Server :11434",
                  f"[{o['color']}]{o['status']}[/{o['color']}]",
                  f"Models: {o['models']}  ({o['names']})")

        m = self.memory_status()
        t.add_row("Memory (Qdrant)",
                  f"[{m['color']}]{m['status']}[/{m['color']}]",
                  f"Size: {m['size']}  Collections: {m['collections']}")

        p = self.proxy_status()
        t.add_row("Tool Proxy :11435",
                  f"[{p['color']}]{p['status']}[/{p['color']}]",
                  "Auto-injection proxy")

        console.print(t)
        console.print()

        # Resources table
        r = Table(title="[bold blue]System Resources[/bold blue]", expand=True)
        r.add_column("Resource", style="cyan", min_width=12)
        r.add_column("Usage", min_width=25)
        r.add_column("Status", min_width=8)

        res = self.resources()
        icon = lambda p: "✓" if p < 80 else "⚠"  # noqa: E731
        r.add_row("CPU",
                  f"{res['cpu']['pct']:.1f}%",
                  f"[{res['cpu']['color']}]{icon(res['cpu']['pct'])}[/{res['cpu']['color']}]")
        r.add_row("Memory",
                  f"{res['mem']['pct']:.1f}%  ({res['mem']['used']}/{res['mem']['total']})",
                  f"[{res['mem']['color']}]{icon(res['mem']['pct'])}[/{res['mem']['color']}]")
        r.add_row("Disk (free)",
                  f"{res['disk']['free']}  ({res['disk']['pct']:.0f}% used)",
                  f"[{res['disk']['color']}]{icon(res['disk']['pct'])}[/{res['disk']['color']}]")

        console.print(r)
        console.print()

        # Governance
        gov = self.governance_status()
        if gov.get("exists"):
            g = Text()
            g.append("Governance: ", style="white")
            g.append(gov["mode"].upper(), style="bold cyan")
            g.append(f"  |  Autonomous: {'Yes' if gov['autonomous'] else 'No'}",
                     style="yellow")
            g.append(f"  |  v{gov['version']}", style="dim")
        else:
            g = Text("⚠ Governance config not found — run deploy script", style="yellow")
        console.print(Panel(g, border_style="cyan",
                             title="[bold blue]Governance Status[/bold blue]"))

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        console.print(f"\n[dim]Updated: {ts}  |  Ctrl+C to exit[/dim]")


def main():
    monitor = KISWARMMonitor()
    try:
        while True:
            monitor.render()
            import time
            time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped.[/yellow]")


if __name__ == "__main__":
    main()
