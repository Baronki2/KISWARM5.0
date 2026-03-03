"""Tests for KISWARM v4.8 — P2P Mesh Network"""
import json
import os
import sys
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────────────────────────────────────────────────────────────
# GOSSIP PROTOCOL TESTS (no network needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestGossipProtocol:
    @pytest.fixture
    def gossip(self, tmp_path):
        from python.sentinel.gossip_protocol import GossipProtocol
        return GossipProtocol(node_id="testnode01", storage_dir=str(tmp_path))

    def test_init(self, gossip):
        assert gossip.node_id == "testnode01"
        assert len(gossip._seen) == 0

    def test_create_gossip_item(self):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.FIX, "node01", {"fix_id": "FIX-TEST"})
        assert item.gossip_id
        assert item.signature
        assert len(item.signature) == 16
        assert item.ttl == 4
        assert item.gossip_type == "fix"

    def test_gossip_item_decrement(self):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.FIX, "node01", {"fix_id": "FIX-TEST"}, ttl=3)
        dec  = item.decrement()
        assert dec.ttl == 2
        assert dec.gossip_id == item.gossip_id  # Same item, lower TTL

    def test_gossip_item_should_forward(self):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.FIX, "n", {}, ttl=1)
        assert item.should_forward()
        dead = item.decrement()
        assert dead.ttl == 0
        assert not dead.should_forward()

    def test_gossip_item_expired(self):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.FIX, "n", {}, ttl=4)
        item.created_at = time.time() - 90000  # 25 hours ago
        assert item.is_expired()
        assert not item.should_forward()

    def test_receive_new_item(self, gossip):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.EXPERIENCE, "other_node", {
            "error_class": "ConnectionError",
            "error_message": "ollama refused",
            "module": "test",
            "os_family": "debian",
        })
        result = gossip.receive(item.to_dict())
        assert result is True
        assert item.signature in gossip._seen

    def test_receive_duplicate_rejected(self, gossip):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.EXPERIENCE, "other", {"error_message": "x"})
        gossip.receive(item.to_dict())
        result = gossip.receive(item.to_dict())  # Second time
        assert result is False

    def test_gossip_fix_marks_seen(self, gossip):
        item = gossip.gossip_fix({"fix_id": "FIX-MESH-001", "error_pattern": "test",
                                   "fix_commands": ["echo ok"], "description": "test"})
        assert item.signature in gossip._seen

    def test_gossip_experience(self, gossip):
        item = gossip.gossip_experience({
            "error_class": "ValueError",
            "error_message": "test error from mesh",
            "module": "test_mod",
            "os_family": "debian",
            "kiswarm_version": "4.8",
        })
        assert item.gossip_type == "experience"
        assert item.ttl == 3  # Experience has lower TTL

    def test_gossip_upgrade(self, gossip):
        item = gossip.gossip_upgrade("4.9", "New features")
        assert item.gossip_type == "upgrade"
        assert item.payload["version"] == "4.9"
        assert "upgrade_cmd" in item.payload

    def test_gossip_peer_info(self, gossip):
        item = gossip.gossip_peer_info("192.168.1.50", 11440)
        assert item.gossip_type == "peer_info"
        assert item.payload["address"] == "192.168.1.50"
        assert item.ttl == 2  # Peer info has lowest TTL

    def test_fix_applied_to_local_store(self, gossip, tmp_path):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        # Point fixes file to tmp
        fixes_file = tmp_path / "known_fixes.json"
        fixes_file.write_text(json.dumps({"fixes": [], "version": "1.0"}))
        gossip._fixes_file = str(fixes_file)

        fix_data = {
            "fix_id": "FIX-MESH-001",
            "error_pattern": "mesh.*test",
            "fix_commands": ["echo mesh"],
            "description": "Mesh test fix",
            "success_rate": 0.8,
            "created_at": "2026-03-01",
            "contributed_by": "community",
        }
        item = GossipItem.create(GossipType.FIX, "peer_node", fix_data)
        gossip.receive(item.to_dict())

        with open(fixes_file) as f:
            data = json.load(f)
        assert any(f["fix_id"] == "FIX-MESH-001" for f in data["fixes"])

    def test_duplicate_fix_not_applied_twice(self, gossip, tmp_path):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        fixes_file = tmp_path / "known_fixes.json"
        fix = {"fix_id": "FIX-DUP", "error_pattern": "x", "fix_commands": [],
                "description": "d", "success_rate": 0.5, "created_at": "2026-01-01",
                "contributed_by": "team"}
        fixes_file.write_text(json.dumps({"fixes": [fix]}))
        gossip._fixes_file = str(fixes_file)

        item = GossipItem.create(GossipType.FIX, "peer", fix)
        gossip.receive(item.to_dict())

        with open(fixes_file) as f:
            data = json.load(f)
        count = sum(1 for f in data["fixes"] if f["fix_id"] == "FIX-DUP")
        assert count == 1  # Not duplicated

    def test_seen_persisted_and_loaded(self, tmp_path):
        from python.sentinel.gossip_protocol import GossipProtocol, GossipItem, GossipType
        g1 = GossipProtocol(node_id="n1", storage_dir=str(tmp_path))
        item = GossipItem.create(GossipType.EXPERIENCE, "n2", {"msg": "test"})
        g1.receive(item.to_dict())
        assert item.signature in g1._seen

        # New instance loads persisted seen-set
        g2 = GossipProtocol(node_id="n1", storage_dir=str(tmp_path))
        assert item.signature in g2._seen
        # So duplicate is rejected
        assert g2.receive(item.to_dict()) is False

    def test_item_roundtrip(self):
        from python.sentinel.gossip_protocol import GossipItem, GossipType
        item = GossipItem.create(GossipType.FIX, "node", {"fix_id": "FIX-RT"})
        d    = item.to_dict()
        item2 = GossipItem.from_dict(d)
        assert item2.gossip_id == item.gossip_id
        assert item2.signature == item.signature
        assert item2.ttl == item.ttl

    def test_broadcaster_called(self, gossip):
        called_with = []
        gossip.set_broadcaster(lambda payload: called_with.append(payload) or 1)
        gossip.gossip_fix({"fix_id": "FIX-BC", "error_pattern": "x",
                           "fix_commands": [], "description": "d",
                           "success_rate": 0.5, "created_at": "2026-01-01",
                           "contributed_by": "team"})
        assert len(called_with) == 1
        assert called_with[0]["gossip_type"] == "fix"

    def test_on_new_fix_callback(self, tmp_path):
        from python.sentinel.gossip_protocol import GossipProtocol, GossipItem, GossipType
        received = []
        g = GossipProtocol(node_id="n", storage_dir=str(tmp_path),
                           on_new_fix=lambda f: received.append(f))
        fixes_file = tmp_path / "known_fixes.json"
        fixes_file.write_text(json.dumps({"fixes": []}))
        g._fixes_file = str(fixes_file)

        fix = {"fix_id": "FIX-CB", "error_pattern": "x", "fix_commands": [],
               "description": "d", "success_rate": 0.5, "created_at": "2026-01-01",
               "contributed_by": "team"}
        item = GossipItem.create(GossipType.FIX, "peer", fix)
        g.receive(item.to_dict())
        assert len(received) == 1
        assert received[0]["fix_id"] == "FIX-CB"

    def test_stats(self, gossip):
        s = gossip.stats()
        assert "node_id" in s
        assert "items_seen" in s
        assert "by_type" in s
        assert s["has_broadcaster"] is False

    def test_upgrade_callback(self, tmp_path):
        from python.sentinel.gossip_protocol import GossipProtocol, GossipItem, GossipType
        got_version = []
        g = GossipProtocol(node_id="n", storage_dir=str(tmp_path),
                           on_upgrade=lambda v: got_version.append(v))
        item = GossipItem.create(GossipType.UPGRADE, "peer",
                                  {"version": "4.9", "upgrade_cmd": "git pull", "changelog": ""})
        g.receive(item.to_dict())
        assert got_version == ["4.9"]


