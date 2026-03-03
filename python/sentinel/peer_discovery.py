"""
KISWARM v4.8 — Module 48: Peer Discovery
==========================================
How nodes find each other without any central registry.

Three parallel discovery strategies (all run simultaneously):
  1. Manual   — operator adds peer via CLI: `kiswarm-cli peer add <addr>`
  2. Subnet   — scans local network for KISWARM nodes (configurable, opt-in)
  3. Gossip   — peers share their peer lists (handled by GossipProtocol)

The result: a self-organizing mesh that requires zero central infrastructure.
If node A knows node B, and node B knows nodes C and D,
then node A will discover C and D automatically via gossip.

Industrial network design principle:
  "Every node that can be found, will be found.
   Every node that wants to hide, stays hidden."
  → Subnet scan is OFF by default, must be explicitly enabled.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

PEER_PORT       = 11440
SCAN_TIMEOUT    = 1.0    # seconds per IP
SCAN_WORKERS    = 50     # parallel scan threads
DISCOVERY_FILE  = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "sentinel_data", "discovered_peers.json"
)


def _is_kiswarm_node(address: str, port: int = PEER_PORT, timeout: float = SCAN_TIMEOUT) -> bool:
    """Check if a host is running a KISWARM peer on the given port."""
    try:
        with socket.create_connection((address, port), timeout=timeout) as s:
            # KISWARM peers respond to a quick probe
            s.sendall(b'{"type":"probe","payload":{}}\n')
            s.settimeout(timeout)
            data = s.recv(256)
            return b"handshake" in data or b"kiswarm" in data.lower()
    except Exception:
        return False


class PeerDiscovery:
    """
    Multi-strategy peer discovery for the KISWARM mesh.
    Runs in the background, calls on_discovered when a new peer is found.
    """

    def __init__(
        self,
        node_id:        str,
        on_discovered:  Optional[Callable[[str, int], None]] = None,
        subnet_scan:    bool = False,   # OFF by default — must be explicitly enabled
    ):
        self.node_id       = node_id
        self.on_discovered = on_discovered
        self.subnet_scan   = subnet_scan

        self._known:   Set[str]   = set()   # "addr:port" already discovered
        self._running: bool       = False
        self._lock                = threading.Lock()

        self._load_known()

    # ── Strategy 1: Manual registration ──────────────────────────────────────

    def register_manual(self, address: str, port: int = PEER_PORT) -> bool:
        """Operator manually adds a peer. Called by kiswarm-cli peer add."""
        addr_str = f"{address}:{port}"
        with self._lock:
            if addr_str in self._known:
                return False
            self._known.add(addr_str)

        self._save_known()
        logger.info(f"[Discovery] Manual registration: {addr_str}")
        if self.on_discovered:
            self.on_discovered(address, port)
        return True

    def remove_peer(self, address: str, port: int = PEER_PORT) -> bool:
        addr_str = f"{address}:{port}"
        with self._lock:
            if addr_str not in self._known:
                return False
            self._known.discard(addr_str)
        self._save_known()
        return True

    # ── Strategy 2: Subnet scan (opt-in) ─────────────────────────────────────

    def scan_subnet(
        self,
        subnet: Optional[str] = None,
        port:   int = PEER_PORT,
    ) -> List[Tuple[str, int]]:
        """
        Scan local subnet for KISWARM nodes.
        Must be explicitly called — never runs automatically.
        subnet: CIDR notation e.g. "192.168.1.0/24" — auto-detected if None
        """
        if subnet is None:
            subnet = self._detect_local_subnet()
        if not subnet:
            logger.warning("[Discovery] Could not detect local subnet")
            return []

        logger.info(f"[Discovery] Scanning subnet {subnet} port {port}...")
        found: List[Tuple[str, int]] = []

        try:
            network = ipaddress.ip_network(subnet, strict=False)
            hosts   = list(network.hosts())
        except ValueError as e:
            logger.error(f"[Discovery] Invalid subnet {subnet}: {e}")
            return []

        # Get our own IP to skip
        our_ip = self._get_local_ip()

        results: List[Tuple[str, int]] = []
        lock    = threading.Lock()

        def probe(ip: str) -> None:
            if ip == our_ip:
                return
            if _is_kiswarm_node(ip, port):
                with lock:
                    results.append((ip, port))
                logger.info(f"[Discovery] Found KISWARM node: {ip}:{port}")

        # Parallel scan
        threads = []
        for host in hosts:
            ip = str(host)
            t = threading.Thread(target=probe, args=(ip,), daemon=True)
            threads.append(t)
            t.start()
            # Limit concurrency
            active = [tt for tt in threads if tt.is_alive()]
            while len(active) >= SCAN_WORKERS:
                time.sleep(0.05)
                active = [tt for tt in threads if tt.is_alive()]

        # Wait for all to finish
        for t in threads:
            t.join(timeout=SCAN_TIMEOUT + 1)

        # Register discovered peers
        for addr, p in results:
            addr_str = f"{addr}:{p}"
            with self._lock:
                is_new = addr_str not in self._known
                if is_new:
                    self._known.add(addr_str)
            if is_new and self.on_discovered:
                self.on_discovered(addr, p)

        if results:
            self._save_known()

        logger.info(f"[Discovery] Scan complete: {len(results)} nodes found in {subnet}")
        return results

    def _detect_local_subnet(self) -> Optional[str]:
        """Auto-detect the primary local subnet."""
        try:
            # Connect to external to find our outbound interface
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            # Assume /24 subnet
            parts = local_ip.rsplit(".", 1)
            return f"{parts[0]}.0/24"
        except Exception:
            return None

    def _get_local_ip(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    # ── Strategy 3: Gossip-based discovery ───────────────────────────────────

    def on_gossip_peer(self, address: str, port: int) -> bool:
        """Called by GossipProtocol when a PEER_INFO gossip arrives."""
        addr_str = f"{address}:{port}"
        with self._lock:
            if addr_str in self._known:
                return False
            self._known.add(addr_str)

        self._save_known()
        logger.info(f"[Discovery] Gossip discovery: {addr_str}")
        if self.on_discovered:
            self.on_discovered(address, port)
        return True

    # ── Reconnect on startup ──────────────────────────────────────────────────

    def get_known_peers(self) -> List[Tuple[str, int]]:
        """Return all known peers for reconnection on startup."""
        result = []
        with self._lock:
            for addr_str in self._known:
                try:
                    addr, port_str = addr_str.rsplit(":", 1)
                    result.append((addr, int(port_str)))
                except Exception:
                    pass
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_known(self) -> None:
        try:
            os.makedirs(os.path.dirname(DISCOVERY_FILE), exist_ok=True)
            with open(DISCOVERY_FILE, "w") as f:
                import json
                json.dump({
                    "node_id":    self.node_id,
                    "saved_at":   time.time(),
                    "known":      list(self._known),
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"[Discovery] Save failed: {e}")

    def _load_known(self) -> None:
        try:
            if os.path.exists(DISCOVERY_FILE):
                import json
                with open(DISCOVERY_FILE) as f:
                    data = json.load(f)
                self._known = set(data.get("known", []))
                logger.info(f"[Discovery] Loaded {len(self._known)} known peers")
        except Exception:
            self._known = set()

    def stats(self) -> Dict[str, Any]:
        return {
            "known_peers":  len(self._known),
            "subnet_scan":  self.subnet_scan,
            "local_ip":     self._get_local_ip(),
        }
