"""
KISWARM v4.8 — Module 46: SwarmPeer
=====================================
Peer-to-peer connection manager. The foundation of the independent swarm net.

Architecture principle (learned from industrial mesh networks, NOT Silicon Valley):
  - Max 5 peers per node — deliberate, not a limitation
  - Each peer is fully trusted once accepted
  - No central authority, no registration server, no GitHub dependency
  - A node that loses all peers still functions — it just learns slower

Redundancy model (dual-track):
  Track A: GitHub (FeedbackChannel) — broad reach, community scale
  Track B: SwarmPeer (this module) — zero dependency, mesh resilience

Peer state machine:
  DISCONNECTED → CONNECTING → HANDSHAKING → ACTIVE → (DEGRADED) → DISCONNECTED

Wire protocol: JSON over TCP, newline-delimited, port 11440
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

PEER_PORT     = 11440
MAX_PEERS     = 5
HEARTBEAT_S   = 30
PEER_TIMEOUT  = 90     # seconds without heartbeat = dead
CONNECT_TIMEOUT = 5
RECV_TIMEOUT  = 10
PROTOCOL_VER  = "1.0"
KISWARM_VER   = "4.8"


# ─────────────────────────────────────────────────────────────────────────────
# PEER STATE
# ─────────────────────────────────────────────────────────────────────────────

class PeerState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    HANDSHAKING  = "handshaking"
    ACTIVE       = "active"
    DEGRADED     = "degraded"   # Missing heartbeats but not timed out


@dataclass
class PeerInfo:
    """Everything known about a peer node."""
    peer_id:        str          # UUID assigned at first handshake
    address:        str          # IP or hostname
    port:           int
    state:          str          # PeerState value
    kiswarm_version: str
    os_family:      str
    connected_at:   float
    last_heartbeat: float
    last_seen:      float
    fixes_known:    int          # How many fixes this peer has
    capabilities:   List[str]    # ["gossip", "sysadmin", "installer"]
    hops:           int = 0      # How many hops away (0 = direct)

    @property
    def addr_str(self) -> str:
        return f"{self.address}:{self.port}"

    def is_alive(self) -> bool:
        return time.time() - self.last_heartbeat < PEER_TIMEOUT

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_alive"] = self.is_alive()
        d["age_s"] = round(time.time() - self.connected_at, 0)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PeerInfo":
        d.pop("is_alive", None)
        d.pop("age_s", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE TYPES
# ─────────────────────────────────────────────────────────────────────────────

class MsgType(str, Enum):
    HANDSHAKE      = "handshake"       # Initial connection
    HANDSHAKE_ACK  = "handshake_ack"   # Accept connection
    HEARTBEAT      = "heartbeat"       # Keep-alive
    HEARTBEAT_ACK  = "heartbeat_ack"
    PEER_LIST      = "peer_list"       # Share known peers
    GOSSIP         = "gossip"          # Carry a gossip payload
    GOSSIP_ACK     = "gossip_ack"
    DISCONNECT     = "disconnect"      # Graceful goodbye
    ERROR          = "error"


def make_msg(msg_type: MsgType, payload: Dict[str, Any] = None, node_id: str = "") -> bytes:
    msg = {
        "type":      msg_type.value,
        "node_id":   node_id,
        "timestamp": time.time(),
        "payload":   payload or {},
    }
    return (json.dumps(msg) + "\n").encode()


def parse_msg(data: bytes) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(data.decode().strip())
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PEER CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

class PeerConnection:
    """A single active TCP connection to one peer."""

    def __init__(
        self,
        sock: socket.socket,
        address: str,
        port: int,
        node_id: str,
        on_message: Callable[[Dict[str, Any], "PeerConnection"], None],
        on_disconnect: Callable[["PeerConnection"], None],
    ):
        self.sock          = sock
        self.address       = address
        self.port          = port
        self.node_id       = node_id       # Our own node ID
        self.peer_id: Optional[str] = None # Set after handshake
        self.on_message    = on_message
        self.on_disconnect = on_disconnect
        self.state         = PeerState.CONNECTING
        self._running      = True
        self._lock         = threading.Lock()

        # Start receive loop
        self._thread = threading.Thread(
            target=self._recv_loop, daemon=True,
            name=f"peer-{address}:{port}"
        )
        self._thread.start()

    def send(self, msg_type: MsgType, payload: Dict[str, Any] = None) -> bool:
        try:
            with self._lock:
                self.sock.sendall(make_msg(msg_type, payload, self.node_id))
            return True
        except Exception as e:
            logger.debug(f"[Peer] Send failed to {self.address}: {e}")
            self._disconnect()
            return False

    def _recv_loop(self) -> None:
        buf = b""
        self.sock.settimeout(RECV_TIMEOUT)
        while self._running:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = parse_msg(line)
                    if msg:
                        try:
                            self.on_message(msg, self)
                        except Exception as e:
                            logger.debug(f"[Peer] Message handler error: {e}")
            except socket.timeout:
                continue
            except Exception:
                break
        self._disconnect()

    def _disconnect(self) -> None:
        if self._running:
            self._running = False
            try:
                self.sock.close()
            except Exception:
                pass
            self.on_disconnect(self)

    def close(self) -> None:
        self.send(MsgType.DISCONNECT, {"reason": "graceful"})
        self._disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# SWARM PEER MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class SwarmPeer:
    """
    Core peer manager for the KISWARM mesh network.
    Manages up to MAX_PEERS active connections.
    Runs a TCP server to accept incoming peers.
    Runs heartbeat loop to detect dead connections.
    """

    def __init__(
        self,
        node_id:   Optional[str] = None,
        port:      int = PEER_PORT,
        os_family: str = "unknown",
        on_gossip: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.node_id    = node_id or str(uuid.uuid4())[:16]
        self.port       = port
        self.os_family  = os_family
        self.on_gossip  = on_gossip   # callback when gossip arrives

        self._peers:       Dict[str, PeerInfo]       = {}   # peer_id → info
        self._conns:       Dict[str, PeerConnection] = {}   # peer_id → conn
        self._known_addrs: Set[str]                  = set()  # addr:port seen
        self._lock         = threading.Lock()
        self._server_sock: Optional[socket.socket]   = None
        self._running      = False

        # Load persisted peers
        self._storage_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "sentinel_data", "peers.json"
        )
        os.makedirs(os.path.dirname(self._storage_file), exist_ok=True)
        self._load_peers()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start TCP server and background threads."""
        self._running = True
        threading.Thread(target=self._server_loop, daemon=True, name="swarm-server").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="swarm-heartbeat").start()
        logger.info(f"[SwarmPeer] Started — node_id={self.node_id} port={self.port}")

    def stop(self) -> None:
        self._running = False
        for conn in list(self._conns.values()):
            try: conn.close()
            except Exception: pass
        if self._server_sock:
            try: self._server_sock.close()
            except Exception: pass

    # ── Connect to peer ───────────────────────────────────────────────────────

    def connect(self, address: str, port: int = PEER_PORT) -> bool:
        """Initiate connection to a peer."""
        addr_str = f"{address}:{port}"
        with self._lock:
            if len(self._conns) >= MAX_PEERS:
                logger.warning(f"[SwarmPeer] Max peers ({MAX_PEERS}) reached")
                return False
            if addr_str in self._known_addrs:
                logger.debug(f"[SwarmPeer] Already connected to {addr_str}")
                return False

        try:
            sock = socket.create_connection((address, port), timeout=CONNECT_TIMEOUT)
            conn = PeerConnection(
                sock=sock, address=address, port=port,
                node_id=self.node_id,
                on_message=self._on_message,
                on_disconnect=self._on_disconnect,
            )
            # Send handshake
            conn.state = PeerState.HANDSHAKING
            conn.send(MsgType.HANDSHAKE, self._handshake_payload())
            with self._lock:
                self._known_addrs.add(addr_str)
            logger.info(f"[SwarmPeer] Connecting to {addr_str}")
            return True
        except Exception as e:
            logger.warning(f"[SwarmPeer] Connect failed to {addr_str}: {e}")
            return False

    def _handshake_payload(self) -> Dict[str, Any]:
        return {
            "node_id":        self.node_id,
            "kiswarm_version": KISWARM_VER,
            "protocol_ver":   PROTOCOL_VER,
            "os_family":      self.os_family,
            "port":           self.port,
            "capabilities":   ["gossip", "sysadmin", "installer", "experience"],
            "peer_count":     len(self._conns),
            "fixes_known":    self._count_known_fixes(),
        }

    # ── Send gossip ───────────────────────────────────────────────────────────

    def broadcast_gossip(self, payload: Dict[str, Any]) -> int:
        """Send gossip to all active peers. Returns how many received it."""
        sent = 0
        with self._lock:
            conns = list(self._conns.values())
        for conn in conns:
            if conn.send(MsgType.GOSSIP, payload):
                sent += 1
        return sent

    def send_to_peer(self, peer_id: str, msg_type: MsgType,
                     payload: Dict[str, Any] = None) -> bool:
        with self._lock:
            conn = self._conns.get(peer_id)
        if conn:
            return conn.send(msg_type, payload)
        return False

    # ── Server loop ───────────────────────────────────────────────────────────

    def _server_loop(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.bind(("0.0.0.0", self.port))
            self._server_sock.listen(10)
            self._server_sock.settimeout(2.0)
            logger.info(f"[SwarmPeer] Listening on :{self.port}")
        except Exception as e:
            logger.error(f"[SwarmPeer] Server bind failed: {e}")
            return

        while self._running:
            try:
                sock, addr = self._server_sock.accept()
                with self._lock:
                    if len(self._conns) >= MAX_PEERS:
                        sock.close()
                        continue
                conn = PeerConnection(
                    sock=sock, address=addr[0], port=addr[1],
                    node_id=self.node_id,
                    on_message=self._on_message,
                    on_disconnect=self._on_disconnect,
                )
                logger.info(f"[SwarmPeer] Incoming connection from {addr[0]}:{addr[1]}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"[SwarmPeer] Server error: {e}")

    # ── Heartbeat loop ────────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        while self._running:
            time.sleep(HEARTBEAT_S)
            now = time.time()
            with self._lock:
                conns = list(self._conns.items())
            for peer_id, conn in conns:
                conn.send(MsgType.HEARTBEAT, {"ts": now})
                # Check timeout
                peer = self._peers.get(peer_id)
                if peer and not peer.is_alive():
                    logger.warning(f"[SwarmPeer] Peer {peer_id} timed out")
                    conn.close()

    # ── Message handler ───────────────────────────────────────────────────────

    def _on_message(self, msg: Dict[str, Any], conn: PeerConnection) -> None:
        mtype   = msg.get("type")
        payload = msg.get("payload", {})
        node_id = msg.get("node_id", "")

        if mtype == MsgType.HANDSHAKE:
            self._handle_handshake(msg, conn)

        elif mtype == MsgType.HANDSHAKE_ACK:
            self._handle_handshake_ack(msg, conn)

        elif mtype in (MsgType.HEARTBEAT, MsgType.HEARTBEAT_ACK):
            peer = self._peers.get(conn.peer_id or "")
            if peer:
                peer.last_heartbeat = time.time()
                peer.last_seen      = time.time()
            if mtype == MsgType.HEARTBEAT:
                conn.send(MsgType.HEARTBEAT_ACK, {"ts": time.time()})

        elif mtype == MsgType.PEER_LIST:
            self._handle_peer_list(payload)

        elif mtype == MsgType.GOSSIP:
            conn.send(MsgType.GOSSIP_ACK, {"received": True})
            if self.on_gossip:
                self.on_gossip(payload)

        elif mtype == MsgType.DISCONNECT:
            conn.close()

    def _handle_handshake(self, msg: Dict[str, Any], conn: PeerConnection) -> None:
        payload  = msg.get("payload", {})
        peer_id  = payload.get("node_id", str(uuid.uuid4())[:16])
        addr_str = f"{conn.address}:{payload.get('port', conn.port)}"

        conn.peer_id = peer_id
        conn.state   = PeerState.ACTIVE

        peer = PeerInfo(
            peer_id=peer_id,
            address=conn.address,
            port=payload.get("port", conn.port),
            state=PeerState.ACTIVE.value,
            kiswarm_version=payload.get("kiswarm_version", "?"),
            os_family=payload.get("os_family", "?"),
            connected_at=time.time(),
            last_heartbeat=time.time(),
            last_seen=time.time(),
            fixes_known=payload.get("fixes_known", 0),
            capabilities=payload.get("capabilities", []),
        )

        with self._lock:
            self._peers[peer_id] = peer
            self._conns[peer_id] = conn
            self._known_addrs.add(addr_str)

        conn.send(MsgType.HANDSHAKE_ACK, self._handshake_payload())
        # Share our peer list
        conn.send(MsgType.PEER_LIST, {"peers": self._peer_list_payload()})
        self._save_peers()
        logger.info(f"[SwarmPeer] Peer accepted: {peer_id} ({addr_str})")

    def _handle_handshake_ack(self, msg: Dict[str, Any], conn: PeerConnection) -> None:
        payload  = msg.get("payload", {})
        peer_id  = payload.get("node_id", str(uuid.uuid4())[:16])
        addr_str = f"{conn.address}:{payload.get('port', conn.port)}"

        conn.peer_id = peer_id
        conn.state   = PeerState.ACTIVE

        peer = PeerInfo(
            peer_id=peer_id,
            address=conn.address,
            port=payload.get("port", conn.port),
            state=PeerState.ACTIVE.value,
            kiswarm_version=payload.get("kiswarm_version", "?"),
            os_family=payload.get("os_family", "?"),
            connected_at=time.time(),
            last_heartbeat=time.time(),
            last_seen=time.time(),
            fixes_known=payload.get("fixes_known", 0),
            capabilities=payload.get("capabilities", []),
        )
        with self._lock:
            self._peers[peer_id] = peer
            self._conns[peer_id] = conn
            self._known_addrs.add(addr_str)

        self._save_peers()
        logger.info(f"[SwarmPeer] Handshake complete: {peer_id} ({addr_str})")

    def _handle_peer_list(self, payload: Dict[str, Any]) -> None:
        """Peer shared its peer list — try connecting to unknown ones."""
        peers = payload.get("peers", [])
        for p in peers:
            addr = p.get("address")
            port = p.get("port", PEER_PORT)
            if addr and f"{addr}:{port}" not in self._known_addrs:
                # Async connect attempt
                threading.Thread(
                    target=self.connect, args=(addr, port), daemon=True
                ).start()

    def _on_disconnect(self, conn: PeerConnection) -> None:
        peer_id = conn.peer_id
        if peer_id:
            with self._lock:
                self._peers.pop(peer_id, None)
                self._conns.pop(peer_id, None)
                addr_str = f"{conn.address}:{conn.port}"
                self._known_addrs.discard(addr_str)
            self._save_peers()
            logger.info(f"[SwarmPeer] Peer disconnected: {peer_id}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def _peer_list_payload(self) -> List[Dict[str, Any]]:
        return [{"address": p.address, "port": p.port}
                for p in self._peers.values() if p.is_alive()]

    def _save_peers(self) -> None:
        try:
            data = {
                "node_id":    self.node_id,
                "saved_at":   time.time(),
                "peers":      [p.to_dict() for p in self._peers.values()],
                "known_addrs": list(self._known_addrs),
            }
            with open(self._storage_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"[SwarmPeer] Save failed: {e}")

    def _load_peers(self) -> None:
        try:
            if os.path.exists(self._storage_file):
                with open(self._storage_file) as f:
                    data = json.load(f)
                self._known_addrs = set(data.get("known_addrs", []))
                logger.info(f"[SwarmPeer] Loaded {len(self._known_addrs)} known addresses")
        except Exception as e:
            logger.debug(f"[SwarmPeer] Load failed: {e}")

    def _count_known_fixes(self) -> int:
        try:
            fixes_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "experience", "known_fixes.json"
            )
            if os.path.exists(fixes_file):
                with open(fixes_file) as f:
                    return len(json.load(f).get("fixes", []))
        except Exception:
            pass
        return 0

    def reconnect_known_peers(self) -> int:
        """Try to reconnect to all previously known peers on startup."""
        connected = 0
        for addr_str in list(self._known_addrs):
            try:
                addr, port_str = addr_str.rsplit(":", 1)
                if self.connect(addr, int(port_str)):
                    connected += 1
            except Exception:
                pass
        return connected

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = [p for p in self._peers.values() if p.is_alive()]
        return {
            "node_id":       self.node_id,
            "port":          self.port,
            "running":       self._running,
            "active_peers":  len(active),
            "max_peers":     MAX_PEERS,
            "known_addrs":   len(self._known_addrs),
            "peers":         [p.to_dict() for p in active],
        }

    def list_peers(self) -> List[PeerInfo]:
        with self._lock:
            return list(self._peers.values())