# ─────────────────────────────────────────────────────────────────────────────
# PEER DISCOVERY TESTS (no network needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestPeerDiscovery:
    @pytest.fixture
    def discovery(self, tmp_path, monkeypatch):
        import python.sentinel.peer_discovery as pd_mod
        monkeypatch.setattr(pd_mod, "DISCOVERY_FILE",
                            str(tmp_path / "discovered_peers.json"))
        from python.sentinel.peer_discovery import PeerDiscovery
        return PeerDiscovery(node_id="disc_node")

    def test_init(self, discovery):
        assert discovery.node_id == "disc_node"
        assert len(discovery._known) == 0

    def test_register_manual(self, discovery):
        result = discovery.register_manual("192.168.1.10", 11440)
        assert result is True
        assert "192.168.1.10:11440" in discovery._known

    def test_register_duplicate_rejected(self, discovery):
        discovery.register_manual("10.0.0.1", 11440)
        result = discovery.register_manual("10.0.0.1", 11440)
        assert result is False

    def test_register_calls_callback(self, discovery):
        found = []
        discovery.on_discovered = lambda a, p: found.append((a, p))
        discovery.register_manual("192.168.1.20", 11440)
        assert found == [("192.168.1.20", 11440)]

    def test_remove_peer(self, discovery):
        discovery.register_manual("10.0.0.5", 11440)
        result = discovery.remove_peer("10.0.0.5", 11440)
        assert result is True
        assert "10.0.0.5:11440" not in discovery._known

    def test_remove_nonexistent(self, discovery):
        result = discovery.remove_peer("99.99.99.99", 11440)
        assert result is False

    def test_gossip_peer_discovery(self, discovery):
        found = []
        discovery.on_discovered = lambda a, p: found.append((a, p))
        result = discovery.on_gossip_peer("172.16.0.5", 11440)
        assert result is True
        assert found == [("172.16.0.5", 11440)]

    def test_gossip_peer_duplicate(self, discovery):
        discovery.on_gossip_peer("172.16.0.5", 11440)
        result = discovery.on_gossip_peer("172.16.0.5", 11440)
        assert result is False

    def test_get_known_peers(self, discovery):
        discovery.register_manual("10.0.0.1", 11440)
        discovery.register_manual("10.0.0.2", 11440)
        peers = discovery.get_known_peers()
        addrs = {f"{a}:{p}" for a, p in peers}
        assert "10.0.0.1:11440" in addrs
        assert "10.0.0.2:11440" in addrs

    def test_persistence(self, tmp_path, monkeypatch):
        import python.sentinel.peer_discovery as pd_mod
        disc_file = str(tmp_path / "disc.json")
        monkeypatch.setattr(pd_mod, "DISCOVERY_FILE", disc_file)
        from python.sentinel.peer_discovery import PeerDiscovery

        d1 = PeerDiscovery(node_id="n1")
        d1.register_manual("10.10.10.1", 11440)
        d1.register_manual("10.10.10.2", 11440)

        d2 = PeerDiscovery(node_id="n1")
        assert "10.10.10.1:11440" in d2._known
        assert "10.10.10.2:11440" in d2._known

    def test_stats(self, discovery):
        discovery.register_manual("1.2.3.4")
        s = discovery.stats()
        assert s["known_peers"] == 1
        assert "local_ip" in s
        assert "subnet_scan" in s

    def test_detect_local_subnet(self, discovery):
        subnet = discovery._detect_local_subnet()
        # May be None if no network, but if present must be valid CIDR
        if subnet:
            import ipaddress
            ipaddress.ip_network(subnet, strict=False)  # Should not raise

    def test_get_local_ip(self, discovery):
        ip = discovery._get_local_ip()
        assert ip  # Should return something


