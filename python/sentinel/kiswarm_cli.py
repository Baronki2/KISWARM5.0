#!/usr/bin/env python3
"""
KISWARM v4.8 — Module 49: kiswarm-cli
=======================================
The command-line interface for the KISWARM mesh network.
Runs as a standalone daemon AND as an interactive CLI tool.

Parallel to the main Sentinel API (port 11436), the CLI daemon
runs on port 11440 and manages all peer-to-peer operations.

Usage:
  kiswarm-cli status              # System + all peers
  kiswarm-cli peer list           # Active peer connections
  kiswarm-cli peer add <addr>     # Add a peer manually
  kiswarm-cli peer remove <addr>  # Remove a peer
  kiswarm-cli peer scan           # Scan local subnet (opt-in)
  kiswarm-cli sync                # Pull fixes from all peers
  kiswarm-cli heal                # Run SysAdmin agent
  kiswarm-cli ask "<question>"    # Ask the mesh (forwards to peer with answer)
  kiswarm-cli gossip fix          # Broadcast a fix to the mesh
  kiswarm-cli daemon start        # Start peer daemon in background
  kiswarm-cli daemon stop         # Stop peer daemon
  kiswarm-cli daemon status       # Daemon health

Architecture:
  kiswarm-cli daemon → SwarmPeer + GossipProtocol + PeerDiscovery
  kiswarm-cli <cmd>  → talks to daemon via local socket or directly
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import signal
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CLI_VERSION   = "4.8"
DAEMON_PORT   = 11440   # Peer protocol port
CONTROL_PORT  = 11441   # Local control socket (CLI → daemon)
PID_FILE      = os.path.expanduser("~/.kiswarm/cli.pid")
LOG_FILE      = os.path.expanduser("~/logs/kiswarm-cli.log")
SENTINEL_API  = "http://localhost:11436"


# ─────────────────────────────────────────────────────────────────────────────
# COLOR OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    DIM    = "\033[2m"

def _c(color: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{C.RESET}"

def ok(msg: str)    -> None: print(f"  {_c(C.GREEN, '✓')} {msg}")
def err(msg: str)   -> None: print(f"  {_c(C.RED, '✗')} {msg}")
def info(msg: str)  -> None: print(f"  {_c(C.CYAN, 'ℹ')} {msg}")
def warn(msg: str)  -> None: print(f"  {_c(C.YELLOW, '⚠')} {msg}")
def head(msg: str)  -> None: print(f"\n{_c(C.BOLD + C.WHITE, msg)}")
def dim(msg: str)   -> None: print(f"  {_c(C.DIM, msg)}")


# ─────────────────────────────────────────────────────────────────────────────
# DAEMON CONTROL
# ─────────────────────────────────────────────────────────────────────────────

class KISWARMDaemon:
    """
    The background peer daemon.
    Manages SwarmPeer + GossipProtocol + PeerDiscovery.
    Exposes a local control socket for CLI commands.
    """

    def __init__(self):
        self._peer:      Optional[Any] = None   # SwarmPeer
        self._gossip:    Optional[Any] = None   # GossipProtocol
        self._discovery: Optional[Any] = None   # PeerDiscovery
        self._running    = False
        self._node_id    = self._load_or_create_node_id()

    def _load_or_create_node_id(self) -> str:
        id_file = os.path.expanduser("~/.kiswarm/node_id")
        os.makedirs(os.path.dirname(id_file), exist_ok=True)
        if os.path.exists(id_file):
            with open(id_file) as f:
                return f.read().strip()
        import uuid
        nid = str(uuid.uuid4())[:16]
        with open(id_file, "w") as f:
            f.write(nid)
        return nid

    def start(self) -> None:
        """Start the daemon — called in background process."""
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [CLI] %(levelname)s — %(message)s",
            handlers=[
                logging.FileHandler(LOG_FILE),
                logging.StreamHandler(),
            ]
        )

        logger.info(f"KISWARM CLI Daemon starting — node_id={self._node_id}")

        # Import mesh modules
        from .swarm_peer      import SwarmPeer
        from .gossip_protocol import GossipProtocol
        from .peer_discovery  import PeerDiscovery

        os_fam = self._detect_os()

        # Wire up the mesh
        self._gossip    = GossipProtocol(node_id=self._node_id,
                                          on_new_fix=self._on_new_fix,
                                          on_upgrade=self._on_upgrade)
        self._peer      = SwarmPeer(node_id=self._node_id, port=DAEMON_PORT,
                                     os_family=os_fam,
                                     on_gossip=self._gossip.receive)
        self._discovery = PeerDiscovery(node_id=self._node_id,
                                         on_discovered=self._on_peer_discovered)

        # Connect gossip to peer broadcaster
        self._gossip.set_broadcaster(self._peer.broadcast_gossip)

        # Start peer server
        self._peer.start()

        # Reconnect known peers
        known = self._discovery.get_known_peers()
        if known:
            logger.info(f"Reconnecting to {len(known)} known peers...")
            for addr, port in known:
                self._peer.connect(addr, port)

        # Write PID
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        self._running = True

        # Start control socket
        threading.Thread(target=self._control_loop, daemon=True, name="cli-control").start()

        logger.info(f"KISWARM CLI Daemon ready — peer port={DAEMON_PORT} control={CONTROL_PORT}")

        # Keep alive
        try:
            while self._running:
                time.sleep(5)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        if self._peer:
            self._peer.stop()
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        logger.info("KISWARM CLI Daemon stopped")

    def _detect_os(self) -> str:
        if platform.system() == "Darwin": return "macos"
        try:
            with open("/etc/os-release") as f:
                content = f.read().lower()
            if "ubuntu" in content or "debian" in content: return "debian"
            if "fedora" in content or "rhel" in content: return "redhat"
            if "arch" in content: return "arch"
        except Exception:
            pass
        return "unknown"

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_peer_discovered(self, address: str, port: int) -> None:
        if self._peer:
            self._peer.connect(address, port)

    def _on_new_fix(self, fix_data: Dict[str, Any]) -> None:
        logger.info(f"New fix received via mesh: {fix_data.get('fix_id')}")

    def _on_upgrade(self, version: str) -> None:
        logger.info(f"Upgrade signal: v{version} available — run: git -C ~/KISWARM pull")

    # ── Control socket ────────────────────────────────────────────────────────

    def _control_loop(self) -> None:
        """Local Unix-style control interface for CLI commands."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", CONTROL_PORT))
            srv.listen(5)
            srv.settimeout(2.0)
        except Exception as e:
            logger.error(f"Control socket failed: {e}")
            return

        while self._running:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=self._handle_control, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_control(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            data = conn.recv(4096)
            if not data:
                return
            cmd = json.loads(data.decode())
            result = self._dispatch(cmd)
            conn.sendall((json.dumps(result) + "\n").encode())
        except Exception as e:
            try:
                conn.sendall(json.dumps({"error": str(e)}).encode())
            except Exception:
                pass
        finally:
            conn.close()

    def _dispatch(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        action = cmd.get("action")

        if action == "status":
            return {
                "node_id":  self._node_id,
                "version":  CLI_VERSION,
                "running":  self._running,
                "peer":     self._peer.status() if self._peer else {},
                "gossip":   self._gossip.stats() if self._gossip else {},
                "discovery": self._discovery.stats() if self._discovery else {},
            }

        elif action == "peer_list":
            peers = self._peer.list_peers() if self._peer else []
            return {"peers": [p.to_dict() for p in peers]}

        elif action == "peer_add":
            addr = cmd.get("address", "")
            port = cmd.get("port", DAEMON_PORT)
            if self._discovery:
                self._discovery.register_manual(addr, port)
            ok = self._peer.connect(addr, port) if self._peer else False
            return {"connected": ok, "address": addr, "port": port}

        elif action == "peer_remove":
            addr = cmd.get("address", "")
            port = cmd.get("port", DAEMON_PORT)
            if self._discovery:
                self._discovery.remove_peer(addr, port)
            return {"removed": True}

        elif action == "peer_scan":
            subnet = cmd.get("subnet")
            found = self._discovery.scan_subnet(subnet) if self._discovery else []
            return {"found": len(found), "peers": [f"{a}:{p}" for a,p in found]}

        elif action == "sync":
            # Request peer lists from all active peers
            count = self._peer.broadcast_gossip({
                "type": "sync_request", "node_id": self._node_id
            }) if self._peer else 0
            return {"synced_peers": count}

        elif action == "gossip_fix":
            fix = cmd.get("fix", {})
            if self._gossip and fix:
                item = self._gossip.gossip_fix(fix)
                return {"gossip_id": item.gossip_id, "ttl": item.ttl}
            return {"error": "no fix provided"}

        elif action == "gossip_upgrade":
            version = cmd.get("version", "")
            if self._gossip and version:
                item = self._gossip.gossip_upgrade(version, cmd.get("changelog", ""))
                return {"gossip_id": item.gossip_id}
            return {"error": "no version provided"}

        elif action == "stop":
            threading.Thread(target=self.stop, daemon=True).start()
            return {"stopped": True}

        return {"error": f"Unknown action: {action}"}


# ─────────────────────────────────────────────────────────────────────────────
# CLI CLIENT (sends commands to daemon via control socket)
# ─────────────────────────────────────────────────────────────────────────────

def _send_to_daemon(action: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Send a command to the running daemon."""
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=3) as s:
            cmd = {"action": action, **kwargs}
            s.sendall(json.dumps(cmd).encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            return json.loads(data.decode())
    except ConnectionRefusedError:
        return None
    except Exception as e:
        return {"error": str(e)}


def _daemon_running() -> bool:
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # signal 0 = check if alive
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_status(args) -> None:
    head("KISWARM CLI — System Status")

    # Daemon status
    if not _daemon_running():
        warn("Daemon nicht aktiv — starte mit: kiswarm-cli daemon start")
        return

    result = _send_to_daemon("status")
    if not result:
        err("Daemon antwortet nicht")
        return

    info(f"Node ID:  {result.get('node_id', '?')}")
    info(f"Version:  v{result.get('version', '?')}")

    peer_info = result.get("peer", {})
    active    = peer_info.get("active_peers", 0)
    max_p     = peer_info.get("max_peers", 5)
    ok(f"Aktive Peers: {active}/{max_p}")

    gossip = result.get("gossip", {})
    info(f"Gossip gesehen: {gossip.get('items_seen', 0)} Signaturen")

    peers = peer_info.get("peers", [])
    if peers:
        head("Verbundene Peers:")
        for p in peers:
            state = "✓" if p.get("is_alive") else "✗"
            print(f"    {state} {p.get('address')}:{p.get('port')} "
                  f"| v{p.get('kiswarm_version', '?')} "
                  f"| {p.get('fixes_known', 0)} fixes"
                  f"| {p.get('os_family', '?')}")
    else:
        warn("Keine aktiven Peers — füge einen hinzu: kiswarm-cli peer add <addr>")


def cmd_peer(args) -> None:
    sub = args.sub

    if sub == "list":
        if not _daemon_running():
            warn("Daemon nicht aktiv"); return
        result = _send_to_daemon("peer_list")
        peers = result.get("peers", []) if result else []
        if not peers:
            info("Keine aktiven Peers")
            return
        head(f"Peers ({len(peers)}):")
        for p in peers:
            alive = "AKTIV" if p.get("is_alive") else "TOT"
            print(f"  [{alive}] {p['address']}:{p['port']} "
                  f"v{p.get('kiswarm_version','?')} "
                  f"OS:{p.get('os_family','?')} "
                  f"fixes:{p.get('fixes_known',0)}")

    elif sub == "add":
        addr = args.address
        port = getattr(args, "port", DAEMON_PORT)
        if not _daemon_running():
            warn("Daemon nicht aktiv — starte zuerst: kiswarm-cli daemon start"); return
        result = _send_to_daemon("peer_add", address=addr, port=port)
        if result and result.get("connected"):
            ok(f"Verbunden mit {addr}:{port}")
        else:
            warn(f"Verbindung zu {addr}:{port} fehlgeschlagen — Peer offline?")

    elif sub == "remove":
        addr = args.address
        result = _send_to_daemon("peer_remove", address=addr)
        ok(f"Peer {addr} entfernt")

    elif sub == "scan":
        if not _daemon_running():
            warn("Daemon nicht aktiv"); return
        subnet = getattr(args, "subnet", None)
        info(f"Scanne Subnetz {subnet or 'auto'}...")
        result = _send_to_daemon("peer_scan", subnet=subnet)
        if result:
            ok(f"Gefunden: {result.get('found', 0)} KISWARM Nodes")
            for addr in result.get("peers", []):
                print(f"    • {addr}")


def cmd_sync(args) -> None:
    if not _daemon_running():
        warn("Daemon nicht aktiv"); return
    info("Synchronisiere mit Peers...")
    result = _send_to_daemon("sync")
    if result:
        ok(f"Sync-Request an {result.get('synced_peers', 0)} Peers gesendet")
    # Also try GitHub track
    info("Auch GitHub-Track synchronisieren...")
    try:
        from .feedback_channel import FeedbackChannel
        ch = FeedbackChannel()
        fixes = ch.load_known_fixes(force_refresh=True)
        ok(f"GitHub-Track: {len(fixes)} fixes geladen")
    except Exception as e:
        warn(f"GitHub-Track: {e}")


def cmd_heal(args) -> None:
    info("Starte SysAdmin Agent...")
    try:
        from .sysadmin_agent import SysAdminAgent
        agent  = SysAdminAgent()
        report = agent.run_full_cycle()
        d = report.to_dict()
        head(f"Diagnose abgeschlossen — {d['overall_health'].upper()}")
        info(f"Score: {d['score']:.0%}")
        info(f"Findings: {d['findings_count']} | Healed: {d['healed_count']} | Unresolved: {d['unresolved_count']}")
        for u in d.get("unresolved", []):
            warn(f"Ungelöst: {u['title']}")
    except Exception as e:
        err(f"SysAdmin Agent Fehler: {e}")


def cmd_ask(args) -> None:
    question = " ".join(args.question)
    # Try local advisor first
    try:
        import requests
        r = requests.post(f"{SENTINEL_API}/advisor/ask",
                          json={"question": question}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            head("Antwort (lokal):")
            print(f"  {data.get('answer', 'Keine Antwort')}")
            return
    except Exception:
        pass
    # Try peers via daemon
    if _daemon_running():
        info("Frage wird an Peers weitergeleitet...")
        # Simplified — in production would broadcast and collect responses
        warn("Peer-basierte Fragen in v4.9 verfügbar")
    else:
        warn("Kein lokaler Sentinel und kein Daemon — starte einen der beiden")


def cmd_daemon(args) -> None:
    sub = args.sub

    if sub == "start":
        if _daemon_running():
            ok("Daemon läuft bereits")
            return
        info("Starte KISWARM CLI Daemon...")
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "-m", "python.sentinel.kiswarm_cli", "_daemon_run"],
            stdout=open(LOG_FILE, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(1)
        if _daemon_running():
            ok(f"Daemon gestartet (PID {proc.pid}) auf Port {DAEMON_PORT}")
        else:
            err("Daemon Start fehlgeschlagen — prüfe ~/logs/kiswarm-cli.log")

    elif sub == "stop":
        if not _daemon_running():
            warn("Daemon läuft nicht")
            return
        result = _send_to_daemon("stop")
        time.sleep(1)
        if not _daemon_running():
            ok("Daemon gestoppt")
        else:
            warn("Daemon reagiert nicht — kill manuell via PID in ~/.kiswarm/cli.pid")

    elif sub == "status":
        if _daemon_running():
            ok(f"Daemon läuft | Port {DAEMON_PORT} | Control {CONTROL_PORT}")
            r = _send_to_daemon("status")
            if r:
                info(f"Peers: {r.get('peer', {}).get('active_peers', 0)}/{r.get('peer', {}).get('max_peers', 5)}")
        else:
            warn("Daemon nicht aktiv")
            dim(f"Log: {LOG_FILE}")
            dim(f"Start: kiswarm-cli daemon start")


def cmd_gossip(args) -> None:
    sub = args.sub

    if sub == "fix":
        fixes_file = os.path.expanduser("~/KISWARM/experience/known_fixes.json")
        if not os.path.exists(fixes_file):
            err("known_fixes.json nicht gefunden"); return
        with open(fixes_file) as f:
            data = json.load(f)
        fixes = data.get("fixes", [])
        if not fixes:
            warn("Keine Fixes vorhanden"); return
        fix = fixes[-1]  # Broadcast latest fix
        result = _send_to_daemon("gossip_fix", fix=fix)
        if result and result.get("gossip_id"):
            ok(f"Fix {fix.get('fix_id')} ins Mesh gebroadcastet (TTL={result.get('ttl', 4)})")
        else:
            err("Gossip fehlgeschlagen — Daemon aktiv?")

    elif sub == "upgrade":
        version = getattr(args, "version", CLI_VERSION)
        result = _send_to_daemon("gossip_upgrade", version=version)
        if result and result.get("gossip_id"):
            ok(f"Upgrade-Signal v{version} ins Mesh gesendet")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Banner
    if len(sys.argv) == 1:
        print(f"\n{_c(C.BOLD+C.CYAN, 'KISWARM CLI')} v{CLI_VERSION} — Mesh Network Interface\n")
        print("  kiswarm-cli status            System + Peers")
        print("  kiswarm-cli peer add <addr>   Peer hinzufügen")
        print("  kiswarm-cli peer list         Aktive Peers")
        print("  kiswarm-cli peer scan         Subnetz scannen")
        print("  kiswarm-cli sync              Mit Peers synchronisieren")
        print("  kiswarm-cli heal              SysAdmin Agent ausführen")
        print("  kiswarm-cli ask '<frage>'     KI-Frage an Advisor")
        print("  kiswarm-cli gossip fix        Fix ins Mesh senden")
        print("  kiswarm-cli daemon start|stop|status")
        print()
        return

    parser = argparse.ArgumentParser(prog="kiswarm-cli")
    sub    = parser.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("sync")
    sub.add_parser("heal")

    p_peer = sub.add_parser("peer")
    pp     = p_peer.add_subparsers(dest="sub")
    pp.add_parser("list")
    p_add = pp.add_parser("add");    p_add.add_argument("address"); p_add.add_argument("--port", type=int, default=DAEMON_PORT)
    p_rm  = pp.add_parser("remove"); p_rm.add_argument("address")
    p_sc  = pp.add_parser("scan");   p_sc.add_argument("--subnet", default=None)

    p_ask = sub.add_parser("ask"); p_ask.add_argument("question", nargs="+")

    p_dmn = sub.add_parser("daemon")
    pd    = p_dmn.add_subparsers(dest="sub")
    pd.add_parser("start"); pd.add_parser("stop"); pd.add_parser("status")

    p_gos = sub.add_parser("gossip")
    pg    = p_gos.add_subparsers(dest="sub")
    pg.add_parser("fix")
    p_up  = pg.add_parser("upgrade"); p_up.add_argument("--version", default=CLI_VERSION)

    # Internal daemon runner
    sub.add_parser("_daemon_run")

    args = parser.parse_args()

    dispatch = {
        "status":  cmd_status,
        "peer":    cmd_peer,
        "sync":    cmd_sync,
        "heal":    cmd_heal,
        "ask":     cmd_ask,
        "daemon":  cmd_daemon,
        "gossip":  cmd_gossip,
        "_daemon_run": lambda _: KISWARMDaemon().start(),
    }

    fn = dispatch.get(args.cmd)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
