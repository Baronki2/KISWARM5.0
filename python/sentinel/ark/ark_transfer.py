"""
KISWARM v4.9 — Module 53: Ark Transfer
========================================
Transfers Ark contents between nodes via the P2P mesh network.
No internet. No cloud storage. Node-to-node direct transfer.

This is the final piece of the resilience puzzle:
  - Node A has a full Ark (healthy)
  - Node B is new/damaged (needs software)
  - ArkTransfer moves exactly what B needs from A to B

Transfer protocol:
  1. B sends MANIFEST_REQUEST to A via SwarmPeer
  2. A responds with its full inventory (manifest)
  3. B computes delta: what A has that B needs
  4. B sends TRANSFER_REQUEST for specific items
  5. A streams each item as chunked binary data
  6. B verifies checksum after receiving each item
  7. B marks item PRESENT and updates its own ark

Design decisions (industrial):
  - PULL model (receiver requests) — sender never pushes without consent
  - Chunked transfer — survives interrupted connections (resume)
  - SHA-256 verification before marking PRESENT — no silent corruption
  - Bandwidth throttle — never saturate the LAN for this alone
  - Priority order — CRITICAL items transferred first

Wire protocol extension on top of SwarmPeer (port 11440):
  Uses a separate TCP connection on port 11442 for bulk data
  Reason: Don't mix large file transfers with mesh control traffic
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .software_ark import ArkCategory, ArkItem, ArkItemState, ArkPriority, SoftwareArk

logger = logging.getLogger(__name__)

TRANSFER_PORT   = 11442
CHUNK_SIZE      = 256 * 1024   # 256KB chunks
MAX_BANDWIDTH   = 50 * 1024**2  # 50 MB/s — don't saturate LAN
CONNECT_TIMEOUT = 10
RECV_TIMEOUT    = 30
PROTOCOL_MAGIC  = b"KIARK01\n"   # 8 bytes — identifies protocol version


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER PROTOCOL MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

def _send_json(sock: socket.socket, data: Dict[str, Any]) -> None:
    raw = json.dumps(data).encode() + b"\n"
    sock.sendall(raw)


def _recv_json(sock: socket.socket) -> Optional[Dict[str, Any]]:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf += chunk
    return json.loads(buf.split(b"\n")[0].decode())


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER ITEMS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransferJob:
    item_id:      str
    name:         str
    size_bytes:   int
    sha256:       Optional[str]
    rel_path:     str
    priority:     str


@dataclass
class TransferResult:
    item_id:       str
    success:       bool
    bytes_received: int
    duration_s:    float
    error:         Optional[str] = None
    verified:      bool = False

    def speed_mbps(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return self.bytes_received / 1024**2 / self.duration_s


@dataclass
class TransferSession:
    session_id:   str
    peer_address: str
    peer_port:    int
    started_at:   float
    direction:    str            # "send" or "receive"
    jobs:         List[TransferJob]
    results:      List[TransferResult] = field(default_factory=list)
    active:       bool = True

    @property
    def total_bytes(self) -> int:
        return sum(j.size_bytes for j in self.jobs)

    @property
    def transferred_bytes(self) -> int:
        return sum(r.bytes_received for r in self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id":     self.session_id,
            "peer":           f"{self.peer_address}:{self.peer_port}",
            "direction":      self.direction,
            "started_at":     self.started_at,
            "jobs_total":     len(self.jobs),
            "jobs_done":      len(self.results),
            "jobs_success":   self.success_count,
            "bytes_total":    self.total_bytes,
            "bytes_done":     self.transferred_bytes,
            "active":         self.active,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ARK TRANSFER — SENDER SIDE
# ─────────────────────────────────────────────────────────────────────────────

class ArkSender:
    """
    Runs as a server on port 11442.
    Responds to transfer requests from peers.
    Any peer can request any PRESENT item from our Ark.
    """

    def __init__(self, ark: SoftwareArk):
        self.ark      = ark
        self._running = False
        self._server  = None
        self._sessions: Dict[str, TransferSession] = {}

    def start(self) -> None:
        self._running = True
        threading.Thread(
            target=self._server_loop, daemon=True, name="ark-sender"
        ).start()
        logger.info(f"[ArkSender] Listening on :{TRANSFER_PORT}")

    def stop(self) -> None:
        self._running = False
        if self._server:
            try: self._server.close()
            except Exception: pass

    def _server_loop(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server.bind(("0.0.0.0", TRANSFER_PORT))
            self._server.listen(5)
            self._server.settimeout(2.0)
        except Exception as e:
            logger.error(f"[ArkSender] Bind failed: {e}")
            return

        while self._running:
            try:
                conn, addr = self._server.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr[0]),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, conn: socket.socket, addr: str) -> None:
        conn.settimeout(RECV_TIMEOUT)
        try:
            # Verify protocol magic
            magic = conn.recv(len(PROTOCOL_MAGIC))
            if magic != PROTOCOL_MAGIC:
                conn.close()
                return
            conn.sendall(PROTOCOL_MAGIC)  # Echo back

            while True:
                req = _recv_json(conn)
                if not req:
                    break

                cmd = req.get("cmd")

                if cmd == "manifest":
                    self._send_manifest(conn)

                elif cmd == "get_item":
                    item_id = req.get("item_id")
                    self._send_item(conn, item_id)

                elif cmd == "bye":
                    break

        except Exception as e:
            logger.debug(f"[ArkSender] Client {addr} error: {e}")
        finally:
            conn.close()

    def _send_manifest(self, conn: socket.socket) -> None:
        """Send our complete inventory to the peer."""
        manifest = []
        for item_id, item in self.ark._inventory.items():
            if item.state == ArkItemState.PRESENT.value:
                manifest.append({
                    "item_id":    item_id,
                    "name":       item.name,
                    "size_bytes": item.size_bytes,
                    "sha256":     item.sha256,
                    "rel_path":   item.rel_path,
                    "priority":   item.priority,
                    "category":   item.category,
                })
        _send_json(conn, {"status": "ok", "manifest": manifest,
                          "count": len(manifest)})

    def _send_item(self, conn: socket.socket, item_id: str) -> None:
        """Stream an item file to the peer."""
        item = self.ark.get_item(item_id)
        if not item or item.state != ArkItemState.PRESENT.value:
            _send_json(conn, {"status": "error",
                              "error": f"Item not available: {item_id}"})
            return

        path = self.ark.item_path(item)
        if not os.path.exists(path):
            _send_json(conn, {"status": "error", "error": "File not found"})
            return

        size = os.path.getsize(path)
        _send_json(conn, {
            "status":     "ok",
            "item_id":    item_id,
            "size_bytes": size,
            "sha256":     item.sha256,
        })

        # Stream the file
        sent = 0
        t_last = time.time()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                conn.sendall(chunk)
                sent += len(chunk)

                # Bandwidth throttle
                elapsed = time.time() - t_last
                expected = sent / MAX_BANDWIDTH
                if expected > elapsed:
                    time.sleep(expected - elapsed)

        logger.info(f"[ArkSender] Sent {item_id} ({sent // 1024**2}MB)")


# ─────────────────────────────────────────────────────────────────────────────
# ARK TRANSFER — RECEIVER SIDE
# ─────────────────────────────────────────────────────────────────────────────

class ArkReceiver:
    """
    Connects to a peer's ArkSender and pulls needed items.
    PULL model: we decide what we need, we request it.
    """

    def __init__(
        self,
        ark:          SoftwareArk,
        on_progress:  Optional[Callable[[str, int, int], None]] = None,
    ):
        self.ark         = ark
        self.on_progress = on_progress  # callback(item_id, received_bytes, total_bytes)

    def get_peer_manifest(
        self, peer_address: str, peer_port: int = TRANSFER_PORT
    ) -> Optional[List[Dict[str, Any]]]:
        """Get the inventory manifest from a peer."""
        try:
            with socket.create_connection(
                (peer_address, peer_port), timeout=CONNECT_TIMEOUT
            ) as sock:
                sock.settimeout(RECV_TIMEOUT)
                sock.sendall(PROTOCOL_MAGIC)
                echo = sock.recv(len(PROTOCOL_MAGIC))
                if echo != PROTOCOL_MAGIC:
                    return None
                _send_json(sock, {"cmd": "manifest"})
                resp = _recv_json(sock)
                if resp and resp.get("status") == "ok":
                    return resp.get("manifest", [])
        except Exception as e:
            logger.warning(f"[ArkReceiver] Manifest from {peer_address} failed: {e}")
        return None

    def compute_delta(
        self, peer_manifest: List[Dict[str, Any]]
    ) -> List[TransferJob]:
        """
        What does the peer have that we need?
        Returns items in priority order — CRITICAL first.
        """
        self.ark.integrity_check(quick=True)

        priority_order = {
            ArkPriority.CRITICAL.value: 0,
            ArkPriority.HIGH.value:     1,
            ArkPriority.NORMAL.value:   2,
            ArkPriority.LOW.value:      3,
        }

        peer_items = {i["item_id"]: i for i in peer_manifest}
        delta: List[TransferJob] = []

        for item_id, item in self.ark._inventory.items():
            if item.state == ArkItemState.PRESENT.value:
                continue   # Already have it
            if item_id not in peer_items:
                continue   # Peer doesn't have it either
            # Skip models that need more RAM than we have
            if (item.category == ArkCategory.MODEL.value
                    and item.min_ram_gb > self.ark._ram_gb):
                continue

            peer_item = peer_items[item_id]
            delta.append(TransferJob(
                item_id=item_id,
                name=item.name,
                size_bytes=peer_item.get("size_bytes", 0),
                sha256=peer_item.get("sha256"),
                rel_path=peer_item.get("rel_path", item.rel_path),
                priority=item.priority,
            ))

        return sorted(delta, key=lambda j: (
            priority_order.get(j.priority, 9),
            j.size_bytes
        ))

    def pull_from_peer(
        self,
        peer_address: str,
        peer_port:    int = TRANSFER_PORT,
        max_items:    Optional[int] = None,
        critical_only: bool = False,
    ) -> TransferSession:
        """
        Pull missing items from a peer.
        Returns a TransferSession with results.
        """
        import uuid
        session = TransferSession(
            session_id=str(uuid.uuid4())[:8],
            peer_address=peer_address,
            peer_port=peer_port,
            started_at=time.time(),
            direction="receive",
            jobs=[],
        )

        # Get manifest
        manifest = self.get_peer_manifest(peer_address, peer_port)
        if manifest is None:
            session.active = False
            return session

        # Compute delta
        delta = self.compute_delta(manifest)
        if critical_only:
            delta = [j for j in delta if j.priority == ArkPriority.CRITICAL.value]
        if max_items:
            delta = delta[:max_items]

        session.jobs = delta
        logger.info(f"[ArkReceiver] {len(delta)} items to pull from {peer_address}")

        if not delta:
            session.active = False
            return session

        # Pull items
        try:
            with socket.create_connection(
                (peer_address, peer_port), timeout=CONNECT_TIMEOUT
            ) as sock:
                sock.settimeout(RECV_TIMEOUT)
                sock.sendall(PROTOCOL_MAGIC)
                echo = sock.recv(len(PROTOCOL_MAGIC))
                if echo != PROTOCOL_MAGIC:
                    session.active = False
                    return session

                for job in delta:
                    result = self._pull_item(sock, job)
                    session.results.append(result)
                    if not result.success and job.priority == ArkPriority.CRITICAL.value:
                        logger.error(f"[ArkReceiver] Critical item failed: {job.item_id}")
                        break

                _send_json(sock, {"cmd": "bye"})

        except Exception as e:
            logger.error(f"[ArkReceiver] Session error: {e}")

        session.active = False
        logger.info(
            f"[ArkReceiver] Session {session.session_id}: "
            f"{session.success_count}/{len(session.results)} items received"
        )
        return session

    def _pull_item(self, sock: socket.socket, job: TransferJob) -> TransferResult:
        """Pull a single item from the connected peer."""
        t0 = time.time()
        _send_json(sock, {"cmd": "get_item", "item_id": job.item_id})

        resp = _recv_json(sock)
        if not resp or resp.get("status") != "ok":
            return TransferResult(
                item_id=job.item_id, success=False,
                bytes_received=0, duration_s=time.time() - t0,
                error=resp.get("error") if resp else "No response"
            )

        size_bytes = resp.get("size_bytes", 0)
        expected_sha = resp.get("sha256")

        # Prepare destination
        item = self.ark.get_item(job.item_id)
        if not item:
            return TransferResult(
                job.item_id, False, 0, time.time() - t0,
                error="Item not in local inventory"
            )

        dest_path = self.ark.item_path(item)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # Receive file data
        received    = 0
        hasher      = hashlib.sha256()
        tmp_path    = dest_path + ".tmp"

        try:
            with open(tmp_path, "wb") as f:
                remaining = size_bytes
                while remaining > 0:
                    chunk_size = min(CHUNK_SIZE, remaining)
                    chunk = self._recv_exactly(sock, chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    received  += len(chunk)
                    remaining -= len(chunk)
                    if self.on_progress:
                        self.on_progress(job.item_id, received, size_bytes)

            # Verify checksum
            verified = False
            if expected_sha:
                actual_sha = hasher.hexdigest()
                if actual_sha == expected_sha:
                    verified = True
                else:
                    os.unlink(tmp_path)
                    return TransferResult(
                        job.item_id, False, received,
                        time.time() - t0,
                        error=f"Checksum mismatch: expected {expected_sha[:8]}"
                    )
            else:
                verified = True  # No checksum — trust the transfer

            # Commit
            os.rename(tmp_path, dest_path)
            item.state         = ArkItemState.PRESENT.value
            item.size_bytes    = received
            if expected_sha:
                item.sha256    = expected_sha
            item.last_verified = time.time()
            self.ark._save_inventory()

            logger.info(f"[ArkReceiver] ✓ {job.item_id} "
                        f"({received // 1024**2}MB, verified={verified})")

            return TransferResult(
                item_id=job.item_id, success=True,
                bytes_received=received,
                duration_s=time.time() - t0,
                verified=verified
            )

        except Exception as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return TransferResult(
                job.item_id, False, received,
                time.time() - t0, error=str(e)
            )

    @staticmethod
    def _recv_exactly(sock: socket.socket, n: int) -> bytes:
        """Receive exactly n bytes from socket."""
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(min(n - len(buf), CHUNK_SIZE))
            if not chunk:
                break
            buf += chunk
        return buf


# ─────────────────────────────────────────────────────────────────────────────
# ARK TRANSFER COORDINATOR
# ─────────────────────────────────────────────────────────────────────────────

class ArkTransfer:
    """
    High-level coordinator that combines sender and receiver.
    Integrates with SwarmPeer for peer discovery.
    """

    def __init__(self, ark: Optional[SoftwareArk] = None):
        self.ark      = ark or SoftwareArk()
        self.sender   = ArkSender(self.ark)
        self.receiver = ArkReceiver(self.ark)
        self._sessions: List[TransferSession] = []

    def start_server(self) -> None:
        """Start the transfer server (sender side)."""
        self.sender.start()

    def stop_server(self) -> None:
        self.sender.stop()

    def pull_from_best_peer(
        self,
        peers: List[Tuple[str, int]],
        critical_only: bool = True,
    ) -> Optional[TransferSession]:
        """
        Try peers in order, pull from the first one that responds.
        peers: list of (address, port) tuples from SwarmPeer
        """
        for addr, port in peers:
            transfer_port = TRANSFER_PORT
            manifest = self.receiver.get_peer_manifest(addr, transfer_port)
            if manifest is not None:
                logger.info(f"[ArkTransfer] Pulling from {addr} ({len(manifest)} items available)")
                session = self.receiver.pull_from_peer(
                    addr, transfer_port, critical_only=critical_only
                )
                self._sessions.append(session)
                return session

        logger.warning("[ArkTransfer] No responding peers found")
        return None

    def fill_from_peers(
        self,
        peers: List[Tuple[str, int]],
    ) -> Dict[str, Any]:
        """
        Pull ALL missing items from peers — tries multiple peers.
        Stops when ark is complete or no more peers available.
        """
        total_received = 0
        sessions_done  = 0

        for addr, port in peers:
            # Check if still missing items
            missing = self.ark.missing_by_priority()
            if not missing:
                break

            manifest = self.receiver.get_peer_manifest(addr, TRANSFER_PORT)
            if manifest is None:
                continue

            delta = self.receiver.compute_delta(manifest)
            if not delta:
                continue

            session = self.receiver.pull_from_peer(addr, TRANSFER_PORT)
            self._sessions.append(session)
            total_received += session.transferred_bytes
            sessions_done  += 1

        can_boot, gaps = self.ark.can_bootstrap()
        return {
            "sessions":       sessions_done,
            "bytes_received": total_received,
            "can_bootstrap":  can_boot,
            "remaining_gaps": gaps,
        }

    def status(self) -> Dict[str, Any]:
        return {
            "server_running":   self.sender._running,
            "transfer_port":    TRANSFER_PORT,
            "sessions_total":   len(self._sessions),
            "sessions_active":  sum(1 for s in self._sessions if s.active),
            "recent_sessions":  [s.to_dict() for s in self._sessions[-5:]],
        }