# ─────────────────────────────────────────────────────────────────────────────
# SWARM PEER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmPeer:
    def test_init(self, tmp_path, monkeypatch):
        import python.sentinel.swarm_peer as sp_mod
        monkeypatch.setattr(sp_mod, "PEER_PORT", 19440)
        from python.sentinel.swarm_peer import SwarmPeer
        peer = SwarmPeer(node_id="test_peer", port=19440)
        assert peer.node_id == "test_peer"
        assert peer.port == 19440
        assert len(peer._peers) == 0

    def test_peer_info_dataclass(self):
        from python.sentinel.swarm_peer import PeerInfo, PeerState
        p = PeerInfo(
            peer_id="abc", address="10.0.0.1", port=11440,
            state=PeerState.ACTIVE.value,
            kiswarm_version="4.8", os_family="debian",
            connected_at=time.time(), last_heartbeat=time.time(),
            last_seen=time.time(), fixes_known=6,
            capabilities=["gossip", "sysadmin"],
        )
        assert p.is_alive()
        assert p.addr_str == "10.0.0.1:11440"
        d = p.to_dict()
        assert d["is_alive"] is True
        assert "age_s" in d

    def test_peer_info_dead(self):
        from python.sentinel.swarm_peer import PeerInfo, PeerState
        p = PeerInfo(
            peer_id="dead", address="10.0.0.2", port=11440,
            state=PeerState.DEGRADED.value,
            kiswarm_version="4.8", os_family="debian",
            connected_at=time.time() - 200,
            last_heartbeat=time.time() - 200,  # > PEER_TIMEOUT (90s)
            last_seen=time.time() - 200,
            fixes_known=0, capabilities=[],
        )
        assert not p.is_alive()

    def test_make_msg(self):
        from python.sentinel.swarm_peer import make_msg, MsgType, parse_msg
        raw = make_msg(MsgType.HEARTBEAT, {"ts": 12345.0}, "node01")
        assert raw.endswith(b"\n")
        parsed = parse_msg(raw)
        assert parsed["type"] == "heartbeat"
        assert parsed["node_id"] == "node01"
        assert parsed["payload"]["ts"] == 12345.0

    def test_parse_invalid_msg(self):
        from python.sentinel.swarm_peer import parse_msg
        assert parse_msg(b"not json\n") is None
        assert parse_msg(b"\n") is None

    def test_peer_info_roundtrip(self):
        from python.sentinel.swarm_peer import PeerInfo, PeerState
        p = PeerInfo(
            peer_id="rt01", address="1.2.3.4", port=11440,
            state=PeerState.ACTIVE.value, kiswarm_version="4.8",
            os_family="redhat", connected_at=1000.0,
            last_heartbeat=1000.0, last_seen=1000.0,
            fixes_known=3, capabilities=["gossip"],
        )
        d  = p.to_dict()
        # from_dict must strip is_alive and age_s
        p2 = PeerInfo.from_dict(d)
        assert p2.peer_id == "rt01"
        assert p2.os_family == "redhat"

    def test_max_peers_constant(self):
        from python.sentinel.swarm_peer import MAX_PEERS
        assert MAX_PEERS == 5  # The architecture contract

    def test_status_structure(self, tmp_path, monkeypatch):
        import python.sentinel.swarm_peer as sp_mod
        monkeypatch.setattr(sp_mod, "PEER_PORT", 19441)
        from python.sentinel.swarm_peer import SwarmPeer
        peer = SwarmPeer(node_id="status_test", port=19441)
        s = peer.status()
        assert s["node_id"] == "status_test"
        assert s["max_peers"] == 5
        assert s["active_peers"] == 0
        assert s["running"] is False

    def test_two_peers_connect(self):
        """Integration: two SwarmPeer instances connect via TCP."""
        from python.sentinel.swarm_peer import SwarmPeer
        import random

        port_a = random.randint(19500, 19600)
        port_b = random.randint(19601, 19700)

        gossip_received = []

        peer_a = SwarmPeer(node_id="nodeA", port=port_a,
                           on_gossip=lambda g: gossip_received.append(("A", g)))
        peer_b = SwarmPeer(node_id="nodeB", port=port_b,
                           on_gossip=lambda g: gossip_received.append(("B", g)))

        peer_a.start()
        peer_b.start()
        time.sleep(0.3)

        # A connects to B
        connected = peer_a.connect("127.0.0.1", port_b)
        time.sleep(1.0)  # Wait for handshake

        assert connected is True
        assert len(peer_a._peers) >= 1 or len(peer_b._peers) >= 1

        # Test broadcast
        sent = peer_a.broadcast_gossip({"type": "test", "msg": "hello from A"})

        peer_a.stop()
        peer_b.stop()


# ─────────────────────────────────────────────────────────────────────────────
# KISWARM CLI TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestKISWARMCli:
    def test_daemon_not_running_initially(self, tmp_path, monkeypatch):
        import python.sentinel.kiswarm_cli as cli_mod
        monkeypatch.setattr(cli_mod, "PID_FILE", str(tmp_path / "cli.pid"))
        assert cli_mod._daemon_running() is False

    def test_send_to_daemon_no_daemon(self, monkeypatch):
        import python.sentinel.kiswarm_cli as cli_mod
        monkeypatch.setattr(cli_mod, "CONTROL_PORT", 19999)
        result = cli_mod._send_to_daemon("status")
        assert result is None  # No daemon running

    def test_daemon_dispatch_status(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        daemon = KISWARMDaemon()
        result = daemon._dispatch({"action": "status"})
        assert "node_id" in result
        assert "running" in result

    def test_daemon_dispatch_unknown(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        daemon = KISWARMDaemon()
        result = daemon._dispatch({"action": "nonexistent"})
        assert "error" in result

    def test_daemon_dispatch_peer_list_empty(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        daemon = KISWARMDaemon()
        result = daemon._dispatch({"action": "peer_list"})
        assert result["peers"] == []

    def test_node_id_persistent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        d1 = KISWARMDaemon()
        nid1 = d1._node_id
        d2 = KISWARMDaemon()
        nid2 = d2._node_id
        assert nid1 == nid2  # Same node ID across restarts

    def test_node_id_format(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        d = KISWARMDaemon()
        assert len(d._node_id) == 16

    def test_dispatch_sync(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        daemon = KISWARMDaemon()
        result = daemon._dispatch({"action": "sync"})
        assert "synced_peers" in result
        assert result["synced_peers"] == 0  # No peers

    def test_dispatch_gossip_fix_no_fix(self):
        from python.sentinel.kiswarm_cli import KISWARMDaemon
        daemon = KISWARMDaemon()
        result = daemon._dispatch({"action": "gossip_fix", "fix": {}})
        assert "error" in result

    def test_cli_version(self):
        from python.sentinel.kiswarm_cli import CLI_VERSION
        assert CLI_VERSION == "4.8"

    def test_ports_defined(self):
        from python.sentinel.kiswarm_cli import DAEMON_PORT, CONTROL_PORT
        assert DAEMON_PORT == 11440
        assert CONTROL_PORT == 11441


# ─────────────────────────────────────────────────────────────────────────────
# DUAL-TRACK INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDualTrackIntegration:
    """Verify GitHub track + P2P track run independently and complement each other."""

    def test_github_track_offline_fallback(self):
        """GitHub track falls back to local fixes when offline."""
        from python.sentinel.feedback_channel import FeedbackChannel
        ch = FeedbackChannel(github_token=None)
        fixes = ch.load_known_fixes()
        assert len(fixes) >= 6  # Built-in fixes always available

    def test_p2p_track_no_internet_needed(self, tmp_path):
        """P2P track works completely without internet."""
        from python.sentinel.gossip_protocol import GossipProtocol, GossipItem, GossipType
        from python.sentinel.peer_discovery  import PeerDiscovery

        g = GossipProtocol(node_id="airgap_node", storage_dir=str(tmp_path))
        d = PeerDiscovery(node_id="airgap_node")

        # Create and process a fix — no internet needed
        fix = {"fix_id": "FIX-P2P-001", "error_pattern": "airgap.*test",
               "fix_commands": ["echo ok"], "description": "Air-gap test fix",
               "success_rate": 0.9, "created_at": "2026-03-01",
               "contributed_by": "community"}

        # Create from peer
        item = GossipItem.create(GossipType.FIX, "remote_peer", fix)

        # Process locally
        result = g.receive(item.to_dict())
        assert result is True  # Processed without any internet

    def test_fix_propagates_both_tracks(self, tmp_path):
        """A fix available on GitHub track should also be gossip-able via P2P."""
        from python.sentinel.feedback_channel import FeedbackChannel
        from python.sentinel.gossip_protocol  import GossipProtocol, GossipType

        g = GossipProtocol(node_id="dual_node", storage_dir=str(tmp_path))
        broadcasts = []
        g.set_broadcaster(lambda p: broadcasts.append(p) or 1)

        ch    = FeedbackChannel()
        fixes = ch.load_known_fixes()
        assert len(fixes) > 0

        # Take first fix and gossip it via P2P
        fix_dict = fixes[0].to_dict()
        item = g.gossip_fix(fix_dict)
        assert len(broadcasts) == 1
        assert broadcasts[0]["gossip_type"] == "fix"

    def test_experience_flows_both_tracks(self, tmp_path):
        """Experience captured locally can go to both GitHub and P2P."""
        from python.sentinel.experience_collector import ExperienceCollector
        from python.sentinel.gossip_protocol      import GossipProtocol
        from python.sentinel.feedback_channel     import FeedbackChannel

        collector = ExperienceCollector(storage_dir=str(tmp_path / "exp"))
        g         = GossipProtocol(node_id="exp_node", storage_dir=str(tmp_path / "gos"))
        broadcasts = []
        g.set_broadcaster(lambda p: broadcasts.append(p) or 1)

        # Capture error
        try:
            raise ConnectionError("ollama refused 11434")
        except ConnectionError as e:
            ev = collector.capture_error("test", e)

        # Gossip it via P2P
        item = g.gossip_experience(ev.to_dict())
        assert len(broadcasts) == 1

        # GitHub track
        ch     = FeedbackChannel()
        result = ch.report_experience([ev.to_dict()], collector._system_id)
        assert result["status"] in ("no_token", "ok", "reported", "disabled")

    def test_redundancy_one_track_down(self, tmp_path):
        """If GitHub is down, P2P still delivers fixes."""
        from python.sentinel.gossip_protocol import GossipProtocol, GossipItem, GossipType

        fixes_received = []
        g = GossipProtocol(node_id="resilient",
                           storage_dir=str(tmp_path),
                           on_new_fix=lambda f: fixes_received.append(f))
        fixes_file = tmp_path / "known_fixes.json"
        fixes_file.write_text(json.dumps({"fixes": []}))
        g._fixes_file = str(fixes_file)

        # Simulate GitHub being down (no FeedbackChannel call)
        # P2P delivers the fix directly
        fix = {"fix_id": "FIX-RESILIENCE", "error_pattern": "x",
               "fix_commands": ["echo resilient"], "description": "resilience test",
               "success_rate": 0.85, "created_at": "2026-03-01", "contributed_by": "peer"}
        item = GossipItem.create(GossipType.FIX, "healthy_peer", fix)
        g.receive(item.to_dict())

        assert len(fixes_received) == 1
        assert fixes_received[0]["fix_id"] == "FIX-RESILIENCE"

    def test_mesh_hop_count(self, tmp_path):
        """Verify TTL-based hop limiting works correctly."""
        from python.sentinel.gossip_protocol import GossipItem, GossipType

        item = GossipItem.create(GossipType.FIX, "origin", {}, ttl=4)
        assert item.should_forward()

        # Simulate 4 hops
        current = item
        for i in range(4):
            assert current.should_forward()
            current = current.decrement()

        assert current.ttl == 0
        assert not current.should_forward()
        # Max reach: 5^4 = 625 nodes covered
